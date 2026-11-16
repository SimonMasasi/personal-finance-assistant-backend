import logging

from src.core.config import SETTINGS

logger = logging.getLogger(__name__)


def log_startup_banner() -> None:
    # Never log DATABASE_URL — it carries the password.
    logger.info(
        "%s starting on %s:%s (debug=%s)",
        SETTINGS.APP_NAME,
        SETTINGS.APP_HOST,
        SETTINGS.APP_PORT,
        SETTINGS.DEBUG,
    )
