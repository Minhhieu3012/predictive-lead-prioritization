"""Environment isolation and storage configuration."""

import os
from pathlib import Path

VALID_ENV_MODES = {"development", "evaluation", "production"}


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_env_mode() -> str:
    mode = os.getenv("ENV_MODE", "development").strip().lower()
    if mode not in VALID_ENV_MODES:
        raise ValueError(
            f"ENV_MODE không hợp lệ: {mode}. Chỉ chấp nhận {sorted(VALID_ENV_MODES)}."
        )
    return mode


def get_database_path() -> Path:
    mode = get_env_mode()
    configured = os.getenv("DATABASE_PATH", "").strip()
    if configured:
        return Path(configured)

    root = get_project_root()
    if mode == "development":
        return root / "data" / "songnhat.db"
    if mode == "evaluation":
        return root / "data" / "evaluation" / "songnhat.db"
    return root / "data" / "production" / "songnhat.db"


def get_quarantine_dir() -> Path:
    mode = get_env_mode()
    configured = os.getenv("QUARANTINE_DIR", "").strip()
    if configured:
        return Path(configured)

    root = get_project_root()
    if mode == "development":
        return root / "data" / "quarantine"
    if mode == "evaluation":
        return root / "data" / "evaluation" / "quarantine"
    return root / "data" / "production" / "quarantine"


def validate_environment_paths() -> dict[str, str]:
    mode = get_env_mode()
    database = get_database_path()
    quarantine = get_quarantine_dir()

    if mode == "evaluation" and "production" in str(database).lower():
        raise RuntimeError("ENV_MODE=evaluation không được trỏ DATABASE_PATH tới production.")
    if mode == "evaluation" and "production" in str(quarantine).lower():
        raise RuntimeError("ENV_MODE=evaluation không được trỏ QUARANTINE_DIR tới production.")
    if mode == "development" and "production" in str(database).lower():
        raise RuntimeError("ENV_MODE=development không được trỏ DATABASE_PATH tới production.")
    if mode == "production":
        from src.security.auth import get_session_secret_key

        get_session_secret_key()

    return {
        "env_mode": mode,
        "database_path": str(database),
        "quarantine_dir": str(quarantine),
    }
