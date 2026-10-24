"""Build the payload that powers the static demo page."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .bucket import bucketize
from .diff import diff_snapshots
from .museum import build_museum
from .snapshot import TZ, load_series, now_local

MAX_CHANGES_PER_DIFF = 60


def _snapshot_meta(snapshot: Mapping[str, Any], index: int) -> dict[str, Any]:
    store = snapshot.get("store", {})
    return {
        "id": f"{store.get('storeCode')}-{snapshot.get('captured_at', '')[:16]}",
        "index": index,
        "store": store.get("storeCode"),
        "storeName": store.get("storeName"),
        "captured_at": snapshot.get("captured_at"),
        "daypart": snapshot.get("daypart"),
        "daypart_label": snapshot.get("daypart_label"),
        "synthetic": bool(snapshot.get("synthetic")),
        "meal_count": int(snapshot.get("menu", {}).get("meal_count", 0)),
        "checksum": snapshot.get("checksum"),
    }


def _consecutive_pairs(series: list[dict[str, Any]]) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for before, after in zip(series, series[1:]):
        yield before, after


def pick_primary_store(series: list[dict[str, Any]]) -> str:
    """Choose the store with the most snapshots (ties → the most recently seen).

    A demo payload must never mix stores: pairing store A's snapshot with store
    B's would report the whole menu as removed/added.
    """
    counts: dict[str, int] = {}
    latest: dict[str, str] = {}
    for snapshot in series:
        code = str((snapshot.get("store") or {}).get("storeCode") or "")
        if not code:
            continue
        counts[code] = counts.get(code, 0) + 1
        captured = str(snapshot.get("captured_at") or "")
        if captured > latest.get(code, ""):
            latest[code] = captured
    if not counts:
        raise SystemExit("快照里没有 storeCode，无法导出演示数据。")
    return max(counts, key=lambda code: (counts[code], latest[code]))


def _pick_default(diffs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """默认展示哪一组：同餐段、有实质变化、实质变化最多、且尽量新。"""
    if not diffs:
        return None

    def score(item: dict[str, Any]) -> tuple[int, int, int, str]:
        bucket = item["bucket"]["summary"]
        return (
            1 if item.get("same_daypart") else 0,
            1 if bucket["real"] else 0,
            bucket["real"],
            str(item["after"]["captured_at"]),
        )

    best = max(diffs, key=score)
    bucket = best["bucket"]["summary"]
    return {
        "before_id": best["before_id"],
        "after_id": best["after_id"],
        "same_daypart": best.get("same_daypart", False),
        "real": bucket["real"],
        "rotation": bucket["rotation"],
        "label": best["label"],
    }


def build_demo_payload(
    root: str | Path = ".",
    store_code: str | None = None,
    *,
    max_diffs: int = 16,
) -> dict[str, Any]:
    all_series = load_series(root)
    if not all_series:
        raise SystemExit("没有快照可导出。先运行 `python -m mcdiff capture --store <门店编号>` 或 `python -m mcdiff seed`。")

    store_code = store_code or pick_primary_store(all_series)
    series = [
        snapshot
        for snapshot in all_series
        if str((snapshot.get("store") or {}).get("storeCode")) == store_code
    ]
    if not series:
        raise SystemExit(f"门店 {store_code} 没有快照。")

    series.sort(key=lambda snap: str(snap.get("captured_at")))
    metas = [_snapshot_meta(snapshot, index) for index, snapshot in enumerate(series)]

    museum_obj = build_museum(series)
    museum = museum_obj.to_dict()
    status = dict(museum_obj.status)
    first_seen = {item.code: item.first_seen for item in museum_obj.items}

    diffs: list[dict[str, Any]] = []
    all_diffs: list[dict[str, Any]] = []
    for index, (before, after) in enumerate(_consecutive_pairs(series)):
        diff = diff_snapshots(before, after)
        payload = diff.to_dict()
        payload["id"] = f"{metas[index]['id']}__{metas[index + 1]['id']}"
        payload["before_id"] = metas[index]["id"]
        payload["after_id"] = metas[index + 1]["id"]
        payload["changes"] = payload["changes"][:MAX_CHANGES_PER_DIFF]
        payload["label"] = (
            f"{before.get('daypart_label', '')} → {after.get('daypart_label', '')}"
            f" · {str(after.get('captured_at'))[5:16].replace('T', ' ')}"
        )
        bucket = bucketize(before, after, diff, status=status, first_seen=first_seen)
        payload["bucket"] = bucket.to_dict()
        payload["same_daypart"] = not bucket.cross_daypart
        all_diffs.append(payload)
        if payload["summary"]["changed"] or payload["summary"]["total_before"] != payload["summary"]["total_after"]:
            diffs.append(payload)

    default = _pick_default(all_diffs)
    diffs.sort(key=lambda item: item["summary"]["changed"], reverse=True)
    diffs = diffs[:max_diffs]

    stores: dict[str, dict[str, Any]] = {}
    for meta in metas:
        entry = stores.setdefault(
            str(meta["store"]),
            {
                "storeCode": meta["store"],
                "storeName": meta["storeName"],
                "snapshots": 0,
                "first_at": meta["captured_at"],
                "last_at": meta["captured_at"],
                "synthetic_days": 0,
            },
        )
        entry["snapshots"] += 1
        entry["last_at"] = meta["captured_at"]
        if meta["synthetic"]:
            entry["synthetic_days"] += 1

    totals = {
        "snapshots": len(metas),
        "stores": len(stores),
        "skus_tracked": museum["stats"]["tracked"],
        "gone": museum["stats"]["gone"],
        "price_up": sum(item["summary"]["price_up"] for item in diffs),
        "price_down": sum(item["summary"]["price_down"] for item in diffs),
        "added": sum(item["summary"]["added"] for item in diffs),
        "removed": sum(item["summary"]["removed"] for item in diffs),
        "real_changes": sum(item["bucket"]["summary"]["real"] for item in all_diffs),
        "rotation_changes": sum(item["bucket"]["summary"]["rotation"] for item in all_diffs),
        "synthetic": any(meta["synthetic"] for meta in metas),
    }

    store_info_by_id = {meta["id"]: meta for meta in metas}
    # compact per-snapshot menu so the static page can diff *any* pair client-side
    detail = []
    for snapshot, meta in zip(series, metas):
        meals = snapshot.get("meals") or {}
        compact = {
            code: [
                meal.get("name", ""),
                int(meal.get("price_cents") or 0),
                meal.get("category", ""),
                list(meal.get("tags") or []),
            ]
            for code, meal in meals.items()
        }
        detail.append(
            {
                "id": meta["id"],
                "meals": compact,
                "categories": [
                    {"name": category.get("name", ""), "codes": list(category.get("codes") or [])}
                    for category in (snapshot.get("menu", {}).get("categories") or [])
                ],
            }
        )

    return {
        # deterministic: same snapshots → byte-identical payload → no commit noise
        "generated_at": str(series[-1].get("captured_at")),
        "project": {
            "name": "McDiff",
            "tagline": "给麦当劳菜单建一个 git 仓库",
            "repo": "https://github.com/Chendykjx/mcdiff",
        },
        "totals": totals,
        "stores": sorted(stores.values(), key=lambda item: str(item["last_at"]), reverse=True),
        "snapshots": metas,
        "detail": detail,
        "diffs": diffs,
        "default": default,
        "museum": museum,
    }


def write_demo_data(payload: Mapping[str, Any], *, root: str | Path = ".", docs_dir: str | Path = "docs") -> tuple[Path, Path]:
    docs = Path(root) / docs_dir
    docs.mkdir(parents=True, exist_ok=True)
    json_path = docs / "data.json"
    js_path = docs / "data.js"
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    js_path.write_text(
        "// generated by `python -m mcdiff report` — do not edit by hand\n"
        f"window.MCDIFF_DATA = {blob};\n",
        encoding="utf-8",
    )
    return json_path, js_path


def write_index_stub(*, root: str | Path = ".", docs_dir: str | Path = "docs") -> Path:
    """Only used when docs/index.html is missing; the real page is hand-written."""
    path = Path(root) / docs_dir / "index.html"
    if path.exists():
        return path
    path.write_text(
        "<!doctype html><meta charset='utf-8'><title>McDiff</title>"
        "<p>demo page missing — run <code>python -m mcdiff report</code>.</p>",
        encoding="utf-8",
    )
    return path


__all__ = ["build_demo_payload", "write_demo_data", "write_index_stub"]
