from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

# A real Argon2id hash of a value nobody knows, used to burn the same CPU on a
# login for an email that does not exist as one that does. Without it, "no such
# user" returns in microseconds while a real user costs ~50ms, and the endpoint
# becomes an account enumerator.
_DUMMY_HASH = _hasher.hash("a-password-that-is-never-a-real-one")

MIN_PASSWORD_LENGTH = 8


class PasswordManager:
    @staticmethod
    def hash_password(password: str) -> str:
        return _hasher.hash(password)

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    @staticmethod
    def waste_time() -> None:
        """Verify against a dummy hash so a missing user costs what a real one does."""
        PasswordManager.verify_password("wrong", _DUMMY_HASH)

    @staticmethod
    def is_strong_password(password: str) -> tuple[bool, str]:
        if len(password) < MIN_PASSWORD_LENGTH:
            return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        if not any(c.isdigit() for c in password):
            return False, "Password must contain a digit."
        if not any(c.isalpha() for c in password):
            return False, "Password must contain a letter."
        return True, "Password is strong."
