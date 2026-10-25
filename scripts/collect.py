"""Capture snapshots on a schedule.

Two modes:

    python scripts/collect.py --stores 1420576,1421182 --city 深圳市   # once
    python scripts/collect.py --every 1800 --hours 12                  # loop

The same script powers ``.github/workflows/snapshot.yml``: the Action simply
runs it once on a cron schedule and commits whatever changed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcdiff.mcp import McdAuthError, McdMCPClient, McdMCPError  # noqa: E402
from mcdiff.report import build_demo_payload, write_demo_data  # noqa: E402
from mcdiff.snapshot import capture, describe, load_series, now_local, save  # noqa: E402


def log(message: str) -> None:
    stamp = now_local().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def capture_stores(client: McdMCPClient, stores: list[str], city: str, keyword: str, *, extras: bool) -> int:
    written = 0
    for store_code in stores:
        try:
            candidates = client.nearby_stores(city, keyword) if city else []
        except McdMCPError:
            candidates = []
        store = {"storeCode": store_code, "city": city}
        for candidate in candidates:
            if str(candidate.get("storeCode")) == str(store_code):
                store = dict(candidate)
                store["city"] = city
                break
        try:
            snapshot = capture(client, store, include_extras=extras)
        except McdMCPError as exc:
            log(f"{store_code} 跳过：{exc}")
            continue
        existing = load_series(ROOT, store_code)
        if existing and existing[-1].get("checksum") == snapshot.get("checksum"):
            log(f"{store_code} 无变化（checksum 相同），跳过写入")
            continue
        path = save(snapshot, ROOT)
        written += 1
        log(f"已写入 {path.relative_to(ROOT)} · {describe(snapshot)} · {snapshot['menu']['meal_count']} SKU")
    return written


def rebuild_report() -> None:
    payload = build_demo_payload(ROOT)
    write_demo_data(payload, root=ROOT)
    totals = payload["totals"]
    log(
        f"演示数据已更新：{totals['snapshots']} 次快照 · 追踪 {totals['skus_tracked']} SKU · "
        f"下架 {totals['gone']} 个 · {len(payload['diffs'])} 组 diff"
    )


def git_commit(store_names: str) -> None:
    try:
        subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
        if diff.returncode == 0:
            log("git：没有需要提交的变化")
            return
        message = f"chore(snapshot): {store_names} @ {now_local().strftime('%Y-%m-%d %H:%M')}"
        subprocess.run(["git", "commit", "-m", message], cwd=ROOT, check=True)
        log(f"git：已提交 {message}")
    except subprocess.CalledProcessError as exc:
        log(f"git 提交失败：{exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="McDiff collector")
    parser.add_argument("--stores", default="1420576", help="逗号分隔的门店编号")
    parser.add_argument("--city", default="")
    parser.add_argument("--keyword", default="麦当劳")
    parser.add_argument("--no-extras", action="store_true", help="跳过券/促销")
    parser.add_argument("--every", type=int, default=0, help="循环模式：间隔秒数")
    parser.add_argument("--hours", type=float, default=0, help="循环模式：总时长（小时）")
    parser.add_argument("--report", action="store_true", default=True, help="采集后重建演示数据")
    parser.add_argument("--git", action="store_true", help="采集后 git commit")
    args = parser.parse_args()

    stores = [code.strip() for code in args.stores.split(",") if code.strip()]
    if not stores:
        print("--stores 不能为空", file=sys.stderr)
        return 2
    if args.every and args.every < 10:
        print("--every 至少 10 秒（接口限流 600 次/分钟）", file=sys.stderr)
        return 2
    try:
        client = McdMCPClient()
    except McdAuthError as exc:
        print(f"{exc}", file=sys.stderr)
        print("提示：Token 从 https://open.mcd.cn/mcp 申请，写入环境变量 MCD_MCP_TOKEN。", file=sys.stderr)
        return 2
    deadline = time.time() + args.hours * 3600 if args.every and args.hours else None
    while True:
        try:
            written = capture_stores(client, stores, args.city, args.keyword, extras=not args.no_extras)
            if (written or not args.every) and args.report:
                rebuild_report()
            if written and args.git:
                git_commit(",".join(stores))
        except Exception:  # noqa: BLE001 - keep the loop alive
            log("采集失败：\n" + traceback.format_exc())
        if not args.every:
            return 0
        if deadline and time.time() + args.every > deadline:
            log("到达 --hours 上限，退出循环")
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
