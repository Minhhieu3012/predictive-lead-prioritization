"""PLPT-26 / PLPT-56 / PLPT-64 / CF-06: Shadow Batch Scoring Runner Isolated from outcome_events."""

import contextlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any
import uuid

import numpy as np
import pandas as pd

from src.features.pre_t0_store import build_pre_t0_features, guard_no_outcome_join
from src.models.train_candidates import RegularizedLogisticRegressionModel, TabularPreprocessor
from src.rules.rules_engine import RULES_LABEL, build_queue

VN_TZ = timezone(timedelta(hours=7))
SHADOW_SCORING_MODE = "SHADOW_ONLY"
DEFAULT_LOCKED_SERVING_MODE = "RULES_BASELINE"
SLA_MAX_SECONDS = 900.0  # 15 phút SLA cho tối đa 5.000 bản ghi (OPS 02)


def ensure_shadow_score_table(conn: sqlite3.Connection) -> None:
    """Tạo bảng lưu vết chấm điểm ngầm (shadow_scoring_logs & alias shadow_score_logs)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_scoring_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enquiry_id TEXT NOT NULL,
            shadow_score REAL NOT NULL,
            model_score REAL NOT NULL,
            shadow_rank INTEGER NOT NULL DEFAULT 0,
            model_version TEXT NOT NULL,
            scoring_mode TEXT NOT NULL DEFAULT 'SHADOW_ONLY',
            serving_mode_locked TEXT NOT NULL DEFAULT 'RULES_BASELINE',
            is_visible_to_staff INTEGER NOT NULL DEFAULT 0,
            scored_at TEXT NOT NULL
        )
        """
    )
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


def ensure_batch_run_table(conn: sqlite3.Connection) -> None:
    """Tạo bảng ghi thời lượng và trạng thái chạy batch (batch_run_logs) theo SLA OPS 02."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS batch_run_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            batch_type TEXT NOT NULL DEFAULT 'DAILY_SHADOW_REFRESH',
            status TEXT NOT NULL,
            records_processed INTEGER NOT NULL,
            duration_seconds REAL NOT NULL,
            sla_threshold_seconds REAL NOT NULL DEFAULT 900.0,
            sla_met INTEGER NOT NULL,
            model_version TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL
        )
        """
    )


def log_batch_run(
    db_path: str | Path,
    *,
    batch_id: str,
    batch_type: str = "DAILY_SHADOW_REFRESH",
    status: str = "SUCCESS",
    records_processed: int = 0,
    duration_seconds: float = 0.0,
    sla_threshold_seconds: float = SLA_MAX_SECONDS,
    model_version: str | None = None,
    error_message: str | None = None,
) -> None:
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
    sla_met = int(duration_seconds <= sla_threshold_seconds and status == "SUCCESS")

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_batch_run_table(conn)
            conn.execute(
                """
                INSERT INTO batch_run_logs (
                    batch_id, batch_type, status, records_processed,
                    duration_seconds, sla_threshold_seconds, sla_met,
                    model_version, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    batch_type,
                    status,
                    records_processed,
                    round(duration_seconds, 4),
                    sla_threshold_seconds,
                    sla_met,
                    model_version,
                    error_message,
                    now_iso,
                ),
            )
            conn.commit()


def get_latest_batch_run(db_path: str | Path, batch_type: str = "DAILY_SHADOW_REFRESH") -> dict[str, Any] | None:
    p = Path(db_path)
    if not p.exists():
        return None
    with contextlib.closing(sqlite3.connect(str(p))) as conn:
        ensure_batch_run_table(conn)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM batch_run_logs WHERE batch_type = ? ORDER BY id DESC LIMIT 1",
            (batch_type,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def persist_shadow_scores(db_path: str | Path, shadow_df: pd.DataFrame) -> int:
    """Lưu điểm chạy ngầm vào cả `shadow_scoring_logs` và `shadow_score_logs` phục vụ giám sát."""
    if shadow_df.empty:
        return 0

    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_shadow_score_table(conn)

            rows_full = []
            rows_legacy = []
            for _, r in shadow_df.iterrows():
                eid = str(r["enquiry_id"])
                s_score = float(r["shadow_score"])
                m_score = float(r.get("model_score", s_score))
                s_rank = int(r.get("shadow_rank", 0))
                m_ver = str(r["model_version"])
                sc_mode = str(r.get("scoring_mode", SHADOW_SCORING_MODE))
                serv_locked = str(r.get("serving_mode_locked", DEFAULT_LOCKED_SERVING_MODE))
                sc_at = str(r.get("scored_at", now_iso))

                rows_full.append(
                    (eid, s_score, m_score, s_rank, m_ver, sc_mode, serv_locked, 0, sc_at)
                )
                rows_legacy.append(
                    (eid, s_score, m_ver, sc_mode, serv_locked, 0, sc_at)
                )

            conn.executemany(
                """
                INSERT INTO shadow_scoring_logs (
                    enquiry_id, shadow_score, model_score, shadow_rank, model_version,
                    scoring_mode, serving_mode_locked, is_visible_to_staff, scored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows_full,
            )
            conn.executemany(
                """
                INSERT INTO shadow_score_logs (
                    enquiry_id, shadow_score, model_version, scoring_mode,
                    serving_mode_locked, is_visible_to_staff, scored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows_legacy,
            )
            conn.commit()

    return len(rows_full)


def run_shadow_batch(
    db_path: str | Path,
    manifest_path: str | Path,
    model: RegularizedLogisticRegressionModel,
    preprocessor: TabularPreprocessor,
    *,
    requested_tables: list[str] | None = None,
    persist_logs: bool = False,
    batch_id: str | None = None,
) -> pd.DataFrame:
    """
    PLPT-64 / MOD 08 / OPS 02:
    Chạy chấm điểm ngầm (Shadow Scoring) tách biệt hoàn toàn khỏi hàng đợi phục vụ chính:
    - CHỈ được phép đọc `clean_enquiries` + `pre_t0_store` + `manifest.json`.
    - Tuyệt đối KHÔNG truy vấn `outcome_events` hoặc `raw_outcomes` (nâng LeakageError nếu cố tình).
    - Tính điểm shadow_score và thứ hạng shadow_rank không hiển thị cho Sales (is_visible_to_staff = False).
    - Đo thời lượng xử lý và ghi vết vào batch_run_logs, đảm bảo tuân thủ SLA <= 15 phút / 5.000 dòng.
    - Xử lý atomic / partial failure: Nếu có lỗi, ghi status FAILED_PARTIAL vào batch_run_logs.
    """
    bid = batch_id or f"batch-{uuid.uuid4().hex[:8]}"
    start_time = time.perf_counter()

    if requested_tables:
        for tbl in requested_tables:
            if "outcome" in tbl.lower():
                guard_no_outcome_join(["label"])

    m_path = Path(manifest_path)
    if not m_path.exists():
        raise FileNotFoundError(f"Manifest not found: {m_path}")
    manifest = json.loads(m_path.read_text(encoding="utf-8"))
    model_version = str(manifest.get("model_version", "unknown"))

    try:
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
            "shadow_rank",
            "model_version",
            "scoring_mode",
            "serving_mode_locked",
            "is_visible_to_staff",
            "scored_at",
        ]
        if df.empty:
            duration = time.perf_counter() - start_time
            if persist_logs:
                log_batch_run(
                    db_path,
                    batch_id=bid,
                    status="SUCCESS",
                    records_processed=0,
                    duration_seconds=duration,
                    model_version=model_version,
                )
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
                "model_version": model_version,
                "scoring_mode": SHADOW_SCORING_MODE,
                "serving_mode_locked": locked_mode,
                "is_visible_to_staff": False,
                "scored_at": now_iso,
            }
        )

        # Tính thứ hạng ngầm shadow_rank (1 là điểm cao nhất)
        out = out.sort_values(by=["shadow_score", "enquiry_id"], ascending=[False, True]).reset_index(drop=True)
        out["shadow_rank"] = np.arange(1, len(out) + 1)

        if persist_logs:
            persist_shadow_scores(db_path, out)

        duration = time.perf_counter() - start_time
        if persist_logs:
            log_batch_run(
                db_path,
                batch_id=bid,
                status="SUCCESS",
                records_processed=len(out),
                duration_seconds=duration,
                model_version=model_version,
            )

        return out

    except Exception as exc:
        duration = time.perf_counter() - start_time
        if persist_logs:
            try:
                log_batch_run(
                    db_path,
                    batch_id=bid,
                    status="FAILED_PARTIAL",
                    records_processed=0,
                    duration_seconds=duration,
                    model_version=model_version,
                    error_message=str(exc),
                )
            except Exception:
                pass
        raise


def compute_shadow_rules_overlap(
    db_path: str | Path,
    k: int = 25,
    shadow_df: pd.DataFrame | None = None,
) -> float:
    """
    Tính tỷ lệ trùng lặp Top-K (overlap rate) giữa thứ tự Rules Baseline và thứ tự Shadow Model:
    overlap = len(set(Top_K_rules) & set(Top_K_shadow)) / min(k, len(total_eligible)).
    """
    p = Path(db_path)
    if not p.exists():
        return 0.0

    with contextlib.closing(sqlite3.connect(str(p))) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM clean_enquiries WHERE COALESCE(LOWER(TRIM(workflow_state)), '') NOT IN ('closed', 'withdrawn')"
        )
        enqs = [dict(r) for r in cur.fetchall()]

    if not enqs:
        return 0.0

    rules_queue = build_queue(enqs, exclude_closed=True)
    top_rules_ids = {str(x["enquiry_id"]) for x in rules_queue[:k]}

    if shadow_df is not None and not shadow_df.empty:
        s_sorted = shadow_df.sort_values(by="shadow_rank", ascending=True)
        top_shadow_ids = {str(x) for x in s_sorted["enquiry_id"].head(k)}
    else:
        with contextlib.closing(sqlite3.connect(str(p))) as conn:
            ensure_shadow_score_table(conn)
            cur = conn.execute(
                """
                SELECT enquiry_id FROM shadow_scoring_logs
                ORDER BY id DESC LIMIT 5000
                """
            )
            rows = [r[0] for r in cur.fetchall()]
        if not rows:
            return 0.0
        top_shadow_ids = set(rows[:k])

    denom = min(k, len(enqs))
    if denom == 0:
        return 0.0
    return round(len(top_rules_ids & top_shadow_ids) / denom, 4)


def get_shadow_summary(
    db_path: str | Path,
    manifest_path: str | Path | None = None,
    k: int = 25,
) -> dict[str, Any]:
    """
    Tổng hợp tóm tắt trạng thái chấm điểm ngầm theo yêu cầu Spec 09:
    GET /api/v1/shadow/summary (RBAC: MODEL_REVIEWER, ADMINISTRATOR).
    """
    p = Path(db_path)
    latest_batch = get_latest_batch_run(p)
    duration_sec = float(latest_batch["duration_seconds"]) if latest_batch else None
    sla_met = bool(latest_batch["sla_met"]) if latest_batch else True
    records_proc = int(latest_batch["records_processed"]) if latest_batch else 0

    m_ver = "unknown"
    appr_status = "SHADOW_ONLY"
    serv_mode = DEFAULT_LOCKED_SERVING_MODE

    if manifest_path and Path(manifest_path).exists():
        try:
            m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            m_ver = str(m.get("model_version", m_ver))
            appr_status = str(m.get("approval_status", appr_status))
            serv_mode = str(m.get("serving_mode_locked", serv_mode))
        except Exception:
            pass

    overlap = compute_shadow_rules_overlap(p, k=k)

    # Đếm số bản ghi trong shadow logs
    s_count = 0
    if p.exists():
        with contextlib.closing(sqlite3.connect(str(p))) as conn:
            ensure_shadow_score_table(conn)
            cur = conn.execute("SELECT COUNT(*) FROM shadow_scoring_logs")
            s_count = cur.fetchone()[0]

    return {
        "active_serving_mode": serv_mode,
        "fallback_reason": None if serv_mode != "FALLBACK_RULES_BASELINE" else "GATE_G1_NOT_APPROVED",
        "latest_batch_duration_sec": duration_sec,
        "sla_threshold_seconds": SLA_MAX_SECONDS,
        "sla_met": sla_met,
        "records_processed": records_proc,
        "top_k_overlap_rate": overlap,
        "registered_model_version": m_ver,
        "shadow_record_count": s_count,
        "approval_status": appr_status,
    }


# Alias hỗ trợ tên hàm rõ nghĩa shadow scoring
score_shadow_batch = run_shadow_batch

