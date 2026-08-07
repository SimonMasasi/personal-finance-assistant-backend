from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Required. The app must not boot with a guessed database or a guessed
    # signing key — a default here would silently sign tokens with a value
    # every deployment shares.
    DATABASE_URL: str
    SECRET_KEY: str

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

    # Rate limits for the auth endpoints, in slowapi's notation.
    RATE_LIMIT_LOGIN: str = "10/minute"
    RATE_LIMIT_REGISTER: str = "5/minute"
    RATE_LIMIT_REFRESH: str = "30/minute"

    CORS_ALLOW_ORIGINS: str = "*"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


SETTINGS = Settings()
