import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcdiff.report import build_demo_payload, pick_primary_store  # noqa: E402
from mcdiff.snapshot import save  # noqa: E402


def snapshot(store_code, captured_at, meals, *, daypart="lunch", synthetic=True, name=None):
    return {
        "schema": "mcdiff/snapshot@1",
        "captured_at": captured_at,
        "captured_ts": 0,
        "daypart": daypart,
        "daypart_label": {"lunch": "午餐", "breakfast": "早餐"}.get(daypart, daypart),
        "synthetic": synthetic,
        "store": {"storeCode": store_code, "storeName": name or f"门店 {store_code}"},
        "menu": {"meal_count": len(meals), "categories": [{"name": "汉堡", "codes": list(meals)}]},
        "meals": meals,
        "extras": {},
        "checksum": f"sha256:{store_code}{captured_at}",
    }


def meal(name, price):
    return {"name": name, "price_cents": price, "tags": [], "category": "汉堡"}


class ReportTests(unittest.TestCase):
    def test_primary_store_prefers_most_snapshots(self):
        series = [
            snapshot("A", "2026-10-01T12:00:00+08:00", {"1": meal("a", 100)}),
            snapshot("B", "2026-10-02T12:00:00+08:00", {"2": meal("b", 100)}),
            snapshot("A", "2026-10-03T12:00:00+08:00", {"1": meal("a", 100)}),
        ]
        self.assertEqual(pick_primary_store(series), "A")

    def test_primary_store_tie_breaks_on_latest(self):
        series = [
            snapshot("A", "2026-10-01T12:00:00+08:00", {"1": meal("a", 100)}),
            snapshot("B", "2026-10-02T12:00:00+08:00", {"2": meal("b", 100)}),
        ]
        self.assertEqual(pick_primary_store(series), "B")

    def test_payload_never_pairs_different_stores(self):
        with tempfile.TemporaryDirectory() as tmp:
            save(snapshot("A", "2026-10-01T12:00:00+08:00", {"1": meal("巨无霸", 2500)}), tmp)
            save(snapshot("B", "2026-10-02T12:00:00+08:00", {"9": meal("另一种堡", 1900)}), tmp)
            save(snapshot("A", "2026-10-03T12:00:00+08:00", {"1": meal("巨无霸", 2600)}), tmp)
            payload = build_demo_payload(tmp)
        stores = {item["store"] for item in payload["snapshots"]}
        self.assertEqual(stores, {"A"})
        self.assertEqual(payload["totals"]["stores"], 1)
        self.assertEqual(payload["totals"]["snapshots"], 2)
        for diff in payload["diffs"]:
            self.assertEqual(diff["before"]["store"]["storeCode"], diff["after"]["store"]["storeCode"])
        # only a price change, not "B's items removed"
        self.assertEqual(payload["diffs"][0]["summary"]["removed"], 0)
        self.assertEqual(payload["diffs"][0]["summary"]["price_up"], 1)

    def test_payload_store_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            save(snapshot("A", "2026-10-01T12:00:00+08:00", {"1": meal("a", 100)}), tmp)
            save(snapshot("B", "2026-10-02T12:00:00+08:00", {"2": meal("b", 100)}, name="B 店"), tmp)
            save(snapshot("B", "2026-10-03T12:00:00+08:00", {"2": meal("b", 200)}, name="B 店"), tmp)
            payload = build_demo_payload(tmp, "B")
        self.assertEqual({item["store"] for item in payload["snapshots"]}, {"B"})
        self.assertEqual(payload["stores"][0]["storeName"], "B 店")

    def test_museum_is_store_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            save(snapshot("A", "2026-10-01T12:00:00+08:00", {"1": meal("a", 100), "2": meal("b", 200)}), tmp)
            save(snapshot("B", "2026-10-02T12:00:00+08:00", {"8": meal("x", 100)}), tmp)
            save(snapshot("A", "2026-10-03T12:00:00+08:00", {"1": meal("a", 100)}), tmp)
            payload = build_demo_payload(tmp)
        gone = [item for item in payload["museum"]["items"] if item["status"] == "gone"]
        self.assertEqual([item["code"] for item in gone], ["2"])

    def test_ids_are_unique_for_same_second_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = snapshot("A", "2026-10-25T05:05:00+08:00", {"1": meal("a", 100)})
            save(base, tmp)
            twin = snapshot("A", "2026-10-25T05:05:00.500000+08:00", {"1": meal("a", 200)})
            save(twin, tmp)
            payload = build_demo_payload(tmp)
        ids = [item["id"] for item in payload["snapshots"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 2)

    def test_totals_cover_every_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            save(snapshot("A", "2026-10-01T12:00:00+08:00", {"1": meal("a", 100)}), tmp)
            save(snapshot("A", "2026-10-02T12:00:00+08:00", {"1": meal("a", 100), "2": meal("b", 200)}), tmp)
            save(snapshot("A", "2026-10-03T12:00:00+08:00", {"1": meal("a", 100)}), tmp)
            payload = build_demo_payload(tmp, max_diffs=1)
        self.assertEqual(len(payload["diffs"]), 1)  # only one pair is exported…
        self.assertEqual(payload["totals"]["added"], 1)   # …but totals see both
        self.assertEqual(payload["totals"]["removed"], 1)

    def test_empty_repository_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                build_demo_payload(tmp)


if __name__ == "__main__":
    unittest.main()
