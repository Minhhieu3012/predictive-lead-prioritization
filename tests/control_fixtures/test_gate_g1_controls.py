"""
SongNhat AI-02 · Predictive Lead Prioritization (PLPT)
PLPT-27 (US-13) / PLPT-47: Automated 40 Control Fixtures Test Suite (Gate G1)

Kiểm chứng tự động 100% (40/40 passed) cho 7 miền nghiệp vụ:
- Domain A: Decision-Time (t0) Cutoff & Leakage Prevention (CF-01 to CF-06)
- Domain B: Identity Resolution, Deduplication & Ingestion Integrity (CF-07 to CF-13)
- Domain C: 60-Day Outcome Maturity & Label Integrity (CF-14 to CF-18)
- Domain D: Missing Data, New Accounts, Score Meaning & Fallback (CF-19 to CF-24)
- Domain E: Queue Policy, Capacity K, Exploration, Escalation & Overrides (CF-25 to CF-30)
- Domain F: Emergency Block, 24h Freshness Hold, Security & Backup Replay (CF-31 to CF-36)
- Domain G: Vietnamese Localization & Performance SLA (CF-37 to CF-40)

Lệnh thực thi chuẩn:
    py -m pytest tests/control_fixtures/test_gate_g1_controls.py -v --junitxml=artifacts/gate_g1/junit_cf40.xml
"""

from pathlib import Path
import pytest

from tests.control_fixtures.fixtures_data import (
    FIXTURES_CATALOG,
    execute_fixture_verification,
    generate_gate_g1_report,
)

FIXTURE_IDS = [f"CF-{i:02d}" for i in range(1, 41)]


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_gate_g1_control_fixture(fixture_id: str):
    """
    Kiểm chứng xác định từng kịch bản kiểm soát trong 40 Control Fixtures.
    Yêu cầu tỷ lệ đạt 100% (Zero-Failure Gate G1 Requirement).
    """
    res = execute_fixture_verification(fixture_id)
    assert res["passed"] is True, (
        f"Control Fixture {fixture_id} ({res.get('name')}) THẤT BẠI: {res.get('details')}"
    )


def test_gate_g1_audit_report_generation(tmp_path):
    """
    Kiểm chứng xuất báo cáo nghiệm thu Gate G1 đạt chuẩn 100% (40/40 passed)
    và tuân thủ schema JSON bắt buộc.
    """
    report_file = tmp_path / "control_fixtures_report.json"
    rep = generate_gate_g1_report(str(report_file))

    assert rep["suite_name"] == "PLPT_GATE_G1_40_CONTROL_FIXTURES"
    assert rep["total_fixtures"] == 40
    assert rep["passed_count"] == 40
    assert rep["failed_count"] == 0
    assert rep["pass_rate_pct"] == 100.0
    assert rep["gate_g1_controls_status"] == "PASSED"
    assert len(rep["fixtures"]) == 40
    assert report_file.exists()


# Hook sau khi chạy toàn bộ test suite để tự động ghi artifacts/gate_g1/control_fixtures_report.json
@pytest.fixture(scope="session", autouse=True)
def export_gate_g1_artifact_on_completion():
    yield
    # Xuất artifact nghiệm thu vào thư mục artifacts/gate_g1/
    artifact_path = Path("artifacts/gate_g1/control_fixtures_report.json")
    generate_gate_g1_report(str(artifact_path))
