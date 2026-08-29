from __future__ import annotations

import os
import base64
import io
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook

import app.main as main_module
from app.main import app
from app.services.douyin_accounts import (
    CreatorDataImport,
    DouyinAccountCreate,
    DouyinAccountStore,
    DouyinAccountUpdate,
    analyse_creator_rows,
    parse_creator_export,
    parse_creator_workbook,
)


CREATOR_CSV = """日期,投稿量,总播放量,总点赞量,总评论量,总分享量,5秒完播率,2秒跳出率,平均播放时长,粉丝净增
2026-08-19,1,1200,80,15,8,70%,21%,43.2秒,12
2026-08-20,1,1000,66,12,6,68%,23%,40秒,9
2026-08-21,1,700,30,5,2,55%,37%,31秒,3
2026-08-22,1,600,24,4,1,51%,42%,27秒,1
"""

SINGLE_ROW_CREATOR_CSV = """日期,投稿量,总播放量,5秒完播率
2026-08-22,1,600,51%
"""


class CreatorExportTests(unittest.TestCase):
    def test_parse_and_compare_account_own_periods(self) -> None:
        parsed = parse_creator_export(CREATOR_CSV)
        self.assertEqual(4, parsed["source_row_count"])
        analysis = analyse_creator_rows(parsed["rows"])
        self.assertEqual("ready", analysis["status"])
        self.assertLess(
            analysis["current"]["five_second_completion"],
            analysis["previous"]["five_second_completion"],
        )
        areas = {item["area"] for item in analysis["recommendations"]}
        self.assertIn("开头 2 秒", areas)
        self.assertIn("前 5 秒承接", areas)

    def test_descending_dates_are_sorted_and_summary_row_is_ignored(self) -> None:
        exported = """日期,投稿量,总播放量,5秒完播率
合计,4,3500,61%
2026-08-22,1,600,51%
2026-08-21,1,700,55%
2026-08-20,1,1000,68%
2026-08-19,1,1200,70%
"""
        parsed = parse_creator_export(exported)
        self.assertEqual(4, parsed["source_row_count"])
        analysis = analyse_creator_rows(parsed["rows"])
        self.assertEqual("date_ascending", analysis["order_basis"])
        self.assertEqual(1100, analysis["previous"]["views_per_post"])
        self.assertEqual(650, analysis["current"]["views_per_post"])

    def test_parse_xlsx_creator_export(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "作品数据"
        sheet.append(["日期", "投稿量", "总播放量", "5秒完播率"])
        sheet.append(["2026-08-19", 1, 1200, 0.70])
        sheet.append(["2026-08-20", 1, 900, 0.61])
        buffer = io.BytesIO()
        workbook.save(buffer)
        workbook.close()
        parsed = parse_creator_workbook(base64.b64encode(buffer.getvalue()).decode("ascii"))
        self.assertEqual(2, parsed["source_row_count"])
        self.assertEqual(1200, parsed["rows"][0]["views"])
        self.assertEqual(70, parsed["rows"][0]["five_second_completion"])
        self.assertEqual("作品数据", parsed["worksheet"])

    def test_publish_time_header_orders_real_creator_rows(self) -> None:
        exported = """作品名称,发布时间,播放量,5s完播率
旧作品,2026-06-27 20:15:38,1019,30.0426%
新作品,2026-07-03 08:17:13,1036,28.1678%
"""
        parsed = parse_creator_export(exported)
        self.assertEqual("date", parsed["recognised_headers"]["发布时间"])
        self.assertEqual("2026-06-27 20:15:38", parsed["rows"][0]["date"])
        analysis = analyse_creator_rows(parsed["rows"])
        self.assertEqual("date_ascending", analysis["order_basis"])


class DouyinAccountStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"PROJECT024_DOUYIN_OPERATIONS_ROOT": self._tmp.name},
        )
        self._env.start()
        self.store = DouyinAccountStore()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_account_import_is_deduplicated_and_strategy_updates(self) -> None:
        account = self.store.create(
            DouyinAccountCreate(display_name="测试账号", douyin_id="demo_024")
        )
        payload = CreatorDataImport(filename="作品数据.csv", csv_text=CREATOR_CSV)
        first = self.store.import_creator_data(account["id"], payload)
        second = self.store.import_creator_data(account["id"], payload)
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual("completed", first["status"])
        self.assertEqual("completed", second["status"])
        self.assertEqual(1, len(self.store.list_imports(account["id"])))

        updated = self.store.update(
            account["id"], DouyinAccountUpdate(strategy_notes="下一轮只改开头钩子")
        )
        self.assertEqual("下一轮只改开头钩子", updated["strategy_notes"])
        result = self.store.analysis(account["id"])
        self.assertEqual("completed", result["status"])
        self.assertNotIn("csv_text", result["latest_import"])

    def test_single_row_import_and_analysis_remain_insufficient(self) -> None:
        account = self.store.create(DouyinAccountCreate(display_name="单行账号"))
        imported = self.store.import_creator_data(
            account["id"],
            CreatorDataImport(filename="单行作品数据.csv", csv_text=SINGLE_ROW_CREATOR_CSV),
        )
        self.assertEqual("insufficient", imported["status"])
        self.assertEqual("insufficient", imported["analysis"]["status"])
        self.assertNotIn("已完成趋势比较", imported["message"])

        analysed = self.store.analysis(account["id"])
        self.assertEqual("insufficient", analysed["status"])
        self.assertEqual("insufficient", analysed["analysis"]["status"])
        self.assertNotIn("已完成趋势比较", analysed["message"])


class DouyinAccountApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"PROJECT024_DOUYIN_OPERATIONS_ROOT": self._tmp.name},
        )
        self._env.start()
        self.store = DouyinAccountStore()
        self._patch = patch.object(main_module, "douyin_accounts", self.store)
        self._patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self._patch.stop()
        self._env.stop()
        self._tmp.cleanup()

    def test_create_import_analyse_and_connection_boundary(self) -> None:
        connection = self.client.get("/api/douyin/accounts/connection")
        self.assertEqual(200, connection.status_code)
        self.assertTrue(connection.json()["recommended_path"]["available"])
        self.assertEqual("creator_export", connection.json()["recommended_path"]["key"])
        self.assertFalse(connection.json()["official_oauth"]["available"])
        self.assertTrue(connection.json()["creator_export"]["available"])

        created = self.client.post(
            "/api/douyin/accounts",
            json={"display_name": "测试账号", "douyin_id": "demo_api"},
        )
        self.assertEqual(201, created.status_code, created.text)
        account_id = created.json()["id"]
        imported = self.client.post(
            f"/api/douyin/accounts/{account_id}/imports",
            json={"filename": "作品数据.csv", "csv_text": CREATOR_CSV},
        )
        self.assertEqual(201, imported.status_code, imported.text)
        self.assertEqual("completed", imported.json()["status"])
        analysed = self.client.get(f"/api/douyin/accounts/{account_id}/analysis")
        self.assertEqual(200, analysed.status_code, analysed.text)
        self.assertEqual("completed", analysed.json()["status"])
        self.assertEqual("ready", analysed.json()["analysis"]["status"])

    def test_single_row_api_does_not_report_completed_trend(self) -> None:
        created = self.client.post(
            "/api/douyin/accounts",
            json={"display_name": "单行 API 账号"},
        )
        self.assertEqual(201, created.status_code, created.text)
        account_id = created.json()["id"]

        imported = self.client.post(
            f"/api/douyin/accounts/{account_id}/imports",
            json={"filename": "单行作品数据.csv", "csv_text": SINGLE_ROW_CREATOR_CSV},
        )
        self.assertEqual(201, imported.status_code, imported.text)
        self.assertEqual("insufficient", imported.json()["status"])
        self.assertEqual("insufficient", imported.json()["analysis"]["status"])
        self.assertNotIn("已完成趋势比较", imported.json()["message"])

        analysed = self.client.get(f"/api/douyin/accounts/{account_id}/analysis")
        self.assertEqual(200, analysed.status_code, analysed.text)
        self.assertEqual("insufficient", analysed.json()["status"])
        self.assertEqual("insufficient", analysed.json()["analysis"]["status"])
        self.assertNotIn("已完成趋势比较", analysed.json()["message"])


if __name__ == "__main__":
    unittest.main()
