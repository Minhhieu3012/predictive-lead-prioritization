
"""PLPT-16: Queue API."""

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from src.rules.rules_engine import build_queue, RULES_LABEL

app = FastAPI(
    title="Predictive Lead Prioritization API",
    version="1.0.0",
)


def load_enquiries():
    """Load cleaned enquiries from the project SQLite database."""

    project_root = Path(__file__).resolve().parents[2]

    db_path = Path(
        os.getenv(
            "DATABASE_PATH",
            str(project_root / "data" / "songnhat.db"),
        )
    )

    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found: {db_path}"
        )

    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row

    try:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM clean_enquiries")
        rows = cursor.fetchall()

        return [dict(row) for row in rows]

    finally:
        connection.close()


@app.get("/api/v1/queue")
def get_queue(
    limit: int = Query(default=100, ge=1, le=1000),
):
    """
    Return deterministic Rules Baseline priority queue.

    Results are ordered by:
    Tier 0 -> RulesScore descending -> FIFO.
    """

    try:
        enquiries = load_enquiries()
        queue = build_queue(enquiries)

        return {
            "rules_label": RULES_LABEL,
            "score_unit": "points",
            "total_enquiries": len(queue),
            "returned_count": min(limit, len(queue)),
            "queue": queue[:limit],
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        )

    except sqlite3.Error as error:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {error}",
        )
