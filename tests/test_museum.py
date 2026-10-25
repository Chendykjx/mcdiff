import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcdiff.museum import build_museum  # noqa: E402


def meal(name, price, tags=None, category="汉堡"):
    return {"name": name, "price_cents": price, "tags": tags or [], "category": category}


def snap(meals, captured_at, synthetic=False, daypart="lunch"):
    return {
        "captured_at": captured_at,
        "daypart": daypart,
        "daypart_label": {"breakfast": "早餐", "lunch": "午餐", "late-night": "深夜"}.get(daypart, daypart),
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

    def test_daypart_rotation_is_not_retirement(self):
        series = [
            snap({"1": meal("猪柳麦满分", 1400), "2": meal("巨无霸", 2500)}, "2026-10-24T05:00:00+08:00", daypart="breakfast"),
            snap({"2": meal("巨无霸", 2500)}, "2026-10-24T05:05:00+08:00", daypart="breakfast"),
            snap({"2": meal("巨无霸", 2500), "3": meal("麦辣鸡腿堡", 2200)}, "2026-10-24T12:00:00+08:00", daypart="lunch"),
        ]
        museum = build_museum(series)
        status = {item.name: museum.status[item.code] for item in museum.items}
        self.assertEqual(status["猪柳麦满分"], "gone")   # breakfast still runs, item no longer listed
        self.assertEqual(status["麦辣鸡腿堡"], "alive")  # lunch-only item, latest lunch lists it

    def test_item_alive_when_latest_daypart_still_lists_it(self):
        series = [
            snap({"1": meal("猪柳麦满分", 1400)}, "2026-10-24T05:00:00+08:00", daypart="breakfast"),
            snap({"9": meal("巨无霸", 2500)}, "2026-10-24T12:00:00+08:00", daypart="lunch"),
            snap({"1": meal("猪柳麦满分", 1400)}, "2026-10-25T05:00:00+08:00", daypart="breakfast"),
        ]
        museum = build_museum(series)
        breakfast = next(item for item in museum.items if item.name == "猪柳麦满分")
        self.assertEqual(museum.status[breakfast.code], "alive")
        self.assertEqual(breakfast.dayparts, {"breakfast"})

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


class EpitaphTests(unittest.TestCase):
    def test_epitaph_uses_last_daypart(self):
        series = [
            snap({"1": meal("猪柳麦满分", 1400), "2": meal("巨无霸", 2500)}, "2026-10-01T05:00:00+08:00", daypart="breakfast"),
            snap({"2": meal("巨无霸", 2500)}, "2026-10-02T05:00:00+08:00", daypart="breakfast"),
            snap({"2": meal("巨无霸", 2500)}, "2026-10-03T12:00:00+08:00", daypart="lunch"),
        ]
        museum = build_museum(series)
        gone = museum.gone[0]
        self.assertEqual(gone.name, "猪柳麦满分")
        self.assertTrue(gone.epitaph())
        self.assertEqual(gone.last_daypart_label, "早餐")
