"""Build a point-in-time feature store using only data available by each t0."""

from datetime import date, datetime, timedelta, timezone
import math
import os
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence

VN_TZ = timezone(timedelta(hours=7))
FEATURE_VERSION = "pre_t0_v1"
MAX_FEATURE_COLUMNS = 30

FEATURE_COLUMNS = (
    "product_family",
    "spec_completeness",
    "is_urgent_replacement",
    "contract_days_to_due_at_t0",
    "industry_at_t0",
    "enquiry_channel_at_t0",
    "region_at_t0",
    "estimated_value_band_at_t0",
    "account_age_days_at_t0",
    "prior_purchase_count_at_t0",
    "prior_enquiry_count",
    "prior_distinct_product_family_count",
    "account_history_status",
)

SOURCE_COLUMNS = (
    "enquiry_id",
    "account_key",
    "t0_time",
    "product_family",
    "spec_completeness",
    "is_urgent_replacement",
    "contract_due_date",
    "industry_at_t0",
    "enquiry_channel_at_t0",
    "region_at_t0",
    "account_age_days_at_t0",
    "prior_purchase_count_at_t0",
    "estimated_value_band_at_t0",
    "feature_event_time",
    "feature_available_at",
)

if len(FEATURE_COLUMNS) > MAX_FEATURE_COLUMNS:
    raise RuntimeError("Pre-t0 feature schema exceeds the 30-column limit")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=VN_TZ)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def _safe_optional_nonnegative_int(value: Any) -> tuple[int | None, bool]:
    if value is None or str(value).strip() == "":
        return None, True
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None, False
    if not math.isfinite(numeric) or not numeric.is_integer() or numeric < 0:
        return None, False
    return int(numeric), True


def _normalize_urgent(value: Any) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"urgent", "true", "yes", "1", "critical", "emergency"}:
        return True
    if normalized in {"normal", "false", "no", "0"}:
        return False
    return None


def _validate_point_in_time(
    row: Mapping[str, Any],
) -> tuple[datetime | None, str | None]:
    t0 = _parse_datetime(row.get("t0_time"))
    if t0 is None:
        return None, "INVALID_T0"

    feature_event_time = _parse_datetime(row.get("feature_event_time"))
    if feature_event_time is None:
        return t0, "INVALID_FEATURE_EVENT_TIME"
    if feature_event_time > t0:
        return t0, "FEATURE_EVENT_AFTER_T0"

    feature_available_at = _parse_datetime(row.get("feature_available_at"))
    if feature_available_at is None:
        return t0, "INVALID_FEATURE_AVAILABLE_AT"
    if feature_available_at > t0:
        return t0, "FEATURE_AVAILABLE_AFTER_T0"

    return t0, None


def build_pre_t0_records(
    enquiries: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return sanitized feature records and identifier-only lineage records."""
    normalized_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for source_value in enquiries:
        source = dict(source_value)
        enquiry_id = str(source.get("enquiry_id") or "").strip()
        if not enquiry_id:
            raise ValueError("Every source enquiry must have an enquiry_id")
        if enquiry_id in seen_ids:
            raise ValueError(f"Duplicate enquiry_id in clean_enquiries: {enquiry_id}")
        seen_ids.add(enquiry_id)

        t0, rejection_code = _validate_point_in_time(source)
        account_age, account_age_valid = _safe_optional_nonnegative_int(
            source.get("account_age_days_at_t0")
        )
        purchase_count, purchase_count_valid = _safe_optional_nonnegative_int(
            source.get("prior_purchase_count_at_t0")
        )
        if rejection_code is None and not account_age_valid:
            rejection_code = "INVALID_ACCOUNT_AGE"
        if rejection_code is None and not purchase_count_valid:
            rejection_code = "INVALID_PRIOR_PURCHASE_COUNT"

        normalized_rows.append(
            {
                "_enquiry_id": enquiry_id,
                "_account_key": str(source.get("account_key") or "").strip(),
                "_t0": t0,
                "_rejection_code": rejection_code,
                "_account_age_days": account_age,
                "_prior_purchase_count": purchase_count,
                "_feature_event_time": _parse_datetime(
                    source.get("feature_event_time")
                ),
                "_feature_available_at": _parse_datetime(
                    source.get("feature_available_at")
                ),
                "_source": source,
            }
        )

    feature_records: list[dict[str, Any]] = []
    lineage_records: list[dict[str, Any]] = []
    for current in normalized_rows:
        source = current["_source"]
        t0 = current["_t0"]
        rejection_code = current["_rejection_code"]
        prior_rows: list[dict[str, Any]] = []

        if rejection_code is None and current["_account_key"] and t0 is not None:
            prior_rows = [
                prior
                for prior in normalized_rows
                if prior["_rejection_code"] is None
                and prior["_account_key"] == current["_account_key"]
                and prior["_account_key"]
                and prior["_t0"] is not None
                and prior["_t0"] < t0
                and prior["_feature_event_time"] is not None
                and prior["_feature_event_time"] <= t0
                and prior["_feature_available_at"] is not None
                and prior["_feature_available_at"] <= t0
            ]

        if rejection_code is None:
            due_date = _parse_date(source.get("contract_due_date"))
            t0_date = t0.astimezone(VN_TZ).date() if t0 is not None else None
            due_delta = (
                (due_date - t0_date).days
                if due_date is not None and t0_date is not None
                else None
            )
            account_key_present = bool(current["_account_key"])
            if not account_key_present:
                history_status = "UNKNOWN"
            elif prior_rows:
                history_status = "HISTORY_FOUND"
            else:
                history_status = "NO_PRIOR_ENQUIRY_OBSERVED"

            feature_record = {
                "enquiry_id": current["_enquiry_id"],
                "product_family": source.get("product_family"),
                "spec_completeness": source.get("spec_completeness"),
                "is_urgent_replacement": _normalize_urgent(
                    source.get("is_urgent_replacement")
                ),
                "contract_days_to_due_at_t0": due_delta,
                "industry_at_t0": source.get("industry_at_t0"),
                "enquiry_channel_at_t0": source.get("enquiry_channel_at_t0"),
                "region_at_t0": source.get("region_at_t0"),
                "estimated_value_band_at_t0": source.get(
                    "estimated_value_band_at_t0"
                ),
                "account_age_days_at_t0": current["_account_age_days"],
                "prior_purchase_count_at_t0": current["_prior_purchase_count"],
                "prior_enquiry_count": len(prior_rows) if prior_rows else None,
                "prior_distinct_product_family_count": (
                    len(
                        {
                            str(prior["_source"].get("product_family")).strip()
                            for prior in prior_rows
                            if str(
                                prior["_source"].get("product_family") or ""
                            ).strip()
                        }
                    )
                    if prior_rows
                    else None
                ),
                "account_history_status": history_status,
            }
            if set(feature_record) != {"enquiry_id", *FEATURE_COLUMNS}:
                raise RuntimeError("Generated feature record does not match schema")
            feature_records.append(feature_record)

        lineage_records.append(
            {
                "enquiry_id": current["_enquiry_id"],
                "t0_time": (
                    t0.astimezone(VN_TZ).isoformat(timespec="seconds")
                    if t0 is not None
                    else None
                ),
                "feature_event_time": (
                    current["_feature_event_time"].astimezone(VN_TZ).isoformat(
                        timespec="seconds"
                    )
                    if current["_feature_event_time"] is not None
                    else None
                ),
                "feature_available_at": (
                    current["_feature_available_at"].astimezone(VN_TZ).isoformat(
                        timespec="seconds"
                    )
                    if current["_feature_available_at"] is not None
                    else None
                ),
                "feature_status": "READY" if rejection_code is None else "REJECTED",
                "rejection_code": rejection_code,
            }
        )

    return feature_records, lineage_records


def _default_db_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return Path(
        os.getenv("DATABASE_PATH", str(project_root / "data" / "songnhat.db"))
    )


def _ensure_store_tables(conn: sqlite3.Connection) -> None:
    feature_definitions = {
        "product_family": "TEXT",
        "spec_completeness": "TEXT",
        "is_urgent_replacement": "INTEGER",
        "contract_days_to_due_at_t0": "INTEGER",
        "industry_at_t0": "TEXT",
        "enquiry_channel_at_t0": "TEXT",
        "region_at_t0": "TEXT",
        "estimated_value_band_at_t0": "TEXT",
        "account_age_days_at_t0": "INTEGER",
        "prior_purchase_count_at_t0": "INTEGER",
        "prior_enquiry_count": "INTEGER",
        "prior_distinct_product_family_count": "INTEGER",
        "account_history_status": "TEXT NOT NULL",
    }
    columns_sql = ",\n".join(
        f'"{column}" {feature_definitions[column]}' for column in FEATURE_COLUMNS
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS pre_t0_features (
            enquiry_id TEXT PRIMARY KEY,
            feature_version TEXT NOT NULL,
            {columns_sql},
            built_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pre_t0_feature_lineage (
            enquiry_id TEXT PRIMARY KEY,
            t0_time TEXT,
            feature_event_time TEXT,
            feature_available_at TEXT,
            feature_status TEXT NOT NULL,
            rejection_code TEXT,
            feature_version TEXT NOT NULL,
            built_at TEXT NOT NULL
        )
        """
    )


def build_pre_t0_store(
    db_path: str | Path | None = None,
) -> dict[str, int | str]:
    """Refresh sanitized feature and lineage tables from clean_enquiries."""
    target_db = Path(db_path) if db_path is not None else _default_db_path()
    if not target_db.exists():
        raise FileNotFoundError(f"Database not found: {target_db}")

    source_uri = f"{target_db.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        table_exists = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'clean_enquiries'
            """
        ).fetchone()
        if table_exists is None:
            raise ValueError(
                "clean_enquiries is required; run the validated ingestion and "
                "deduplication pipeline first"
            )

        available_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(clean_enquiries)")
        }
        missing_columns = set(SOURCE_COLUMNS) - available_columns
        if missing_columns:
            raise ValueError(
                "clean_enquiries is missing required columns: "
                + ", ".join(sorted(missing_columns))
            )
        selected_columns = ", ".join(f'"{column}"' for column in SOURCE_COLUMNS)
        enquiries = [
            dict(row)
            for row in conn.execute(
                f"SELECT {selected_columns} FROM clean_enquiries"
            )
        ]

    features, lineage = build_pre_t0_records(enquiries)
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    placeholders = ", ".join("?" for _ in range(len(FEATURE_COLUMNS) + 3))
    feature_columns_sql = ", ".join(f'"{column}"' for column in FEATURE_COLUMNS)
    feature_insert_sql = (
        "INSERT INTO pre_t0_features "
        f"(enquiry_id, feature_version, {feature_columns_sql}, built_at) "
        f"VALUES ({placeholders})"
    )
    lineage_insert_sql = """
        INSERT INTO pre_t0_feature_lineage (
            enquiry_id, t0_time, feature_event_time, feature_available_at,
            feature_status, rejection_code, feature_version, built_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """

    with sqlite3.connect(str(target_db), timeout=30) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            _ensure_store_tables(conn)
            conn.execute("DELETE FROM pre_t0_features")
            conn.execute("DELETE FROM pre_t0_feature_lineage")
            conn.executemany(
                feature_insert_sql,
                [
                    (
                        record["enquiry_id"],
                        FEATURE_VERSION,
                        *(record[column] for column in FEATURE_COLUMNS),
                        built_at,
                    )
                    for record in features
                ],
            )
            conn.executemany(
                lineage_insert_sql,
                [
                    (
                        record["enquiry_id"],
                        record["t0_time"],
                        record["feature_event_time"],
                        record["feature_available_at"],
                        record["feature_status"],
                        record["rejection_code"],
                        FEATURE_VERSION,
                        built_at,
                    )
                    for record in lineage
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    rejected_count = sum(
        record["feature_status"] == "REJECTED" for record in lineage
    )
    return {
        "status": "SUCCESS",
        "feature_version": FEATURE_VERSION,
        "feature_count": len(features),
        "rejected_count": rejected_count,
        "feature_columns": len(FEATURE_COLUMNS),
    }


if __name__ == "__main__":
    result = build_pre_t0_store()
    print(
        f"[PRE-T0] {result['status']}: "
        f"features={result['feature_count']}, "
        f"rejected={result['rejected_count']}, "
        f"columns={result['feature_columns']}"
    )
