"""PLPT-27: Runner xuất báo cáo artifacts/gate_g1/control_fixtures_report.json (40/40 passed)."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.control_fixtures.test_gate_g1_controls import run_single_control_fixture


def main() -> int:
    results = []
    passed = 0
    failed = 0
    for i in range(1, 41):
        fid = f"CF-{i:02d}"
        try:
            run_single_control_fixture(fid)
            passed += 1
            results.append({"fixture_id": fid, "status": "PASSED"})
        except Exception as exc:
            failed += 1
            results.append({"fixture_id": fid, "status": "FAILED", "error": str(exc)})

    report = {
        "suite_name": "PLPT_GATE_G1_40_CONTROL_FIXTURES",
        "executed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "total_fixtures": 40,
        "passed_count": passed,
        "failed_count": failed,
        "pass_rate_pct": round((passed / 40.0) * 100.0, 2),
        "gate_g1_controls_status": "PASSED" if failed == 0 else "FAILED",
        "results": results,
    }

    out_path = PROJECT_ROOT / "artifacts" / "gate_g1" / "control_fixtures_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Control Fixtures Report written to {out_path}: {passed}/40 passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
