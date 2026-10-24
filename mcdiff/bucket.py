"""Split a diff into "real changes" and "daypart rotation".

A menu has dayparts. When a store switches from 深夜 to 早餐, dozens of SKUs
"disappear" and dozens "appear" — that is not a menu change, it is the same menu
rotating.  This module re-labels such rows so a diff never claims 61 new products
when the menu simply changed shifts.

Rules (mirrors what the demo page does in JS):

* before and after belong to different dayparts  → every added/removed row is rotation;
* added, first ever seen at ``after``            → 真新上架 (fresh);
* added, seen before                             → rotation (temporarily absent);
* removed and the museum says ``gone``           → 永久下架 (gone);
* removed but still on the menu in its daypart   → rotation;
* price / rename / tags / category               → real change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .diff import KIND_LABELS, Change, Diff

ROTATION = "rotation"


@dataclass
class Bucket:
    cross_daypart: bool
    fresh: list[Change] = field(default_factory=list)
    gone: list[Change] = field(default_factory=list)
    rotation_added: list[Change] = field(default_factory=list)
    rotation_removed: list[Change] = field(default_factory=list)
    other: list[Change] = field(default_factory=list)

    @property
    def rotation(self) -> list[Change]:
        return self.rotation_added + self.rotation_removed

    @property
    def real(self) -> list[Change]:
        return self.other + self.fresh + self.gone

    @property
    def summary(self) -> dict[str, int]:
        return {
            "fresh": len(self.fresh),
            "gone": len(self.gone),
            "rotation_added": len(self.rotation_added),
            "rotation_removed": len(self.rotation_removed),
            "rotation": len(self.rotation),
            "other": len(self.other),
            "real": len(self.real),
            "changed": len(self.real) + len(self.rotation),
        }

    @property
    def has_real(self) -> bool:
        return bool(self.real)

    def to_dict(self) -> dict[str, Any]:
        def rows(changes: Iterable[Change]) -> list[dict[str, Any]]:
            return [
                {
                    "code": change.code,
                    "name": change.name,
                    "kind": change.kind,
                    "label": KIND_LABELS.get(change.kind, change.kind),
                    "before": change.before,
                    "after": change.after,
                    "delta_cents": change.delta_cents,
                }
                for change in changes
            ]

        return {
            "cross_daypart": self.cross_daypart,
            "fresh": rows(self.fresh),
            "gone": rows(self.gone),
            "rotation_added": rows(self.rotation_added),
            "rotation_removed": rows(self.rotation_removed),
            "other": rows(self.other),
            "summary": self.summary,
        }


def bucketize(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    diff: Diff,
    *,
    status: Mapping[str, str] | None = None,
    first_seen: Mapping[str, str] | None = None,
) -> Bucket:
    cross = str(before.get("daypart")) != str(after.get("daypart"))
    status = status or {}
    first_seen = first_seen or {}
    after_at = str(after.get("captured_at"))

    bucket = Bucket(cross_daypart=cross)
    for change in diff.changes:
        if change.kind == "added":
            # 没有历史（显式指定一对快照）时，无法判断"新"还是"轮换"，按新上架处理
            if cross or (first_seen and first_seen.get(change.code, "") != after_at):
                bucket.rotation_added.append(change)
            else:
                bucket.fresh.append(change)
        elif change.kind == "removed":
            if cross or (status and status.get(change.code) != "gone"):
                bucket.rotation_removed.append(change)
            else:
                bucket.gone.append(change)
        else:
            bucket.other.append(change)
    return bucket


def render_bucketed(before: Mapping[str, Any], after: Mapping[str, Any], diff: Diff, bucket: Bucket) -> str:
    """git-style text that separates real changes from daypart rotation."""
    from .diff import pad, truncate
    from .snapshot import format_cents

    store = (after.get("store") or {}).get("storeName") or (after.get("store") or {}).get("storeCode", "")
    stamp = lambda snap: str(snap.get("captured_at"))[5:16].replace("T", " ")  # noqa: E731
    real = bucket.summary["real"]
    rotation = bucket.summary["rotation"]
    lines = [
        f"--- {stamp(before)} {before.get('daypart_label', '')}",
        f"+++ {stamp(after)} {after.get('daypart_label', '')}",
        f"@@ {store}（{(after.get('store') or {}).get('storeCode', '')}） "
        f"{(before.get('menu') or {}).get('meal_count', 0)} → {(after.get('menu') or {}).get('meal_count', 0)} 个 SKU · "
        f"{real} 处实质变化"
        + (f"（另有 {rotation} 处餐段轮换）" if rotation else "")
        + " @@",
    ]

    def row(change) -> str:
        marker = {"added": "+", "removed": "-"}.get(change.kind, "~")
        name = pad(truncate(change.name, 22), 24)
        if change.kind in ("price_up", "price_down"):
            before_price = format_cents((change.before or {}).get("price_cents"))
            after_price = format_cents((change.after or {}).get("price_cents"))
            detail = f"{before_price} → {after_price}  {KIND_LABELS.get(change.kind, change.kind)}"
        else:
            detail = KIND_LABELS.get(change.kind, change.kind)
        return f"{marker} {pad(change.code, 12)} {name} {detail}"

    for change in bucket.other:
        lines.append(row(change))
    for change in bucket.fresh:
        lines.append(row(change))
    for change in bucket.gone:
        lines.append(row(change))
    if bucket.rotation_added:
        lines.append(f"+ {len(bucket.rotation_added)} 个 SKU 随餐段上架（不计入新上架）")
    if bucket.rotation_removed:
        lines.append(f"- {len(bucket.rotation_removed)} 个 SKU 随餐段让位（不计入永久下架）")
    if not real and rotation:
        lines.insert(3, "  （没有实质变化：所有差异都来自餐段切换）")
    return "\n".join(lines)


def museum_indexes(series: Iterable[Mapping[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """Return (status, first_seen) from a museum built over ``series``."""
    from .museum import build_museum

    museum = build_museum(series)
    status = dict(museum.status)
    first_seen = {item.code: item.first_seen for item in museum.items}
    return status, first_seen


__all__ = ["ROTATION", "Bucket", "bucketize", "museum_indexes", "render_bucketed"]
