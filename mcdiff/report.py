"""Build the payload that powers the static demo page."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

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


def build_demo_payload(
    root: str | Path = ".",
    store_code: str | None = None,
    *,
    max_diffs: int = 16,
) -> dict[str, Any]:
    series = load_series(root, store_code)
    if not series:
        raise SystemExit("没有快照可导出。先运行 `python -m mcdiff capture --store <门店编号>` 或 `python -m mcdiff seed`。")

    series.sort(key=lambda snap: str(snap.get("captured_at")))
    metas = [_snapshot_meta(snapshot, index) for index, snapshot in enumerate(series)]

    diffs: list[dict[str, Any]] = []
    for index, (before, after) in enumerate(_consecutive_pairs(series)):
        diff = diff_snapshots(before, after)
        payload = diff.to_dict()
        payload["id"] = f"{metas[index]['id']}__{metas[index + 1]['id']}"
        payload["changes"] = payload["changes"][:MAX_CHANGES_PER_DIFF]
        payload["label"] = (
            f"{before.get('daypart_label', '')} → {after.get('daypart_label', '')}"
            f" · {str(after.get('captured_at'))[5:16].replace('T', ' ')}"
        )
        if payload["summary"]["changed"] or payload["summary"]["total_before"] != payload["summary"]["total_after"]:
            diffs.append(payload)

    diffs.sort(key=lambda item: item["summary"]["changed"], reverse=True)
    diffs = diffs[:max_diffs]

    museum = build_museum(series).to_dict()

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
        "generated_at": now_local().isoformat(timespec="seconds"),
        "project": {
            "name": "McDiff",
            "tagline": "给麦当劳菜单建一个 git 仓库",
            "repo": "https://github.com/你的账号/mcdiff",
        },
        "totals": totals,
        "stores": sorted(stores.values(), key=lambda item: str(item["last_at"]), reverse=True),
        "snapshots": metas,
        "detail": detail,
        "diffs": diffs,
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
