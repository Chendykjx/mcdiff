"""Export a snapshot diff as an animated GIF (needs Pillow).

    python -m mcdiff gif                       # 默认：最近一组「同餐段 + 实质变化」
    python -m mcdiff gif --from a.json --to b.json -o menu-diff.gif

The renderer is deterministic: same input → same bytes.  It is a *presentation*
layer only; all numbers come from :mod:`mcdiff.diff` and :mod:`mcdiff.bucket`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Pillow is optional: the CLI works without it, only `gif` needs it.
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - depends on environment
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]

from .diff import KIND_LABELS, Change, diff_snapshots, pad, truncate
from .snapshot import format_cents

WIDTH = 800
HEIGHT = 484
MAX_COLORS = 128

LIGHT = {
    "outer": (246, 241, 231),
    "window": (255, 255, 255),
    "bar": (250, 247, 241),
    "border": (226, 218, 203),
    "text": (26, 26, 26),
    "dim": (107, 107, 107),
    "faint": (138, 138, 138),
    "add": (27, 122, 56),
    "del": (198, 40, 40),
    "mod": (138, 90, 0),
    "accent": (218, 41, 28),
    "gold": (255, 199, 44),
}
DARK = {
    "outer": (7, 8, 10),
    "window": (11, 12, 14),
    "bar": (13, 15, 19),
    "border": (33, 36, 42),
    "text": (230, 237, 243),
    "dim": (139, 148, 158),
    "faint": (110, 118, 129),
    "add": (63, 185, 80),
    "del": (248, 81, 73),
    "mod": (210, 153, 34),
    "accent": (218, 41, 28),
    "gold": (255, 199, 44),
}


class GifUnavailable(RuntimeError):
    """Raised when Pillow is missing."""


def _require_pillow() -> None:
    if Image is None:
        raise GifUnavailable("导出 GIF 需要 Pillow：pip install pillow")


def _font_candidates(bold: bool) -> list[str]:
    if sys.platform.startswith("win"):
        base = Path(r"C:\Windows\Fonts")
        return [
            str(base / ("msyhbd.ttc" if bold else "msyh.ttc")),
            str(base / "msyh.ttc"),
        ]
    return [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]


def _load_font(size: int, *, bold: bool = False):
    for candidate in _font_candidates(bold):
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


@dataclass
class Row:
    marker: str
    kind: str
    text: str
    price: str = ""
    label: str = ""

    @property
    def color_key(self) -> str:
        return {"added": "add", "removed": "del"}.get(self.kind, "mod")


def _rows_for(diff_changes: Sequence[Change], limit: int) -> list[Row]:
    """Prefer real changes (price/rename/…) then a few adds/removes."""
    order = {"price_up": 0, "price_down": 0, "renamed": 1, "tags": 1, "moved": 1, "removed": 2, "added": 3}
    ranked = sorted(diff_changes, key=lambda change: (order.get(change.kind, 9), change.code))
    rows: list[Row] = []
    for change in ranked[:limit]:
        marker = {"added": "+", "removed": "-"}.get(change.kind, "~")
        name = truncate(change.name, 22)
        if change.kind in ("price_up", "price_down"):
            before_price = format_cents((change.before or {}).get("price_cents"))
            after_price = format_cents((change.after or {}).get("price_cents"))
            price = f"{before_price} → {after_price}"
        else:
            price = format_cents((change.after or change.before or {}).get("price_cents"))
        rows.append(
            Row(
                marker=marker,
                kind=change.kind,
                text=pad(name, 24),
                price=price,
                label=KIND_LABELS.get(change.kind, change.kind),
            )
        )
    return rows


def _scene_frames(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    rows: Sequence[Row],
    summary: Mapping[str, int],
    *,
    palette: Mapping[str, tuple[int, int, int]],
    width: int,
) -> list[tuple[Any, int]]:
    _require_pillow()
    height = int(width * HEIGHT / WIDTH)
    scale = width / WIDTH
    r = lambda value: int(round(value * scale))  # noqa: E731 - tiny helper

    win = (r(22), r(18), r(WIDTH - 22), r(HEIGHT - 18))
    font_title = _load_font(r(15), bold=False)
    font_line = _load_font(r(17))
    font_small = _load_font(r(13))
    font_big = _load_font(r(46), bold=True)

    store = (after.get("store") or {}).get("storeName") or ""
    header = [
        f"--- {str(before.get('captured_at'))[5:16].replace('T', ' ')} {before.get('daypart_label', '')}",
        f"+++ {str(after.get('captured_at'))[5:16].replace('T', ' ')} {after.get('daypart_label', '')}",
        f"@@ {summary.get('total_before', 0)} → {summary.get('total_after', 0)} 个 SKU · "
        f"+{summary.get('added', 0)} -{summary.get('removed', 0)} ↑{summary.get('price_up', 0)} ↓{summary.get('price_down', 0)} @@",
    ]

    def render(lines: int, card: bool, cursor: bool):
        img = Image.new("RGB", (width, height), palette["outer"])
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, width, r(6)], fill=palette["gold"])
        draw.rectangle(win, fill=palette["window"], outline=palette["border"], width=max(1, r(1)))
        # title bar
        draw.rectangle([win[0], win[1], win[2], win[1] + r(34)], fill=palette["bar"])
        for index, color in enumerate(("#f26d63", "#f5bf4f", "#5bc454")):
            cx = win[0] + r(18) + index * r(16)
            draw.ellipse([cx, win[1] + r(14), cx + r(8), win[1] + r(22)], fill=color)
        draw.text((win[0] + r(72), win[1] + r(10)), f"mcdiff diff · {store}", font=font_title, fill=palette["dim"])

        if not card:
            y = win[1] + r(52)
            for line in header[:lines]:
                draw.text((win[0] + r(20), y), line, font=font_line, fill=palette["dim"])
                y += r(26)
            visible_rows = rows[: max(0, lines - len(header) - 1)]
            if lines > len(header):
                y += r(10)
            row_bottom = win[3] - r(48)
            for row in visible_rows:
                if y > row_bottom:
                    break
                color = palette[row.color_key]
                draw.text((win[0] + r(20), y), row.marker, font=font_line, fill=color)
                draw.text((win[0] + r(44), y), row.text, font=font_line, fill=color)
                draw.text((win[2] - r(210), y), row.price, font=font_line, fill=palette["dim"])
                draw.text((win[2] - r(24), y), row.label, font=font_line, fill=color, anchor="ra")
                y += r(26)
            if cursor:
                draw.rectangle([win[0] + r(20), y, win[0] + r(30), y + r(18)], fill=palette["accent"])
        else:
            cx = width // 2
            draw.text((cx, r(120)), "今天菜单的 diff", font=font_big, fill=palette["text"], anchor="ma")
            draw.text(
                (cx, r(190)),
                f"{summary.get('real', summary.get('changed', 0))} 处实质变化 · "
                f"{summary.get('rotation', 0)} 处餐段轮换",
                font=font_small,
                fill=palette["dim"],
                anchor="ma",
            )
            stats = [
                (f"{summary.get('added', 0)}", "新上架", palette["add"]),
                (f"{summary.get('removed', 0)}", "下架", palette["del"]),
                (f"{summary.get('price_up', 0)}", "涨价", palette["mod"]),
                (f"{summary.get('price_down', 0)}", "降价", palette["add"]),
            ]
            x = r(110)
            for value, label, color in stats:
                draw.text((x, r(250)), value, font=_load_font(r(56), bold=True), fill=color)
                draw.text((x, r(330)), label, font=font_small, fill=palette["dim"])
                x += r(160)
            draw.text((cx, r(410)), "McDiff · 用麦当劳 MCP 每天 commit 一次菜单", font=font_small, fill=palette["faint"], anchor="ma")

        return img

    frames: list[tuple[Any, int]] = []
    total_lines = len(header) + 1 + len(rows)
    for step in range(total_lines + 1):
        frames.append((render(step, False, True), 90))
    frames.append((render(total_lines, False, False), 400))
    frames.append((render(total_lines, True, False), 1400))
    return frames


def render_gif(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    out: str | Path,
    *,
    width: int = WIDTH,
    theme: str = "light",
    max_rows: int = 9,
    summary: Mapping[str, int] | None = None,
) -> Path:
    """Render ``before → after`` as a GIF and return the written path."""
    _require_pillow()
    width = max(320, min(int(width), 1600))
    max_rows = max(1, min(int(max_rows), 12))
    palette = LIGHT if theme == "light" else DARK
    diff = diff_snapshots(before, after)
    rows = _rows_for(diff.changes, max_rows)
    payload = summary or {
        **diff.summary,
        "real": diff.summary["changed"],
        "rotation": 0,
    }
    frames = _scene_frames(before, after, rows, payload, palette=palette, width=width)

    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    images = [frame for frame, _delay in frames]
    durations = [delay for _frame, delay in frames]
    quantized = [image.quantize(colors=MAX_COLORS) for image in images]
    quantized[0].save(
        path,
        save_all=True,
        append_images=quantized[1:],
        duration=durations,
        loop=0,
        optimize=True,
        default_image=False,
    )
    return path


def default_pair(series: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Pick the most story-worthy consecutive pair (same daypart wins)."""
    from .bucket import bucketize, museum_indexes

    if len(series) < 2:
        raise ValueError("至少需要两次快照才能导出 diff GIF")
    status, first_seen = museum_indexes(series)
    # group by store first: pairing store A with store B would report the whole menu as changed
    by_store: dict[str, list[Mapping[str, Any]]] = {}
    for snapshot in series:
        code = str((snapshot.get("store") or {}).get("storeCode") or "")
        by_store.setdefault(code, []).append(snapshot)
    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for group in by_store.values():
        group.sort(key=lambda snap: str(snap.get("captured_at")))
        pairs.extend(zip(group, group[1:]))

    best: tuple[tuple[int, int, str], Mapping[str, Any], Mapping[str, Any]] | None = None
    for before, after in pairs:
        diff = diff_snapshots(before, after)
        if not diff.changes:
            continue
        bucket = bucketize(before, after, diff, status=status, first_seen=first_seen)
        score = (1 if not bucket.cross_daypart else 0, len(bucket.real), str(after.get("captured_at")))
        if best is None or score > best[0]:
            best = (score, before, after)
    if best is None:
        raise ValueError("最近两次快照没有差异，无法导出 GIF")
    return best[1], best[2]


__all__ = ["GifUnavailable", "default_pair", "render_gif"]
