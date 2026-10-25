"""Turn two snapshots into a git-style diff."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .snapshot import format_cents

ADDED = "added"
REMOVED = "removed"
PRICE_UP = "price_up"
PRICE_DOWN = "price_down"
RENAMED = "renamed"
TAGS = "tags"
MOVED = "moved"

KIND_LABELS = {
    ADDED: "新上",
    REMOVED: "下架",
    PRICE_UP: "涨价",
    PRICE_DOWN: "降价",
    RENAMED: "改名",
    TAGS: "标签",
    MOVED: "换分类",
}


def _safe_moment(value: object) -> datetime | None:
    """Parse an ISO timestamp, tolerating missing/broken values in hand-built snapshots."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int, *, align: str = "left") -> str:
    gap = max(0, width - _width(text))
    if align == "right":
        return " " * gap + text
    return text + " " * gap


def truncate(text: str, width: int) -> str:
    if _width(text) <= width:
        return text
    out = ""
    for ch in text:
        if _width(out + ch) > width - 1:
            break
        out += ch
    return out + "…"


@dataclass
class Change:
    kind: str
    code: str
    name: str
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)

    @property
    def delta_cents(self) -> int | None:
        if self.kind not in (PRICE_UP, PRICE_DOWN):
            return None
        return int(self.after.get("price_cents", 0)) - int(self.before.get("price_cents", 0))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "kind": self.kind,
            "label": KIND_LABELS[self.kind],
            "code": self.code,
            "name": self.name,
            "before": self.before,
            "after": self.after,
        }
        if self.delta_cents is not None:
            payload["delta_cents"] = self.delta_cents
            payload["delta_pct"] = round(self.delta_cents / self.before["price_cents"] * 100, 1) if self.before.get("price_cents") else None
        return payload


@dataclass
class Diff:
    before: dict[str, Any]
    after: dict[str, Any]
    changes: list[Change]
    unchanged: int = 0

    # ------------------------------------------------------------------ summary
    @property
    def summary(self) -> dict[str, int]:
        counts = {kind: 0 for kind in KIND_LABELS}
        for change in self.changes:
            counts[change.kind] += 1
        counts["unchanged"] = self.unchanged
        counts["total_before"] = int(self.before.get("menu", {}).get("meal_count", 0))
        counts["total_after"] = int(self.after.get("menu", {}).get("meal_count", 0))
        counts["changed"] = len(self.changes)
        return counts

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def to_dict(self) -> dict[str, Any]:
        return {
            "before": {
                "store": self.before.get("store", {}),
                "captured_at": self.before.get("captured_at"),
                "daypart_label": self.before.get("daypart_label"),
            },
            "after": {
                "store": self.after.get("store", {}),
                "captured_at": self.after.get("captured_at"),
                "daypart_label": self.after.get("daypart_label"),
            },
            "summary": self.summary,
            "changes": [change.to_dict() for change in self.changes],
        }

    # ------------------------------------------------------------------ renderers
    def render_git(self) -> str:
        store = self.after.get("store", {})
        before_moment = _safe_moment(self.before.get("captured_at"))
        after_moment = _safe_moment(self.after.get("captured_at"))
        summary = self.summary

        header = [
            f"--- {before_moment.strftime('%Y-%m-%d %H:%M') if before_moment else '?'} {self.before.get('daypart_label', '')}",
            f"+++ {after_moment.strftime('%Y-%m-%d %H:%M') if after_moment else '?'} {self.after.get('daypart_label', '')}",
            f"@@ {store.get('storeName', '')}（{store.get('storeCode', '')}） "
            f"{summary['total_before']} → {summary['total_after']} 个 SKU "
            f"· +{summary[ADDED]} -{summary[REMOVED]} ↑{summary[PRICE_UP]} ↓{summary[PRICE_DOWN]} "
            f"~{summary[RENAMED] + summary[TAGS] + summary[MOVED]} @@",
        ]
        if self.is_empty:
            return "\n".join(header + ["（没有变化：菜单与上次采集完全一致）"])

        rows = []
        for change in self.changes:
            name = truncate(change.name, 26)
            if change.kind == ADDED:
                marker, detail = "+", f"{format_cents(change.after.get('price_cents'))}  新增"
            elif change.kind == REMOVED:
                marker, detail = "-", f"{format_cents(change.before.get('price_cents'))}  下架"
            elif change.kind in (PRICE_UP, PRICE_DOWN):
                marker = "~"
                delta = change.delta_cents or 0
                pct = change.to_dict().get("delta_pct")
                arrow = "↑" if delta > 0 else "↓"
                detail = (
                    f"{format_cents(change.before.get('price_cents'))} → {format_cents(change.after.get('price_cents'))}"
                    f"  {arrow}{format_cents(abs(delta))}"
                    + (f" ({pct:+.1f}%)" if pct is not None else "")
                )
            elif change.kind == RENAMED:
                marker = "~"
                detail = f"{change.before.get('name')} → {change.name}"
            elif change.kind == TAGS:
                marker = "~"
                added = [t for t in change.after.get("tags", []) if t not in change.before.get("tags", [])]
                removed = [t for t in change.before.get("tags", []) if t not in change.after.get("tags", [])]
                bits = [f"+{tag}" for tag in added] + [f"-{tag}" for tag in removed]
                detail = "标签 " + " ".join(bits)
            else:  # MOVED
                marker = "~"
                detail = f"{change.before.get('category')} → {change.after.get('category')}"
            rows.append(f"{marker} {pad(change.code, 12)} {pad(name, 26)} {detail}")

        return "\n".join(header + rows)

    def render_markdown(self) -> str:
        summary = self.summary
        lines = [
            f"## 菜单变化：{self.after.get('daypart_label', '')} · {self.after.get('store', {}).get('storeName', '')}",
            "",
            "| 新上 | 下架 | 涨价 | 降价 | 改名/标签/分类 | SKU 总数 |",
            "|---:|---:|---:|---:|---:|---:|",
            f"| {summary[ADDED]} | {summary[REMOVED]} | {summary[PRICE_UP]} | {summary[PRICE_DOWN]} | "
            f"{summary[RENAMED] + summary[TAGS] + summary[MOVED]} | {summary['total_before']} → {summary['total_after']} |",
            "",
        ]
        if self.is_empty:
            lines.append("_没有变化。_")
            return "\n".join(lines)
        lines += ["| 变化 | SKU | 名称 | 详情 |", "|---|---|---|---|"]
        for change in self.changes:
            if change.kind == ADDED:
                detail = f"{format_cents(change.after.get('price_cents'))}（新增）"
            elif change.kind == REMOVED:
                detail = f"{format_cents(change.before.get('price_cents'))}（下架）"
            elif change.kind in (PRICE_UP, PRICE_DOWN):
                delta = change.delta_cents or 0
                detail = f"{format_cents(change.before.get('price_cents'))} → {format_cents(change.after.get('price_cents'))}（{format_cents(delta)}）"
            elif change.kind == RENAMED:
                detail = f"{change.before.get('name')} → {change.name}"
            elif change.kind == TAGS:
                detail = "标签调整"
            else:
                detail = f"{change.before.get('category')} → {change.after.get('category')}"
            lines.append(f"| {KIND_LABELS[change.kind]} | `{change.code}` | {change.name} | {detail} |")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def diff_snapshots(before: Mapping[str, Any], after: Mapping[str, Any]) -> Diff:
    before_meals: Mapping[str, Any] = before.get("meals") or {}
    after_meals: Mapping[str, Any] = after.get("meals") or {}
    changes: list[Change] = []
    unchanged = 0

    for code in sorted(set(before_meals) | set(after_meals)):
        old = before_meals.get(code)
        new = after_meals.get(code)
        if old is None and new is not None:
            changes.append(Change(ADDED, code, new.get("name", ""), {}, dict(new)))
            continue
        if new is None and old is not None:
            changes.append(Change(REMOVED, code, old.get("name", ""), dict(old), {}))
            continue
        assert old is not None and new is not None

        if old.get("name") != new.get("name"):
            changes.append(Change(RENAMED, code, new.get("name", ""), dict(old), dict(new)))
            continue
        old_price = int(old.get("price_cents") or 0)
        new_price = int(new.get("price_cents") or 0)
        if old_price != new_price:
            kind = PRICE_UP if new_price > old_price else PRICE_DOWN
            changes.append(Change(kind, code, new.get("name", ""), dict(old), dict(new)))
            continue
        if sorted(old.get("tags") or []) != sorted(new.get("tags") or []):
            changes.append(Change(TAGS, code, new.get("name", ""), dict(old), dict(new)))
            continue
        if (old.get("category") or "") != (new.get("category") or ""):
            changes.append(Change(MOVED, code, new.get("name", ""), dict(old), dict(new)))
            continue
        unchanged += 1

    order = {ADDED: 0, REMOVED: 1, PRICE_UP: 2, PRICE_DOWN: 3, RENAMED: 4, TAGS: 5, MOVED: 6}
    changes.sort(key=lambda c: (order.get(c.kind, 9), c.code))
    return Diff(before=dict(before), after=dict(after), changes=changes, unchanged=unchanged)


__all__ = [
    "ADDED",
    "Change",
    "Diff",
    "KIND_LABELS",
    "MOVED",
    "PRICE_DOWN",
    "PRICE_UP",
    "REMOVED",
    "RENAMED",
    "TAGS",
    "diff_snapshots",
    "pad",
    "truncate",
]
