"""App settings, read from environment / .env."""
import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _int_env(name, default):
    """Read an integer env var with a fallback."""
    return int(os.getenv(name, default))


class Config:
    """Default config; values come from .env."""

    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "rvm.db"))
    MACHINE_KEY = os.getenv("MACHINE_KEY", "")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")  # empty = /admin disabled
    # Comma separated list of known machine IDs, e.g. "RVM-01,RVM-02"
    MACHINE_IDS = [m.strip() for m in os.getenv("MACHINE_IDS", "RVM-01").split(",") if m.strip()]
    PRICE_BOTTLE = _int_env("PRICE_BOTTLE", 1)
    PRICE_CAN = _int_env("PRICE_CAN", 2)
    SESSION_TIMEOUT_S = _int_env("SESSION_TIMEOUT_S", 300)
    MAX_ITEMS_PER_SESSION = _int_env("MAX_ITEMS_PER_SESSION", 20)
    PAYOUT_MODE = os.getenv("PAYOUT_MODE", "mock")
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = _int_env("PORT", 5001)  # 5000 is taken by macOS AirPlay
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
