"""
Unit Tests for Task PLPT-15 (US-07: Identity Resolution & Multi-Channel Deduplication Engine)

Kiểm chứng các tiêu chí nghiệm thu (Acceptance Criteria):
1. [BE/Data] Xây dựng thuật toán hợp nhất định danh đa kênh (account_key + product_family + project_ref_code) về một mã enquiry_id duy nhất.
2. [BE/Data] Lưu vết lịch sử gộp trùng vào bảng merge_audit_log và bảo toàn mốc thời gian đủ điều kiện sớm nhất (t0).
3. [QA/Data] Viết Unit Test kiểm chứng 2 liên hệ cùng dự án từ kênh Web và Email được gộp thành đúng 1 vị trí trên hàng đợi.
"""

import gc
from pathlib import Path
import sqlite3
import tempfile
import unittest
import pandas as pd

from src.ingest.deduplicator import deduplicate_dataframe, deduplicate_enquiries


class TestMultiChannelDeduplication(unittest.TestCase):
    def setUp(self):
        """Tạo cơ sở dữ liệu tạm thời để chạy test cô lập."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_songnhat.db"

    def tearDown(self):
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _create_raw_enquiries_table(self, conn: sqlite3.Connection, has_project_ref: bool = False):
        proj_col = "project_ref_code TEXT," if has_project_ref else ""
        conn.execute(f"""
            CREATE TABLE raw_enquiries (
                enquiry_id TEXT PRIMARY KEY,
                account_key TEXT NOT NULL,
                cohort TEXT,
                source TEXT,
                t0_time TEXT NOT NULL,
                extraction_time TEXT NOT NULL,
                product_family TEXT NOT NULL,
                {proj_col}
                spec_completeness TEXT,
                is_urgent_replacement TEXT,
                contract_due_date TEXT,
                workflow_state TEXT,
                industry_at_t0 TEXT,
                enquiry_channel_at_t0 TEXT,
                region_at_t0 TEXT,
                account_age_days_at_t0 INTEGER,
                prior_purchase_count_at_t0 INTEGER,
                estimated_value_band_at_t0 TEXT,
                feature_event_time TEXT,
                feature_available_at TEXT
            )
        """)
        conn.commit()

    def test_two_contacts_web_and_email_merged_to_one_queue_position(self):
        """
        [QA/Data AC] Kiểm chứng 2 liên hệ cùng dự án từ kênh Web và Email
        được gộp thành đúng 1 vị trí trên hàng đợi Sales.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            self._create_raw_enquiries_table(conn, has_project_ref=True)
            # Hai liên hệ cùng account_key, cùng nhóm sản phẩm Motor_DongCo, cùng dự án PRJ-SN-2025
            # Bản ghi 1 đến từ Website lúc 08:30 (sớm hơn)
            # Bản ghi 2 đến từ Email lúc 10:15 (muộn hơn)
            conn.execute("""
                INSERT INTO raw_enquiries (
                    enquiry_id, account_key, cohort, source, t0_time, extraction_time,
                    product_family, project_ref_code, enquiry_channel_at_t0
                ) VALUES 
                ('SN-WEB-001', 'ACCT-100', 'TRAINING_CANDIDATE', 'Website',
                 '2025-01-15T08:30:00+07:00', '2025-01-15T12:00:00+07:00',
                 'Motor_DongCo', 'PRJ-SN-2025', 'Website'),
                ('SN-EML-002', 'ACCT-100', 'TRAINING_CANDIDATE', 'Direct Sales',
                 '2025-01-15T10:15:00+07:00', '2025-01-15T12:00:00+07:00',
                 'Motor_DongCo', 'PRJ-SN-2025', 'Email')
            """)
            conn.commit()
        finally:
            conn.close()

        # Thực thi gộp trùng
        result = deduplicate_enquiries(self.db_path)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["raw_count"], 2)
        self.assertEqual(result["clean_count"], 1)
        self.assertEqual(result["duplicates_count"], 1)

        conn = sqlite3.connect(self.db_path)
        try:
            clean_df = pd.read_sql_query("SELECT * FROM clean_enquiries", conn)
            merge_logs = pd.read_sql_query("SELECT * FROM merge_logs", conn)
            audit_logs = pd.read_sql_query("SELECT * FROM merge_audit_log", conn)
        finally:
            conn.close()

        # 1. Hàng đợi clean_enquiries chỉ còn đúng 1 vị trí
        self.assertEqual(len(clean_df), 1)
        surviving = clean_df.iloc[0]
        self.assertEqual(surviving["enquiry_id"], "SN-WEB-001")
        # 2. Bảo toàn mốc t0_time sớm nhất
        self.assertEqual(surviving["t0_time"], "2025-01-15T08:30:00+07:00")
        self.assertEqual(surviving["enquiry_channel_at_t0"], "Website")

        # 3. Lịch sử gộp trùng được lưu vết đầy đủ trong cả merge_logs và merge_audit_log
        self.assertEqual(len(merge_logs), 1)
        self.assertEqual(len(audit_logs), 1)
        log = merge_logs.iloc[0]
        self.assertEqual(log["canonical_enquiry_id"], "SN-WEB-001")
        self.assertEqual(log["merged_enquiry_id"], "SN-EML-002")
        self.assertEqual(log["canonical_channel"], "Website")
        self.assertEqual(log["merged_channel"], "Email")
        self.assertEqual(log["account_key"], "ACCT-100")
        self.assertEqual(log["product_family"], "Motor_DongCo")
        self.assertEqual(log["project_ref_code"], "PRJ-SN-2025")

    def test_earliest_t0_preserved_when_email_arrives_first(self):
        """
        Kiểm chứng trường hợp kênh Email gửi trước, Web form gửi sau:
        Bản ghi Email (sớm hơn) phải được giữ lại làm canonical_enquiry_id.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            self._create_raw_enquiries_table(conn, has_project_ref=False)
            conn.execute("""
                INSERT INTO raw_enquiries (
                    enquiry_id, account_key, t0_time, extraction_time,
                    product_family, enquiry_channel_at_t0
                ) VALUES 
                ('SN-001', 'ACCT-200', '2025-02-01T14:00:00+07:00', '2025-02-02T00:00:00+07:00',
                 'Drive_BienTan', 'Website'),
                ('SN-002', 'ACCT-200', '2025-02-01T09:00:00+07:00', '2025-02-02T00:00:00+07:00',
                 'Drive_BienTan', 'Email')
            """)
            conn.commit()
        finally:
            conn.close()

        deduplicate_enquiries(self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            clean_df = pd.read_sql_query("SELECT * FROM clean_enquiries", conn)
            merge_logs = pd.read_sql_query("SELECT * FROM merge_logs", conn)
        finally:
            conn.close()

        self.assertEqual(len(clean_df), 1)
        # Giữ lại SN-002 vì có t0 lúc 09:00 (sớm hơn 14:00 của SN-001)
        self.assertEqual(clean_df.iloc[0]["enquiry_id"], "SN-002")
        self.assertEqual(clean_df.iloc[0]["t0_time"], "2025-02-01T09:00:00+07:00")
        self.assertEqual(clean_df.iloc[0]["enquiry_channel_at_t0"], "Email")

        self.assertEqual(len(merge_logs), 1)
        self.assertEqual(merge_logs.iloc[0]["canonical_enquiry_id"], "SN-002")
        self.assertEqual(merge_logs.iloc[0]["merged_enquiry_id"], "SN-001")

    def test_different_product_families_are_not_merged(self):
        """
        Cùng một khách hàng nhưng hỏi 2 nhóm sản phẩm khác nhau (Động cơ và Biến tần)
        phải được giữ thành 2 vị trí độc lập trên hàng đợi Sales.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            self._create_raw_enquiries_table(conn, has_project_ref=False)
            conn.execute("""
                INSERT INTO raw_enquiries (
                    enquiry_id, account_key, t0_time, extraction_time,
                    product_family, enquiry_channel_at_t0
                ) VALUES 
                ('SN-001', 'ACCT-300', '2025-03-01T08:00:00+07:00', '2025-03-02T00:00:00+07:00',
                 'Motor_DongCo', 'Website'),
                ('SN-002', 'ACCT-300', '2025-03-01T09:00:00+07:00', '2025-03-02T00:00:00+07:00',
                 'Drive_BienTan', 'Email')
            """)
            conn.commit()
        finally:
            conn.close()

        deduplicate_enquiries(self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            clean_df = pd.read_sql_query("SELECT * FROM clean_enquiries", conn)
            merge_logs = pd.read_sql_query("SELECT * FROM merge_logs", conn)
        finally:
            conn.close()

        # Cả 2 dòng đều được giữ lại vì khác product_family
        self.assertEqual(len(clean_df), 2)
        self.assertEqual(len(merge_logs), 0)

    def test_different_project_ref_codes_are_not_merged(self):
        """
        Cùng khách hàng, cùng sản phẩm nhưng khác mã dự án (project_ref_code)
        thì không được gộp trùng (đại diện cho 2 gói thầu/nhu cầu riêng).
        """
        conn = sqlite3.connect(self.db_path)
        try:
            self._create_raw_enquiries_table(conn, has_project_ref=True)
            conn.execute("""
                INSERT INTO raw_enquiries (
                    enquiry_id, account_key, t0_time, extraction_time,
                    product_family, project_ref_code, enquiry_channel_at_t0
                ) VALUES 
                ('SN-001', 'ACCT-400', '2025-03-01T08:00:00+07:00', '2025-03-02T00:00:00+07:00',
                 'Motor_DongCo', 'PRJ-PHASE-1', 'Website'),
                ('SN-002', 'ACCT-400', '2025-03-01T09:00:00+07:00', '2025-03-02T00:00:00+07:00',
                 'Motor_DongCo', 'PRJ-PHASE-2', 'Email')
            """)
            conn.commit()
        finally:
            conn.close()

        deduplicate_enquiries(self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            clean_df = pd.read_sql_query("SELECT * FROM clean_enquiries", conn)
            merge_logs = pd.read_sql_query("SELECT * FROM merge_logs", conn)
        finally:
            conn.close()

        self.assertEqual(len(clean_df), 2)
        self.assertEqual(len(merge_logs), 0)

    def test_multi_duplicates_cluster_merge(self):
        """
        Kiểm chứng trường hợp 1 khách hàng gửi 4 lần qua Web, Email, Phone và Trade Fair.
        Tất cả 4 liên hệ phải được gộp thành đúng 1 vị trí duy nhất trên hàng đợi.
        """
        raw_rows = [
            {"enquiry_id": "SN-01", "account_key": "ACCT-500", "product_family": "Transmission_HopSo",
             "t0_time": "2025-04-10T14:00:00+07:00", "enquiry_channel_at_t0": "Website"},
            {"enquiry_id": "SN-02", "account_key": "ACCT-500", "product_family": "Transmission_HopSo",
             "t0_time": "2025-04-10T08:00:00+07:00", "enquiry_channel_at_t0": "Email"},
            {"enquiry_id": "SN-03", "account_key": "ACCT-500", "product_family": "Transmission_HopSo",
             "t0_time": "2025-04-10T11:00:00+07:00", "enquiry_channel_at_t0": "Phone"},
            {"enquiry_id": "SN-04", "account_key": "ACCT-500", "product_family": "Transmission_HopSo",
             "t0_time": "2025-04-10T16:00:00+07:00", "enquiry_channel_at_t0": "Trade Fair"},
        ]
        raw_df = pd.DataFrame(raw_rows)
        clean_df, merge_logs = deduplicate_dataframe(raw_df)

        self.assertEqual(len(clean_df), 1)
        # Bản ghi SN-02 lúc 08:00 sớm nhất
        self.assertEqual(clean_df.iloc[0]["enquiry_id"], "SN-02")
        self.assertEqual(clean_df.iloc[0]["t0_time"], "2025-04-10T08:00:00+07:00")

        # Có đúng 3 bản ghi bị gộp (SN-03, SN-01, SN-04)
        self.assertEqual(len(merge_logs), 3)
        self.assertTrue((merge_logs["canonical_enquiry_id"] == "SN-02").all())
        self.assertSetEqual(set(merge_logs["merged_enquiry_id"]), {"SN-01", "SN-03", "SN-04"})

    def test_idempotency_running_twice_produces_same_output(self):
        """
        Kiểm chứng tính Idempotent: chạy deduplication nhiều lần không gây sinh thêm dữ liệu rác.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            self._create_raw_enquiries_table(conn, has_project_ref=False)
            conn.execute("""
                INSERT INTO raw_enquiries (
                    enquiry_id, account_key, t0_time, extraction_time,
                    product_family, enquiry_channel_at_t0
                ) VALUES 
                ('SN-001', 'ACCT-600', '2025-05-01T08:00:00+07:00', '2025-05-02T00:00:00+07:00',
                 'Motor_DongCo', 'Website'),
                ('SN-002', 'ACCT-600', '2025-05-01T10:00:00+07:00', '2025-05-02T00:00:00+07:00',
                 'Motor_DongCo', 'Email')
            """)
            conn.commit()
        finally:
            conn.close()

        run1 = deduplicate_enquiries(self.db_path)
        run2 = deduplicate_enquiries(self.db_path)

        self.assertEqual(run1["clean_count"], run2["clean_count"])
        self.assertEqual(run1["duplicates_count"], run2["duplicates_count"])
        self.assertEqual(run1["merge_logs_count"], run2["merge_logs_count"])

        conn = sqlite3.connect(self.db_path)
        try:
            count_clean = conn.execute("SELECT COUNT(*) FROM clean_enquiries").fetchone()[0]
            count_logs = conn.execute("SELECT COUNT(*) FROM merge_logs").fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count_clean, 1)
        self.assertEqual(count_logs, 1)

    def test_live_songnhat_db_integrity(self):
        """
        Kiểm chứng thực tế trên file data/songnhat.db:
        - raw_enquiries: 1.250 dòng
        - clean_enquiries: 969 dòng
        - merge_logs / merge_audit_log: 281 dòng
        - Số dòng ở clean_enquiries ít hơn raw_enquiries đúng bằng số dòng trùng:
          1250 - 969 = 281 dòng trùng.
        """
        live_db = Path("data/songnhat.db")
        if not live_db.exists():
            self.skipTest("Không tìm thấy data/songnhat.db")

        stats = deduplicate_enquiries(live_db)
        self.assertEqual(stats["status"], "SUCCESS")
        self.assertEqual(stats["raw_count"], 1250)
        self.assertEqual(stats["clean_count"], 969)
        self.assertEqual(stats["duplicates_count"], 281)
        self.assertEqual(stats["merge_logs_count"], 281)
        self.assertEqual(stats["merge_audit_log_count"], 281)
        self.assertTrue(stats["math_check_passed"])


if __name__ == "__main__":
    unittest.main()
