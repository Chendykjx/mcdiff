import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcdiff.seed import generate_history  # noqa: E402


def base_snapshot():
    meals = {
        "1120": {"name": "双层吉士汉堡", "price_cents": 2300, "original_price_cents": 2300, "tags": [], "category": "人气热卖"},
        "1406": {"name": "板烧鸡腿堡", "price_cents": 2350, "original_price_cents": 2350, "tags": [], "category": "人气热卖"},
        "3010": {"name": "雪碧", "price_cents": 950, "original_price_cents": 950, "tags": [], "category": "饮品"},
    }
    return {
        "schema": "mcdiff/snapshot@1",
        "captured_at": "2026-10-25T04:42:00+08:00",
        "captured_ts": 0,
        "daypart": "late-night",
        "daypart_label": "深夜",
        "synthetic": False,
        "store": {"storeCode": "1420576", "storeName": "测试店"},
        "menu": {"meal_count": 3, "categories": [{"name": "人气热卖", "codes": ["1120", "1406"]}, {"name": "饮品", "codes": ["3010"]}]},
        "meals": meals,
        "extras": {},
        "checksum": "sha256:abc",
    }


class SeedTests(unittest.TestCase):
    def test_length_and_last_is_real(self):
        series = generate_history(base_snapshot(), days=10)
        self.assertEqual(len(series), 10)
        self.assertTrue(all(item["synthetic"] for item in series[:-1]))
        self.assertFalse(series[-1]["synthetic"])
        self.assertEqual(series[-1]["captured_at"], base_snapshot()["captured_at"])

    def test_deterministic(self):
        first = generate_history(base_snapshot(), days=12)
        second = generate_history(base_snapshot(), days=12)
        self.assertEqual([item["meals"] for item in first], [item["meals"] for item in second])
        self.assertEqual([item["checksum"] for item in first], [item["checksum"] for item in second])

    def test_dates_are_consecutive(self):
        series = generate_history(base_snapshot(), days=5)
        dates = [item["captured_at"][:10] for item in series]
        self.assertEqual(dates, ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"])

    def test_contains_launches_and_retirements(self):
        series = generate_history(base_snapshot(), days=30)
        codes = [set(item["meals"]) for item in series]
        union = set().union(*codes)
        intersection = set.intersection(*codes)
        self.assertGreater(len(union), len(intersection))  # something appeared or vanished
        self.assertGreater(len(union) - len(intersection), 3)

    def test_prices_land_back_on_todays_values(self):
        series = generate_history(base_snapshot(), days=30)
        for code, meal in base_snapshot()["meals"].items():
            self.assertEqual(series[-1]["meals"][code]["price_cents"], meal["price_cents"])
            self.assertIn(code, series[0]["meals"])

    def test_menu_counts_match_meals(self):
        for item in generate_history(base_snapshot(), days=6):
            self.assertEqual(item["menu"]["meal_count"], len(item["meals"]))
            codes = [code for category in item["menu"]["categories"] for code in category["codes"]]
            self.assertEqual(sorted(codes), sorted(item["meals"]))

    def test_does_not_mutate_input(self):
        snapshot = base_snapshot()
        original = copy.deepcopy(snapshot)
        generate_history(snapshot, days=8)
        self.assertEqual(snapshot, original)


if __name__ == "__main__":
    unittest.main()
