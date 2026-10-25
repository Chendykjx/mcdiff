"""Command line interface: ``python -m mcdiff <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .bucket import render_bucketed
from .diff import diff_snapshots
from .mcp import (
    DEFAULT_TOKEN_ENV,
    McdAuthError,
    McdBusinessError,
    McdMCPClient,
    McdMCPError,
)
from .museum import build_museum
from .report import build_demo_payload, write_demo_data
from .seed import generate_history
from .snapshot import (
    capture,
    describe,
    iter_snapshots,
    latest,
    load,
    load_series,
    now_local,
    save,
)


def _client(args: argparse.Namespace) -> McdMCPClient:
    return McdMCPClient(token=getattr(args, "token", None), url=args.url)


def _friendly(exc: Exception) -> str:
    if isinstance(exc, McdAuthError):
        return f"鉴权失败：{exc}\n提示：Token 从 https://open.mcd.cn/mcp 申请，写入环境变量 {DEFAULT_TOKEN_ENV}。"
    if isinstance(exc, McdBusinessError):
        return f"接口返回业务错误：[{exc.code}] {exc.message}（tool={exc.tool}）"
    return f"{type(exc).__name__}: {exc}"


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        client = _client(args)
        tools = client.list_tools()
        info = client.now()
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(_friendly(exc), file=sys.stderr)
        return 2
    print(f"McDiff {__version__}")
    print(f"server   : {args.url}")
    print(f"tools    : {len(tools)}")
    print(f"server   : {info.get('datetime')} ({info.get('dayOfWeek', '')})")
    print(f"requests : {client.stats()['requests']}")
    if len(tools) < 30:
        print("警告：工具数量异常，服务端可能变更。", file=sys.stderr)
    return 0


def cmd_stores(args: argparse.Namespace) -> int:
    try:
        client = _client(args)
        stores = client.nearby_stores(args.city, args.keyword, be_type=args.be_type)
    except Exception as exc:  # noqa: BLE001
        print(_friendly(exc), file=sys.stderr)
        return 2
    if not stores:
        print("没有找到门店，试试换一个关键词（city 和 keyword 必须同时给）。", file=sys.stderr)
        return 1
    print(f"{'storeCode':<10} {'营业':<4} {'营业时间':<12} 门店")
    for store in stores:
        status = "是" if store.get("businessStatus") else "否"
        hours = f"{store.get('businessStartTime', '')}-{store.get('businessEndTime', '')}"
        print(f"{store.get('storeCode', ''):<10} {status:<4} {hours:<12} {store.get('storeName', '')}")
    return 0


def _resolve_store(client: McdMCPClient, args: argparse.Namespace) -> dict:
    store: dict = {"storeCode": args.store, "city": args.city}
    if args.name:
        store["storeName"] = args.name
    if args.city and args.keyword:
        try:
            candidates = client.nearby_stores(args.city, args.keyword, be_type=args.be_type)
        except McdMCPError:
            candidates = []
        for candidate in candidates:
            if str(candidate.get("storeCode")) == str(args.store):
                merged = dict(candidate)
                merged["city"] = args.city
                return merged
        open_candidates = [c for c in candidates if c.get("businessStatus")]
        if open_candidates and not args.store:
            merged = dict(open_candidates[0])
            merged["city"] = args.city
            return merged
        if candidates:
            print(
                f"提示：附近的 {len(candidates)} 家门店里没有 {args.store}，将用最小信息采集。",
                file=sys.stderr,
            )
    return store


def cmd_capture(args: argparse.Namespace) -> int:
    try:
        client = _client(args)
        store = _resolve_store(client, args)
        anchors = [code.strip() for code in (args.anchors or "").split(",") if code.strip()]
        snapshot = capture(
            client,
            store,
            note=args.note,
            include_extras=not args.no_extras,
            anchors=anchors,
        )
        path = save(snapshot, args.root)
    except Exception as exc:  # noqa: BLE001
        print(_friendly(exc), file=sys.stderr)
        return 2
    print(f"已保存快照：{path}")
    print(f"{describe(snapshot)} · {snapshot['menu']['meal_count']} 个 SKU · checksum {snapshot['checksum'][-8:]}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    paths = iter_snapshots(args.root, args.store)
    if not paths:
        print("还没有快照。")
        return 1
    print(f"{'captured_at':<26} {'store':<10} {'daypart':<12} {'SKU':>4}  {'synthetic':<9} file")
    for path in paths:
        snap = load(path)
        print(
            f"{snap.get('captured_at', ''):<26} {str((snap.get('store') or {}).get('storeCode', '')):<10} "
            f"{snap.get('daypart_label', ''):<12} "
            f"{snap.get('menu', {}).get('meal_count', 0):>4}  "
            f"{'yes' if snap.get('synthetic') else 'no':<9} {path.name}"
        )
    return 0


def _store_series(root: str, store: str | None) -> list[dict]:
    """Load snapshots, scoped to one store (never mix stores in a diff)."""
    series = load_series(root, store)
    if store is None and series:
        from .report import pick_primary_store

        primary = pick_primary_store(series)
        series = [snap for snap in series if str((snap.get("store") or {}).get("storeCode")) == primary]
    return sorted(series, key=lambda snap: str(snap.get("captured_at")))


def _pick_pair(args: argparse.Namespace) -> tuple[dict, dict]:
    if args.from_path and args.to_path:
        return load(args.from_path), load(args.to_path)
    series = _store_series(args.root, args.store)
    if len(series) < 2:
        raise SystemExit("至少需要两次快照才能 diff。先多跑几次 capture，或 `python -m mcdiff seed`。")
    return series[-2], series[-1]


def cmd_diff(args: argparse.Namespace) -> int:
    try:
        before, after = _pick_pair(args)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"读不到快照文件：{exc.filename or exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"快照文件不是合法的 JSON：{exc}", file=sys.stderr)
        return 1
    diff = diff_snapshots(before, after)
    if args.json:
        print(diff.to_json())
    elif args.markdown:
        print(diff.render_markdown())
    elif args.buckets:
        from .bucket import bucketize, museum_indexes

        series = _store_series(args.root, args.store)
        status, first_seen = museum_indexes(series) if len(series) > 1 else ({}, {})
        bucket = bucketize(before, after, diff, status=status, first_seen=first_seen)
        print(render_bucketed(before, after, diff, bucket))
    else:
        print(diff.render_git())
    return 0


def cmd_gif(args: argparse.Namespace) -> int:
    from .gifexport import GifUnavailable, default_pair, render_gif

    try:
        if args.from_path and args.to_path:
            before, after = load(args.from_path), load(args.to_path)
            summary = None
        else:
            series = _store_series(args.root, args.store)
            if len(series) < 2:
                print("至少需要两次快照才能导出 GIF。", file=sys.stderr)
                return 1
            before, after = default_pair(series)
            from .bucket import bucketize, museum_indexes

            status, first_seen = museum_indexes(series)
            diff = diff_snapshots(before, after)
            bucket = bucketize(before, after, diff, status=status, first_seen=first_seen)
            summary = {**diff.summary, **{key: bucket.summary[key] for key in ("real", "rotation", "fresh", "gone")}}
        out = args.out or f"menu-diff-{after.get('store', {}).get('storeCode', 'store')}.gif"
        path = render_gif(
            before,
            after,
            out,
            width=args.width,
            theme=args.theme,
            max_rows=args.rows,
            summary=summary,
        )
    except GifUnavailable as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    size = path.stat().st_size
    print(f"已导出 GIF：{path}（{size / 1024:.0f} KB，{args.width}px 宽，{args.theme} 主题）")
    return 0 if size else 1


def cmd_museum(args: argparse.Namespace) -> int:
    series = _store_series(args.root, args.store)
    if not series:
        print("还没有快照。", file=sys.stderr)
        return 1
    museum = build_museum(series)
    if args.json:
        print(json.dumps(museum.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(museum.render_markdown())
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    if args.base:
        base = load(args.base)
    else:
        base = latest(args.root, args.store)
    if not base:
        print("没有可用的基准快照：先 capture 一次，或用 --base 指定文件。", file=sys.stderr)
        return 1
    series = generate_history(base, days=args.days)
    written = 0
    for snapshot in series[:-1]:  # last one is the real base capture
        save(snapshot, args.root)
        written += 1
    print(f"已生成 {written} 天的示例历史（全部标记 synthetic=true）。")
    print("提示：示例数据只用于演示 diff/博物馆，README 中必须标注。")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    try:
        payload = build_demo_payload(args.root, args.store)
        json_path, js_path = write_demo_data(payload, root=args.root)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    totals = payload["totals"]
    print(f"已生成 {json_path} 与 {js_path}")
    print(
        f"快照 {totals['snapshots']} 次 · 门店 {totals['stores']} 家 · 追踪 SKU {totals['skus_tracked']} 个 · "
        f"下架 {totals['gone']} 个 · diff {len(payload['diffs'])} 组"
    )
    print("打开 docs/index.html 查看演示页（无需 Token）。")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    path = Path(args.root) / "docs" / "index.html"
    print(f"演示页：{path.resolve()}")
    print("直接双击打开，或用任何静态服务器（例如 `python -m http.server -d docs 8000`）。")
    return 0 if path.exists() else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcdiff",
        description="McDiff — 给麦当劳菜单建一个 git 仓库（麦当劳 MCP 参赛作品）",
    )
    parser.add_argument("--version", action="version", version=f"mcdiff {__version__}")
    parser.add_argument("--root", default=".", help="仓库根目录（默认当前目录）")
    parser.add_argument("--url", default="https://mcp.mcd.cn", help="MCP 服务地址")
    parser.add_argument("--token", default=None, help=f"覆盖环境变量 {DEFAULT_TOKEN_ENV}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="检查 Token / 连通性 / 工具数量")
    doctor.set_defaults(func=cmd_doctor)

    stores = sub.add_parser("stores", help="按城市 + 关键词找门店")
    stores.add_argument("--city", required=True)
    stores.add_argument("--keyword", default="麦当劳")
    stores.add_argument("--be-type", type=int, default=1, choices=[1, 5])
    stores.set_defaults(func=cmd_stores)

    cap = sub.add_parser("capture", help="采集一次门店菜单快照")
    cap.add_argument("--store", default="", help="门店编号；留空则自动选第一家营业中的门店")
    cap.add_argument("--city", default="")
    cap.add_argument("--keyword", default="麦当劳")
    cap.add_argument("--name", default="", help="门店名（找不到门店信息时手动补）")
    cap.add_argument("--be-type", type=int, default=1, choices=[1, 2, 5, 6])
    cap.add_argument("--note", default="")
    cap.add_argument("--anchors", default="", help="用 calculate-price 核价的商品编码，逗号分隔")
    cap.add_argument("--no-extras", action="store_true", help="跳过券/促销采集")
    cap.set_defaults(func=cmd_capture)

    listing = sub.add_parser("list", help="列出本地快照")
    listing.add_argument("--store", default=None)
    listing.set_defaults(func=cmd_list)

    diff = sub.add_parser("diff", help="对两次快照做 diff（默认最近两次）")
    diff.add_argument("--store", default=None)
    diff.add_argument("--from", dest="from_path", default=None)
    diff.add_argument("--to", dest="to_path", default=None)
    diff.add_argument("--json", action="store_true")
    diff.add_argument("--markdown", action="store_true")
    diff.add_argument("--buckets", action="store_true", help="把餐段轮换与实质变化分开显示")
    diff.set_defaults(func=cmd_diff)

    museum = sub.add_parser("museum", help="麦门考古：列出已下架的餐品")
    museum.add_argument("--store", default=None)
    museum.add_argument("--json", action="store_true")
    museum.set_defaults(func=cmd_museum)

    seed = sub.add_parser("seed", help="生成示例历史（标记 synthetic，供离线演示）")
    seed.add_argument("--base", default=None, help="基准快照文件；默认取最新一次")
    seed.add_argument("--store", default=None)
    seed.add_argument("--days", type=int, default=30)
    seed.set_defaults(func=cmd_seed)

    report = sub.add_parser("report", help="生成演示页数据 docs/data.js")
    report.add_argument("--store", default=None)
    report.set_defaults(func=cmd_report)

    gif = sub.add_parser("gif", help="把两组快照的 diff 导出成 GIF")
    gif.add_argument("--from", dest="from_path", default=None)
    gif.add_argument("--to", dest="to_path", default=None)
    gif.add_argument("--store", default=None)
    gif.add_argument("--out", default=None, help="输出文件（默认 menu-diff-<storeCode>.gif）")
    gif.add_argument("--width", type=int, default=800)
    gif.add_argument("--theme", choices=["light", "dark"], default="light")
    gif.add_argument("--rows", type=int, default=9, help="正文最多展示几行变化")
    gif.set_defaults(func=cmd_gif)

    demo = sub.add_parser("demo", help="打印演示页路径")
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        print(f"now: {now_local().isoformat(timespec='seconds')}")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
