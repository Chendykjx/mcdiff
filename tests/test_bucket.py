import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcdiff.bucket import bucketize, render_bucketed  # noqa: E402
from mcdiff.diff import diff_snapshots  # noqa: E402


def meal(name, price, tags=None, category="汉堡"):
    return {"name": name, "price_cents": price, "tags": tags or [], "category": category}


def snap(meals, captured_at="2026-10-25T05:05:00+08:00", daypart="breakfast"):
    return {
        "captured_at": captured_at,
        "daypart": daypart,
        "daypart_label": {"breakfast": "早餐", "lunch": "午餐", "late-night": "深夜"}.get(daypart, daypart),
        "store": {"storeCode": "1420576", "storeName": "测试店"},
        "menu": {"meal_count": len(meals), "categories": []},
        "meals": meals,
    }


class BucketTests(unittest.TestCase):
    def test_cross_daypart_is_rotation(self):
        before = snap({"1": meal("巨无霸", 2500), "2": meal("薯条", 1000)}, daypart="late-night")
        after = snap({"3": meal("豆浆", 950), "4": meal("油条", 850)}, daypart="breakfast")
        diff = diff_snapshots(before, after)
        bucket = bucketize(before, after, diff)
        self.assertTrue(bucket.cross_daypart)
        self.assertEqual(len(bucket.fresh), 0)
        self.assertEqual(len(bucket.gone), 0)
        self.assertEqual(len(bucket.rotation_added), 2)
        self.assertEqual(len(bucket.rotation_removed), 2)
        self.assertEqual(bucket.summary["real"], 0)
        self.assertEqual(bucket.summary["rotation"], 4)

    def test_same_daypart_fresh_and_gone(self):
        before = snap({"1": meal("巨无霸", 2500), "2": meal("薯角", 1150)})
        after = snap({"1": meal("巨无霸", 2500)})
        diff = diff_snapshots(before, after)
        bucket = bucketize(
            before,
            after,
            diff,
            status={"2": "gone"},
            first_seen={"1": "2026-10-01T12:00:00+08:00", "2": "2026-10-01T12:00:00+08:00"},
        )
        self.assertFalse(bucket.cross_daypart)
        self.assertEqual([change.code for change in bucket.gone], ["2"])
        self.assertEqual(bucket.summary["real"], 1)

    def test_same_daypart_new_item_is_fresh(self):
        before = snap({"1": meal("巨无霸", 2500)})
        after = snap({"1": meal("巨无霸", 2500), "9": meal("新堡", 2900)}, captured_at="2026-10-25T07:05:00+08:00")
        diff = diff_snapshots(before, after)
        bucket = bucketize(before, after, diff, status={}, first_seen={"1": "x", "9": "2026-10-25T07:05:00+08:00"})
        self.assertEqual([change.code for change in bucket.fresh], ["9"])

    def test_temporarily_absent_is_rotation(self):
        before = snap({"1": meal("巨无霸", 2500)})
        after = snap({"1": meal("巨无霸", 2500), "7": meal("回来堡", 2000)})
        diff = diff_snapshots(before, after)
        bucket = bucketize(before, after, diff, status={}, first_seen={"7": "2026-10-01T12:00:00+08:00"})
        self.assertEqual(len(bucket.rotation_added), 1)
        self.assertEqual(len(bucket.fresh), 0)

    def test_price_changes_are_real(self):
        before = snap({"1": meal("巨无霸", 2500)})
        after = snap({"1": meal("巨无霸", 2600)})
        diff = diff_snapshots(before, after)
        bucket = bucketize(before, after, diff)
        self.assertEqual(len(bucket.other), 1)
        self.assertEqual(bucket.summary["real"], 1)

    def test_render_bucketed_groups_rotation(self):
        before = snap({"1": meal("巨无霸", 2500), "2": meal("薯条", 1000)}, daypart="late-night")
        after = snap({"3": meal("豆浆", 950)}, daypart="breakfast")
        diff = diff_snapshots(before, after)
        bucket = bucketize(before, after, diff)
        text = render_bucketed(before, after, diff, bucket)
        self.assertIn("没有实质变化", text)
        self.assertIn("随餐段上架", text)
        self.assertIn("随餐段让位", text)

    def test_bucket_to_dict_shape(self):
        before = snap({"1": meal("巨无霸", 2500)})
        after = snap({"1": meal("巨无霸", 2600)})
        diff = diff_snapshots(before, after)
        payload = bucketize(before, after, diff).to_dict()
        self.assertIn("summary", payload)
        self.assertEqual(payload["other"][0]["code"], "1")
        self.assertEqual(payload["other"][0]["label"], "涨价")


if __name__ == "__main__":
    unittest.main()
