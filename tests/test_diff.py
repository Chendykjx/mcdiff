import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcdiff.diff import diff_snapshots, pad, truncate  # noqa: E402


def meal(name, price, tags=None, category="汉堡", **extra):
    payload = {"name": name, "price_cents": price, "tags": tags or [], "category": category}
    payload.update(extra)
    return payload


def snap(meals, captured_at="2026-10-25T04:42:00+08:00", daypart="night"):
    return {
        "captured_at": captured_at,
        "daypart_label": daypart,
        "store": {"storeCode": "1420576", "storeName": "深圳湾一店"},
        "menu": {"meal_count": len(meals), "categories": []},
        "meals": meals,
    }


class DiffTests(unittest.TestCase):
    def test_added_and_removed(self):
        before = snap({"1": meal("巨无霸", 2500)})
        after = snap({"2": meal("麦辣鸡腿堡", 2200)})
        diff = diff_snapshots(before, after)
        kinds = {(change.code, change.kind) for change in diff.changes}
        self.assertIn(("2", "added"), kinds)
        self.assertIn(("1", "removed"), kinds)
        self.assertEqual(diff.summary["added"], 1)
        self.assertEqual(diff.summary["removed"], 1)

    def test_price_up_and_down(self):
        before = snap({"1": meal("A", 1000), "2": meal("B", 2000)})
        after = snap({"1": meal("A", 1200), "2": meal("B", 1500)})
        diff = diff_snapshots(before, after)
        by_code = {change.code: change for change in diff.changes}
        self.assertEqual(by_code["1"].kind, "price_up")
        self.assertEqual(by_code["1"].delta_cents, 200)
        self.assertAlmostEqual(by_code["1"].to_dict()["delta_pct"], 20.0)
        self.assertEqual(by_code["2"].kind, "price_down")
        self.assertEqual(by_code["2"].delta_cents, -500)

    def test_renamed_wins_over_price(self):
        before = snap({"1": meal("旧名字", 1000)})
        after = snap({"1": meal("新名字", 1200)})
        diff = diff_snapshots(before, after)
        self.assertEqual(diff.changes[0].kind, "renamed")

    def test_tags_and_category_changes(self):
        before = snap({"1": meal("A", 1000, ["经典"], "汉堡"), "2": meal("B", 1000, [], "小食")})
        after = snap({"1": meal("A", 1000, ["经典", "限时"], "汉堡"), "2": meal("B", 1000, [], "甜品")})
        diff = diff_snapshots(before, after)
        kinds = {change.code: change.kind for change in diff.changes}
        self.assertEqual(kinds["1"], "tags")
        self.assertEqual(kinds["2"], "moved")

    def test_unchanged_count(self):
        before = snap({"1": meal("A", 1000), "2": meal("B", 1000)})
        after = snap({"1": meal("A", 1000), "2": meal("B", 1000)})
        diff = diff_snapshots(before, after)
        self.assertTrue(diff.is_empty)
        self.assertEqual(diff.unchanged, 2)

    def test_order_of_changes(self):
        before = snap({"1": meal("A", 1000), "2": meal("B", 2000)})
        after = snap({"3": meal("C", 3000), "2": meal("B", 2500)})
        diff = diff_snapshots(before, after)
        self.assertEqual([change.kind for change in diff.changes], ["added", "removed", "price_up"])

    def test_render_git_contains_markers(self):
        before = snap({"1": meal("巨无霸", 2500), "2": meal("麦香鱼", 2200)})
        after = snap({"1": meal("巨无霸", 2600), "3": meal("龙焰鸡腿堡", 2900)})
        text = diff_snapshots(before, after).render_git()
        self.assertIn("--- 2026-10-25 04:42", text)
        self.assertIn("+++ 2026-10-25 04:42", text)
        self.assertIn("+ 3", text)
        self.assertIn("- 2", text)
        self.assertIn("¥25.00 → ¥26.00", text)
        self.assertIn("↑¥1.00", text)

    def test_render_git_empty(self):
        same = snap({"1": meal("A", 1000)})
        text = diff_snapshots(same, same).render_git()
        self.assertIn("没有变化", text)

    def test_render_markdown_table(self):
        before = snap({"1": meal("A", 1000)})
        after = snap({"1": meal("A", 1100)})
        md = diff_snapshots(before, after).render_markdown()
        self.assertIn("| 新上 | 下架 | 涨价 | 降价 |", md)
        self.assertIn("`1`", md)
        self.assertIn("¥10.00 → ¥11.00", md)

    def test_to_dict_shape(self):
        before = snap({"1": meal("A", 1000)})
        after = snap({"2": meal("B", 900)})
        payload = diff_snapshots(before, after).to_dict()
        self.assertEqual(payload["before"]["store"]["storeCode"], "1420576")
        self.assertIn("summary", payload)
        self.assertEqual(payload["changes"][0]["label"], "新上")

    def test_cjk_padding(self):
        self.assertEqual(pad("巨无霸", 8), "巨无霸  ")
        self.assertEqual(pad("ab", 4), "ab  ")
        self.assertEqual(pad("7", 3, align="right"), "  7")

    def test_truncate_cjk(self):
        self.assertEqual(truncate("双层吉士汉堡", 6), "双层…")
        self.assertEqual(truncate("abc", 10), "abc")


if __name__ == "__main__":
    unittest.main()
