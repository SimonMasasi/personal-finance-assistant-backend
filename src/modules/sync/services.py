import uuid as uuid_lib

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import SETTINGS
from src.modules.sync.dtos import SyncRequest, SyncResponse, TransactionDTO
from src.modules.sync.models import SYNC_SEQ, Transaction
from src.shared.errors import PayloadTooLargeError, ValidationError
from src.shared.time import (
    EpochRangeError,
    dt_to_ms,
    dt_to_ms_optional,
    ms_to_dt,
    ms_to_dt_optional,
    now_ms,
)

# Columns the client owns. On conflict these are all overwritten by the incoming
# row — the loser's content is replaced wholesale, never merged field by field,
# because a half-old half-new row is a state neither device ever had.
_CLIENT_COLUMNS = (
    "provider",
    "direction",
    "amount_minor",
    "balance_after_minor",
    "currency",
    "counterparty",
    "reference",
    "occurred_at",
    "category",
    "confidence",
    "parser_version",
    "source",
    "is_user_edited",
    "created_at",
    "updated_at",
    "deleted_at",
)


class SyncService:
    # ── conversion ────────────────────────────────────────────────────────

    @staticmethod
    def _to_row(user_id: uuid_lib.UUID, dto: TransactionDTO) -> dict[str, object]:
        """DTO -> insertable row. This is the only place ms becomes datetime."""
        return {
            "id": uuid_lib.uuid4(),
            "user_id": user_id,
            "uuid": dto.uuid,
            "sync_seq": SYNC_SEQ.next_value(),
            "provider": dto.provider,
            "direction": dto.direction,
            "amount_minor": dto.amount_minor,
            "balance_after_minor": dto.balance_after_minor,
            "currency": dto.currency,
            "counterparty": dto.counterparty,
            "reference": dto.reference,
            "occurred_at": ms_to_dt(dto.occurred_at),
            "category": dto.category,
            "confidence": float(dto.confidence),
            "parser_version": dto.parser_version,
            "source": dto.source,
            "is_user_edited": dto.is_user_edited,
            "created_at": ms_to_dt(dto.created_at),
            "updated_at": ms_to_dt(dto.updated_at),
            "deleted_at": ms_to_dt_optional(dto.deleted_at),
        }

    @staticmethod
    def _to_dto(row: Transaction) -> TransactionDTO:
        return TransactionDTO(
            uuid=row.uuid,
            provider=row.provider,
            direction=row.direction,
            amount_minor=row.amount_minor,
            balance_after_minor=row.balance_after_minor,
            currency=row.currency,
            counterparty=row.counterparty,
            reference=row.reference,
            occurred_at=dt_to_ms(row.occurred_at),
            category=row.category,
            confidence=row.confidence,
            parser_version=row.parser_version,
            source=row.source,
            is_user_edited=row.is_user_edited,
            created_at=dt_to_ms(row.created_at),
            updated_at=dt_to_ms(row.updated_at),
            deleted_at=dt_to_ms_optional(row.deleted_at),
        )

    # ── cursor ────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_cursor(cursor: str) -> int:
        raw = (cursor or "0").strip()
        if raw == "":
            return 0
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValidationError("cursor must be a non-negative integer string.") from exc
        if value < 0:
            raise ValidationError("cursor must be a non-negative integer string.")
        return value

    @staticmethod
    def _dedupe(changes: list[TransactionDTO]) -> list[TransactionDTO]:
        """Collapse repeated uuids within one batch, keeping the newest.

        Postgres refuses an ON CONFLICT DO UPDATE that would touch the same row
        twice in one statement ("cannot affect row a second time"), so a client
        that queued two edits to one row between syncs would otherwise get a
        500. Keeping the highest `updated_at` matches the conflict rule the rest
        of the endpoint applies.
        """
        newest: dict[str, TransactionDTO] = {}
        for dto in changes:
            current = newest.get(dto.uuid)
            if current is None or dto.updated_at > current.updated_at:
                newest[dto.uuid] = dto
        return list(newest.values())

    # ── the endpoint ──────────────────────────────────────────────────────

    async def sync(
        self, session: AsyncSession, user_id: uuid_lib.UUID, payload: SyncRequest
    ) -> SyncResponse:
        if len(payload.changes) > SETTINGS.SYNC_MAX_BATCH:
            raise PayloadTooLargeError(
                f"A sync batch may contain at most {SETTINGS.SYNC_MAX_BATCH} rows."
            )

        cursor = self._parse_cursor(payload.cursor)
        changes = self._dedupe(payload.changes)

        # Push and pull share one transaction. Without that, a concurrent sync
        # from the user's other device could commit between the two halves and
        # its rows would be assigned a sync_seq below the cursor we are about to
        # hand back — invisible to this client forever.
        applied_uuids = await self._push(session, user_id, changes)
        rows, has_more = await self._pull(session, user_id, cursor, applied_uuids)
        new_cursor = await self._next_cursor(session, user_id, cursor, rows, has_more)
        await session.commit()

        return SyncResponse(
            cursor=str(new_cursor),
            changes=[self._to_dto(row) for row in rows],
            server_time=now_ms(),
            has_more=has_more,
        )

    async def _push(
        self, session: AsyncSession, user_id: uuid_lib.UUID, changes: list[TransactionDTO]
    ) -> set[str]:
        """Upsert the client's rows. Returns the uuids that actually changed."""
        if not changes:
            return set()

        try:
            values = [self._to_row(user_id, dto) for dto in changes]
        except EpochRangeError as exc:
            raise ValidationError(str(exc)) from exc

        stmt = pg_insert(Transaction).values(values)

        # The whole conflict policy, in one clause.
        #
        #   - no server counterpart      -> plain insert
        #   - incoming updated_at newer  -> update
        #   - incoming updated_at older
        #     or equal                   -> WHERE fails, row untouched, no error
        #
        # That last line is why a replayed batch is a no-op (idempotent retry),
        # why a stale write is silently ignored rather than a 409, and why an
        # older non-deleted copy cannot resurrect a tombstoned row: a delete is
        # just an update that set `deleted_at`, so it wins on the same
        # comparison every other update does.
        #
        # There is deliberately no special case for `is_user_edited`. A user
        # edit always bumps `updated_at` on the client, so a hand-corrected row
        # already beats a stale machine-parsed one; a special case would only
        # reorder writes the timestamps already order correctly.
        upsert = stmt.on_conflict_do_update(
            constraint="uq_transactions_user_uuid",
            set_={
                **{col: getattr(stmt.excluded, col) for col in _CLIENT_COLUMNS},
                # Bumped on every update, not just inserts — a row the client
                # cannot see a new sync_seq for is a row it never pulls.
                "sync_seq": SYNC_SEQ.next_value(),
            },
            where=Transaction.updated_at < stmt.excluded.updated_at,
        ).returning(Transaction.uuid)

        result = await session.execute(upsert)
        return set(result.scalars().all())

    async def _pull(
        self,
        session: AsyncSession,
        user_id: uuid_lib.UUID,
        cursor: int,
        applied_uuids: set[str],
    ) -> tuple[list[Transaction], bool]:
        query = select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.sync_seq > cursor,
        )

        # Exclude only the uuids the upsert *actually applied*, not everything
        # the client sent. A row whose push lost the last-write-wins comparison
        # is deliberately left in: the client's copy is stale, so it must
        # receive the winning version. Excluding the whole submitted batch
        # instead would leave the two sides permanently disagreeing.
        if applied_uuids:
            query = query.where(Transaction.uuid.not_in(applied_uuids))

        # One extra row is the cheapest way to learn whether more are waiting.
        query = query.order_by(Transaction.sync_seq).limit(SETTINGS.SYNC_MAX_PULL + 1)

        rows = list((await session.execute(query)).scalars().all())
        has_more = len(rows) > SETTINGS.SYNC_MAX_PULL
        return rows[: SETTINGS.SYNC_MAX_PULL], has_more

    async def _next_cursor(
        self,
        session: AsyncSession,
        user_id: uuid_lib.UUID,
        cursor: int,
        rows: list[Transaction],
        has_more: bool,
    ) -> int:
        if has_more:
            # Only advance as far as the client was actually served. Jumping to
            # the table's max here would skip every row past the page limit.
            #
            # Note the client's own just-pushed rows sit above this point (they
            # were assigned the newest sequence values), so a paged sync hands
            # them back on a later round trip. They are byte-identical to what
            # the client already has and its own last-write-wins makes them
            # no-ops, so this costs bandwidth, not correctness.
            return rows[-1].sync_seq

        # Nothing left to pull, so the client is level with the whole table.
        # This is what makes "push 3 rows, pull with the returned cursor, get 0
        # back" hold: the pushed rows are at or below this maximum.
        highest = (
            await session.execute(
                select(func.max(Transaction.sync_seq)).where(Transaction.user_id == user_id)
            )
        ).scalar_one_or_none()

        return max(cursor, highest or 0)
