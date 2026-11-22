"""PLPT-26 / PLPT-56 / CF-06: Shadow Batch Scoring Runner Isolated from outcome_events."""

import contextlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd

from src.features.pre_t0_store import build_pre_t0_features, guard_no_outcome_join
from src.models.train_candidates import RegularizedLogisticRegressionModel, TabularPreprocessor

VN_TZ = timezone(timedelta(hours=7))
SHADOW_SCORING_MODE = "SHADOW_ONLY"
DEFAULT_LOCKED_SERVING_MODE = "RULES_BASELINE"


def run_shadow_batch(
    db_path: str | Path,
    manifest_path: str | Path,
    model: RegularizedLogisticRegressionModel,
    preprocessor: TabularPreprocessor,
    *,
    requested_tables: list[str] | None = None,
    persist_logs: bool = False,
) -> pd.DataFrame:
    """
    Chạy chấm điểm ngầm (Shadow Scoring) tách biệt hoàn toàn khỏi hàng đợi phục vụ chính:
    - CHỈ được phép đọc `clean_enquiries` + `pre_t0_store` + `manifest.json`.
    - Tuyệt đối KHÔNG truy vấn `outcome_events` hoặc `raw_outcomes` (nâng LeakageError nếu cố tình).
    - Không thay đổi thứ tự hàng đợi đang hiển thị cho Sales (`is_visible_to_staff = False`).
    """
    if requested_tables:
        for tbl in requested_tables:
            if "outcome" in tbl.lower():
                guard_no_outcome_join(["label"])

    m_path = Path(manifest_path)
    if not m_path.exists():
        raise FileNotFoundError(f"Manifest not found: {m_path}")
    manifest = json.loads(m_path.read_text(encoding="utf-8"))

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            df = pd.read_sql_query(
                "SELECT * FROM clean_enquiries WHERE COALESCE(LOWER(TRIM(workflow_state)), '') NOT IN ('closed', 'withdrawn')",
                conn,
            )

    empty_cols = [
        "enquiry_id",
        "shadow_score",
        "model_score",
        "model_version",
        "scoring_mode",
        "serving_mode_locked",
        "is_visible_to_staff",
        "scored_at",
    ]
    if df.empty:
        return pd.DataFrame(columns=empty_cols)

    guard_no_outcome_join(list(df.columns))
    feat_df, _ = build_pre_t0_features(df, strict_leakage=True)
    x_mat = preprocessor.transform(feat_df)
    scores = model.predict_scores(x_mat)
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
    locked_mode = str(manifest.get("serving_mode_locked", DEFAULT_LOCKED_SERVING_MODE))

    out = pd.DataFrame(
        {
            "enquiry_id": feat_df["enquiry_id"].astype(str),
            "shadow_score": scores,
            "model_score": scores,
            "model_version": str(manifest.get("model_version", "unknown")),
            "scoring_mode": SHADOW_SCORING_MODE,
            "serving_mode_locked": locked_mode,
            "is_visible_to_staff": False,
            "scored_at": now_iso,
        }
    )

    if persist_logs:
        persist_shadow_scores(db_path, out)

    return out


def ensure_shadow_score_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_score_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enquiry_id TEXT NOT NULL,
            shadow_score REAL NOT NULL,
            model_version TEXT NOT NULL,
            scoring_mode TEXT NOT NULL DEFAULT 'SHADOW_ONLY',
            serving_mode_locked TEXT NOT NULL DEFAULT 'RULES_BASELINE',
            is_visible_to_staff INTEGER NOT NULL DEFAULT 0,
            scored_at TEXT NOT NULL
        )
        """
    )


def persist_shadow_scores(db_path: str | Path, shadow_df: pd.DataFrame) -> int:
    """Lưu điểm chạy ngầm vào bảng `shadow_score_logs` phục vụ giám sát hậu kiểm."""
    if shadow_df.empty:
        return 0

    rows: list[tuple[Any, ...]] = [
        (
            str(r["enquiry_id"]),
            float(r["shadow_score"]),
            str(r["model_version"]),
            str(r.get("scoring_mode", SHADOW_SCORING_MODE)),
            str(r.get("serving_mode_locked", DEFAULT_LOCKED_SERVING_MODE)),
            0,
            str(r["scored_at"]),
        )
        for _, r in shadow_df.iterrows()
    ]
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_shadow_score_table(conn)
            conn.executemany(
                """
                INSERT INTO shadow_score_logs (
                    enquiry_id, shadow_score, model_version, scoring_mode,
                    serving_mode_locked, is_visible_to_staff, scored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
    return len(rows)


# Alias hỗ trợ tên hàm rõ nghĩa shadow scoring
score_shadow_batch = run_shadow_batch
