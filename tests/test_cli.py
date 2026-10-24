import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcdiff.cli import build_parser, main  # noqa: E402
from mcdiff.seed import generate_history  # noqa: E402
from mcdiff.snapshot import save  # noqa: E402

BASE = {
    "schema": "mcdiff/snapshot@1",
    "captured_at": "2026-10-25T04:42:00+08:00",
    "captured_ts": 0,
    "daypart": "late-night",
    "daypart_label": "深夜",
    "synthetic": False,
    "store": {"storeCode": "1420576", "storeName": "测试店"},
    "menu": {"meal_count": 3, "categories": [{"name": "人气热卖", "codes": ["1120", "1406"]}]},
    "meals": {
        "1120": {"name": "双层吉士汉堡", "price_cents": 2300, "original_price_cents": 2300, "tags": [], "category": "人气热卖"},
        "1406": {"name": "板烧鸡腿堡", "price_cents": 2350, "original_price_cents": 2350, "tags": [], "category": "人气热卖"},
        "3010": {"name": "雪碧", "price_cents": 950, "original_price_cents": 950, "tags": [], "category": "饮品"},
    },
    "extras": {},
    "checksum": "sha256:abc",
}


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(argv)
    return code, out.getvalue()


class CliTests(unittest.TestCase):
    def test_parser_requires_command(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_parser_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["diff"])
        self.assertEqual(args.command, "diff")
        self.assertEqual(args.root, ".")

    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run(["--root", tmp, "list"])
        self.assertEqual(code, 1)
        self.assertIn("还没有快照", out)

    def test_list_after_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for snapshot in generate_history(BASE, days=4):
                save(snapshot, tmp)
            code, out = run(["--root", tmp, "list"])
        self.assertEqual(code, 0)
        self.assertIn("synthetic", out)
        self.assertEqual(out.count("2026-10-"), 4)

    def test_diff_default_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            for snapshot in generate_history(BASE, days=4):
                save(snapshot, tmp)
            code, out = run(["--root", tmp, "diff"])
        self.assertEqual(code, 0)
        self.assertIn("@@", out)

    def test_diff_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            for snapshot in generate_history(BASE, days=4):
                save(snapshot, tmp)
            code, out = run(["--root", tmp, "diff", "--json"])
        payload = json.loads(out)
        self.assertIn("summary", payload)
        self.assertIn("changes", payload)

    def test_museum_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            for snapshot in generate_history(BASE, days=10):
                save(snapshot, tmp)
            code, out = run(["--root", tmp, "museum"])
        self.assertEqual(code, 0)
        self.assertIn("麦门考古", out)

    def test_report_writes_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for snapshot in generate_history(BASE, days=6):
                save(snapshot, tmp)
            code, out = run(["--root", tmp, "report"])
            self.assertEqual(code, 0)
            data_js = Path(tmp) / "docs" / "data.js"
            self.assertTrue(data_js.exists())
            text = data_js.read_text(encoding="utf-8")
            self.assertIn("window.MCDIFF_DATA", text)
            payload = json.loads((Path(tmp) / "docs" / "data.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(payload["totals"]["snapshots"], 6)

    def test_diff_without_snapshots_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run(["--root", tmp, "diff"])
        self.assertEqual(code, 1)
        self.assertIn("至少需要两次快照", out)

    def test_seed_command_requires_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run(["--root", tmp, "seed", "--days", "3"])
        self.assertEqual(code, 1)
        self.assertIn("没有可用的基准快照", out)

    def test_seed_command_writes_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "base.json"
            base_path.write_text(json.dumps(BASE, ensure_ascii=False), encoding="utf-8")
            code, out = run(["--root", tmp, "seed", "--base", str(base_path), "--days", "5"])
            self.assertEqual(code, 0)
            self.assertIn("已生成 4 天的示例历史", out)


if __name__ == "__main__":
    unittest.main()
