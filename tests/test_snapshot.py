import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcdiff.snapshot import (  # noqa: E402
    TZ,
    capture,
    format_cents,
    infer_daypart,
    iter_snapshots,
    latest,
    load,
    now_local,
    save,
    yuan_to_cents,
)


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeClient:
    def __init__(self, meals, categories, extras=None):
        self.meals = meals
        self.categories = categories
        self.extras = extras or {}
        self.calls = []

    def call(self, tool, arguments=None):
        self.calls.append((tool, arguments))
        if tool == "query-meals":
            return FakeResult({"categories": self.categories, "meals": self.meals})
        if tool == "query-store-coupons":
            return FakeResult(self.extras.get("coupons", []))
        if tool == "query-promotions":
            return FakeResult(self.extras.get("promotions", []))
        if tool == "calculate-price":
            code = arguments["items"][0]["productCode"]
            if code == "dropped":
                return FakeResult({"price": 0, "productList": []})
            return FakeResult({"price": 1490, "productList": [{"productCode": code}]})
        raise AssertionError(f"unexpected tool {tool}")


def menu():
    meals = {
        "1120": {"name": "双层吉士汉堡", "currentPrice": "23", "originalPrice": "23", "image": "x.png"},
        "9900013304": {"name": "人气经典随心配", "currentPrice": "14.9", "originalPrice": "14.9"},
    }
    categories = [
        {"name": "人气热卖\n精选", "meals": [{"code": "1120", "tags": ["全球爆款", "全天供应"]}]},
        {"name": "随心配 1+1", "meals": [{"code": "9900013304", "tags": ["超值百种组合随心配"]}]},
    ]
    return meals, categories


class SnapshotTests(unittest.TestCase):
    def test_yuan_to_cents(self):
        self.assertEqual(yuan_to_cents("14.9"), 1490)
        self.assertEqual(yuan_to_cents("23"), 2300)
        self.assertEqual(yuan_to_cents("¥8.50"), 850)
        self.assertEqual(yuan_to_cents(14.9), 1490)
        self.assertIsNone(yuan_to_cents(None))
        self.assertIsNone(yuan_to_cents(""))

    def test_format_cents(self):
        self.assertEqual(format_cents(1490), "¥14.90")
        self.assertEqual(format_cents(5), "¥0.05")
        self.assertEqual(format_cents(-300), "-¥3.00")
        self.assertEqual(format_cents(None), "—")

    def test_daypart_boundaries(self):
        cases = {
            (4, 59): ("late-night", "深夜"),
            (5, 0): ("breakfast", "早餐"),
            (10, 29): ("breakfast", "早餐"),
            (10, 30): ("lunch", "午餐"),
            (13, 59): ("lunch", "午餐"),
            (14, 0): ("afternoon", "下午茶"),
            (17, 0): ("dinner", "夜市"),
            (21, 29): ("dinner", "夜市"),
            (21, 30): ("late-night", "深夜"),
        }
        for (hour, minute), expected in cases.items():
            moment = datetime(2026, 10, 25, hour, minute, tzinfo=TZ)
            self.assertEqual(infer_daypart(moment), expected, msg=f"{hour}:{minute}")

    def test_capture_parses_menu(self):
        meals, categories = menu()
        client = FakeClient(meals, categories)
        snapshot = capture(client, {"storeCode": "1420576", "storeName": "测试店"}, include_extras=False)
        self.assertEqual(snapshot["schema"], "mcdiff/snapshot@1")
        self.assertEqual(snapshot["store"]["storeCode"], "1420576")
        self.assertEqual(snapshot["menu"]["meal_count"], 2)
        self.assertEqual(snapshot["meals"]["1120"]["price_cents"], 2300)
        self.assertEqual(snapshot["meals"]["1120"]["tags"], ["全球爆款", "全天供应"])
        self.assertEqual(snapshot["meals"]["1120"]["category"], "人气热卖 精选")
        self.assertEqual(snapshot["meals"]["9900013304"]["price_cents"], 1490)

    def test_capture_marks_extras_errors(self):
        class BrokenClient(FakeClient):
            def call(self, tool, arguments=None):
                if tool in ("query-store-coupons", "query-promotions"):
                    from mcdiff.mcp import McdBusinessError

                    raise McdBusinessError(tool, 600057, "门店可能已关闭")
                return super().call(tool, arguments)

        meals, categories = menu()
        snapshot = capture(BrokenClient(meals, categories), {"storeCode": "1"}, include_extras=True)
        self.assertIn("error", snapshot["extras"]["coupons"])
        self.assertIn("error", snapshot["extras"]["promotions"])

    def test_capture_anchors_detect_dropped_item(self):
        meals, categories = menu()
        client = FakeClient(meals, categories)
        snapshot = capture(
            client,
            {"storeCode": "1"},
            include_extras=False,
            anchors=["9900013304", "dropped"],
        )
        self.assertEqual(snapshot["anchors"]["9900013304"]["price_cents"], 1490)
        self.assertTrue(snapshot["anchors"]["dropped"]["dropped"])

    def test_checksum_stable_and_sensitive(self):
        meals, categories = menu()
        first = capture(FakeClient(meals, categories), {"storeCode": "1"}, include_extras=False)
        second = capture(FakeClient(meals, categories), {"storeCode": "1"}, include_extras=False)
        self.assertEqual(first["checksum"], second["checksum"])

        meals2 = json.loads(json.dumps(meals))
        meals2["1120"]["currentPrice"] = "24"
        third = capture(FakeClient(meals2, categories), {"storeCode": "1"}, include_extras=False)
        self.assertNotEqual(first["checksum"], third["checksum"])

    def test_save_and_load_roundtrip(self):
        meals, categories = menu()
        snapshot = capture(FakeClient(meals, categories), {"storeCode": "1420576", "storeName": "测试店"}, include_extras=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = save(snapshot, tmp)
            self.assertTrue(path.exists())
            restored = load(path)
            self.assertEqual(restored["store"]["storeCode"], "1420576")
            self.assertEqual(restored["meals"]["1120"]["price_cents"], 2300)
            self.assertEqual(len(iter_snapshots(tmp)), 1)
            self.assertEqual(latest(tmp)["checksum"], snapshot["checksum"])

    def test_snapshot_filename_contains_daypart(self):
        meals, categories = menu()
        moment = datetime(2026, 10, 25, 8, 0, tzinfo=TZ)
        snapshot = capture(FakeClient(meals, categories), {"storeCode": "1"}, include_extras=False, moment=moment)
        with tempfile.TemporaryDirectory() as tmp:
            path = save(snapshot, tmp)
        self.assertIn("20261025T0800-breakfast", path.name)

    def test_capture_requires_store_code(self):
        with self.assertRaises(ValueError):
            capture(FakeClient({}, []), {})

    def test_now_local_is_timezone_aware(self):
        self.assertIsNotNone(now_local().tzinfo)


if __name__ == "__main__":
    unittest.main()
