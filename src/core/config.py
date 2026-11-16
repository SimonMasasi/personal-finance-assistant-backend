from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Required. The app must not boot with a guessed database or a guessed
    # signing key — a default here would silently sign tokens with a value
    # every deployment shares.
    DATABASE_URL: str
    SECRET_KEY: str

    # Comma-separated, and required for the same reason as SECRET_KEY: an empty
    # audience means accepting an ID token minted for somebody else's app.
    #
    # THIS IS THE **WEB** CLIENT ID, NOT THE ANDROID ONE. The Android OAuth
    # client (bound to package name + SHA-1) authorises the sign-in request but
    # never appears in the token; @react-native-google-signin is given the web
    # client id as `webClientId` and Google mints the ID token with that in
    # `aud`. Putting the Android id here makes every sign-in fail with a
    # deliberately generic 401 that reads like a signature problem.
    GOOGLE_CLIENT_IDS: str

    APP_NAME: str = "Personal Finance Assistant"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = True

    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "personal-finance-assistant-backend"

    # Short-lived on purpose. The access token is a bearer credential with no
    # revocation list behind it, so its blast radius is bounded by its lifetime.
    JWT_ACCESS_TOKEN_EXPIRE_SECONDS: int = 15 * 60

    # Long-lived, because the phone should not ask for a password every day.
    # Safe to be long only because refresh tokens rotate and are stored hashed.
    REFRESH_TOKEN_EXPIRE_SECONDS: int = 60 * 60 * 24 * 30

    # A sync batch is a phone's backlog since its last successful sync. 1000
    # rows covers months of transactions; past that the client should page
    # rather than send one enormous request that risks a gateway timeout
    # mid-transaction.
    SYNC_MAX_BATCH: int = 1000

    # How many rows one pull returns. The client drains the rest by syncing
    # again with the returned cursor, which is cheap and keeps each response
    # small enough for a mobile connection.
    SYNC_MAX_PULL: int = 500

    # Hard cap on the request body, enforced before JSON parsing. A 1000-row
    # batch is roughly 300 KB; 2 MB leaves generous headroom while still
    # refusing a body large enough to be a memory-exhaustion attempt.
    MAX_REQUEST_BYTES: int = 2 * 1024 * 1024

    # Google's signing keys rotate; cache them rather than fetching per request.
    GOOGLE_JWKS_CACHE_SECONDS: int = 3600
    # Deliberately shorter than PyJWT's 30s default, so a Google outage fails
    # fast instead of holding a worker thread open for half a minute.
    GOOGLE_JWKS_TIMEOUT_SECONDS: float = 5.0
    # Phone clocks drift, and an ID token is only valid for an hour.
    GOOGLE_ID_TOKEN_LEEWAY_SECONDS: int = 60

    # Rate limits for the auth endpoints, in slowapi's notation.
    RATE_LIMIT_GOOGLE: str = "10/minute"
    RATE_LIMIT_REFRESH: str = "30/minute"

    CORS_ALLOW_ORIGINS: str = "*"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


SETTINGS = Settings()
