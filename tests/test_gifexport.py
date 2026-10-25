import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcdiff.gifexport import default_pair, render_gif  # noqa: E402

try:
    from PIL import Image  # noqa: F401

    HAS_PILLOW = True
except ImportError:  # pragma: no cover
    HAS_PILLOW = False


def meal(name, price):
    return {"name": name, "price_cents": price, "tags": [], "category": "汉堡"}


def snap(store, captured_at, meals, daypart="breakfast"):
    return {
        "captured_at": captured_at,
        "daypart": daypart,
        "daypart_label": {"breakfast": "早餐", "lunch": "午餐"}.get(daypart, daypart),
        "store": {"storeCode": store, "storeName": f"门店 {store}"},
        "menu": {"meal_count": len(meals), "categories": []},
        "meals": meals,
    }


@unittest.skipUnless(HAS_PILLOW, "需要 Pillow 才能导出 GIF")
class GifExportTests(unittest.TestCase):
    def test_render_gif_creates_animation(self):
        before = snap("A", "2026-10-25T05:05:00+08:00", {"1": meal("巨无霸", 2500), "2": meal("薯条", 1000)})
        after = snap("A", "2026-10-25T07:05:00+08:00", {"1": meal("巨无霸", 2600)})
        with tempfile.TemporaryDirectory() as tmp:
            path = render_gif(before, after, Path(tmp) / "out.gif", width=400)
            self.assertTrue(path.exists())
            size = path.stat().st_size
            self.assertGreater(size, 4000)
            self.assertLess(size, 2_000_000)
            with Image.open(path) as image:
                self.assertEqual(image.size, (400, 242))
                self.assertGreaterEqual(image.n_frames, 8)

    def test_dark_theme(self):
        before = snap("A", "2026-10-25T05:05:00+08:00", {"1": meal("巨无霸", 2500)})
        after = snap("A", "2026-10-25T07:05:00+08:00", {"1": meal("巨无霸", 2700)})
        with tempfile.TemporaryDirectory() as tmp:
            path = render_gif(before, after, Path(tmp) / "dark.gif", width=320, theme="dark")
            self.assertTrue(path.exists())

    def test_default_pair_prefers_same_daypart(self):
        series = [
            snap("A", "2026-10-25T05:05:00+08:00", {"1": meal("a", 100)}, daypart="breakfast"),
            snap("A", "2026-10-25T07:05:00+08:00", {"1": meal("a", 100), "2": meal("b", 200)}, daypart="breakfast"),
            snap("A", "2026-10-25T12:00:00+08:00", {"9": meal("c", 300)}, daypart="lunch"),
        ]
        before, after = default_pair(series)
        self.assertEqual(before["daypart"], "breakfast")
        self.assertEqual(after["daypart"], "breakfast")

    def test_default_pair_never_crosses_stores(self):
        series = [
            snap("A", "2026-10-25T05:05:00+08:00", {"1": meal("a", 100)}),
            snap("B", "2026-10-25T05:06:00+08:00", {"2": meal("b", 100)}),
            snap("A", "2026-10-25T07:05:00+08:00", {"1": meal("a", 200)}),
            snap("A", "2026-10-25T09:05:00+08:00", {"1": meal("a", 300)}),
        ]
        before, after = default_pair(series)
        self.assertEqual(before["store"]["storeCode"], after["store"]["storeCode"])

    def test_clamps_absurd_options(self):
        before = snap("A", "2026-10-25T05:05:00+08:00", {"1": meal("a", 100)})
        after = snap("A", "2026-10-25T07:05:00+08:00", {"1": meal("a", 200)})
        with tempfile.TemporaryDirectory() as tmp:
            path = render_gif(before, after, Path(tmp) / "tiny.gif", width=120, max_rows=50)
            with Image.open(path) as image:
                self.assertEqual(image.size[0], 320)

    def test_single_snapshot_raises(self):
        with self.assertRaises(ValueError):
            default_pair([snap("A", "2026-10-25T05:05:00+08:00", {"1": meal("a", 100)})])


if __name__ == "__main__":
    unittest.main()
