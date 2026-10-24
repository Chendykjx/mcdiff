import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcdiff.museum import build_museum  # noqa: E402


def meal(name, price, tags=None, category="汉堡"):
    return {"name": name, "price_cents": price, "tags": tags or [], "category": category}


def snap(meals, captured_at, synthetic=False):
    return {
        "captured_at": captured_at,
        "daypart_label": "午餐",
        "store": {"storeCode": "1", "storeName": "测试店"},
        "synthetic": synthetic,
        "menu": {"meal_count": len(meals)},
        "meals": meals,
    }


class MuseumTests(unittest.TestCase):
    def series(self):
        return [
            snap({"1": meal("巨无霸", 2500), "2": meal("薯角", 1150), "3": meal("新品堡", 2900)}, "2026-10-01T12:00:00+08:00", synthetic=True),
            snap({"1": meal("巨无霸", 2500), "2": meal("薯角", 1200), "3": meal("新品堡", 2900)}, "2026-10-03T12:00:00+08:00", synthetic=True),
            snap({"1": meal("巨无霸", 2600), "3": meal("新品堡", 2900)}, "2026-10-05T12:00:00+08:00", synthetic=True),
        ]

    def test_gone_items(self):
        museum = build_museum(self.series())
        gone = museum.gone
        self.assertEqual([item.name for item in gone], ["薯角"])
        self.assertEqual(gone[0].days_alive, 3)
        self.assertEqual(gone[0].last_price, 1200)
        self.assertTrue(gone[0].epitaph())

    def test_alive_items(self):
        museum = build_museum(self.series())
        names = sorted(item.name for item in museum.alive)
        self.assertEqual(names, ["巨无霸", "新品堡"])

    def test_price_history(self):
        museum = build_museum(self.series())
        burger = next(item for item in museum.items if item.name == "巨无霸")
        self.assertEqual([price for _ts, price in burger.prices], [2500, 2600])
        self.assertEqual(len(burger.price_changes), 1)
        self.assertEqual(burger.price_changes[0]["to"], 2600)

    def test_stats_and_dict(self):
        museum = build_museum(self.series())
        payload = museum.to_dict()
        self.assertEqual(payload["snapshot_count"], 3)
        self.assertEqual(payload["stats"]["gone"], 1)
        self.assertEqual(payload["stats"]["alive"], 2)
        self.assertTrue(payload["stats"]["synthetic"])
        gone = [item for item in payload["items"] if item["status"] == "gone"]
        self.assertEqual(gone[0]["code"], "2")
        self.assertIn("epitaph", gone[0])

    def test_empty_input(self):
        with self.assertRaises(ValueError):
            build_museum([])

    def test_render_markdown(self):
        text = build_museum(self.series()).render_markdown()
        self.assertIn("麦门考古", text)
        self.assertIn("薯角", text)
        self.assertIn("| 餐品 |", text)

    def test_render_markdown_without_gone(self):
        museum = build_museum([snap({"1": meal("巨无霸", 2500)}, "2026-10-01T12:00:00+08:00")])
        self.assertIn("暂时没有发现下架的餐品", museum.render_markdown())

    def test_longest_lifespan_ordering(self):
        museum = build_museum(self.series())
        payload = museum.to_dict()
        gone_items = [item for item in payload["items"] if item["status"] == "gone"]
        # gone items come first in the payload
        self.assertEqual(payload["items"][0]["status"], "gone")
        self.assertEqual(len(gone_items), 1)


if __name__ == "__main__":
    unittest.main()
