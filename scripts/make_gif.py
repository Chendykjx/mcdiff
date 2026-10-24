#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""McDiff —— README 首屏动图 + 社交分享图生成器。

用法::

    python scripts/make_gif.py

产物（只依赖 Pillow；不联网、不写别的文件；同样的数据 + 同样的 Pillow 版本下输出确定）::

    docs/media/demo.gif   800x484 · 100 帧 @ 70ms = 7.0s · 无限循环 · 目标 < 2 MB
    docs/media/og.png     1200x630 · 社交分享图 · 目标 < 400 KB

所有数字都来自 docs/data.json 里真实采集到的快照 diff，脚本只负责渲染：

* 第 1 幕（4.5s）：模拟终端里跑 ``mcdiff diff``，把真实的变化一行行敲出来；
* 第 2 幕（2.5s）：一句话结论卡——今天几个下架 / 几个涨价 / 几个降价。

配色取自 GitHub Dark，diff 的三种语义色贯穿两幕：

    bg #0b0c0e · text #e6edf3 · dim #8b949e
    新增 #3fb950 · 下架 #f85149 · 修改（涨价）#d29922 · 强调 #58a6ff

GIF 体积靠三件事压住：整个文件共用一份全局调色板、量化时不抖动（相邻帧才能只写差异
区域）、帧数控制在 100 帧。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
# 路径 & 体积上限
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "docs" / "data.json"
MEDIA_DIR = ROOT / "docs" / "media"
GIF_PATH = MEDIA_DIR / "demo.gif"
OG_PATH = MEDIA_DIR / "og.png"

GIF_LIMIT = 2_000_000
PNG_LIMIT = 400_000

# --------------------------------------------------------------------------- #
# 版式常量（两幕共用 800x484 画布）
# --------------------------------------------------------------------------- #

W, H = 800, 484
FRAMES_ACT1 = 64                   # 第 1 幕：终端打字
FRAMES_DIP = 2                     # 幕间：淡到黑
FRAMES_ACT2 = 34                   # 第 2 幕：结论卡
FRAME_MS = 70                      # 64 + 2 + 34 = 100 帧 × 70ms = 7.0s

WIN = (22, 18, 778, 466)           # 终端窗口
TITLE_H = 42
CONTENT_X = 46
CONTENT_R = 754
ROW_Y0 = 82
ROW_H = 26
BAR_Y = 428                        # 收尾进度条
STATUS_SEP_Y = 440

PREFIX_X = 46
NAME_X = 70
PRICE_R = 400
CHANGE_X = 418
DELTA_R = 596
TAG_R = 754

MAX_CHANGE_ROWS = 7

OG_W, OG_H = 1200, 630

# --------------------------------------------------------------------------- #
# 颜色
# --------------------------------------------------------------------------- #


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


# --- 白底 + 麦当劳经典配色（红 #DA291C / 金 #FFC72C）---
BG = _hex("ffffff")        # 终端正文底色 / 结论卡底色
OUTER = _hex("f6f1e7")     # 第一幕窗口外的画布（暖白，窗口才浮得起来）
SURF = _hex("faf7f1")      # 标题栏
SURF2 = _hex("f3eee4")     # 状态栏
BORDER = _hex("e2dacb")
TEXT = _hex("1a1a1a")
TEXT2 = _hex("333333")
DIM = _hex("6b6b6b")
DIM2 = _hex("8a8a8a")
GREEN = _hex("1b7a38")
GREEN_T = _hex("146b30")
RED = _hex("c62828")
RED_T = _hex("a61b1b")
YELLOW = _hex("8a5a00")
YELLOW_T = _hex("7a4f00")
BLUE = _hex("da291c")      # 品牌红：hunk / 光标
PURPLE = _hex("b07a00")
GOLD = _hex("ffc72c")      # 品牌金：装饰条

ROW_TINT = {
    "removed": _hex("fdecec"),
    "added": _hex("edf8f0"),
    "modified": _hex("fff7e3"),
}

DIFF_GRADIENT_STOPS = [
    (0.00, RED),
    (0.34, GOLD),
    (0.62, RED),
    (1.00, GOLD),
]

# --------------------------------------------------------------------------- #
# 字体
# --------------------------------------------------------------------------- #

FONT_CANDIDATES = {
    "sans": ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyh.ttf",
             "C:/Windows/Fonts/deng.ttf", "C:/Windows/Fonts/simhei.ttf"],
    "sansb": ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
              "C:/Windows/Fonts/dengb.ttf", "C:/Windows/Fonts/simhei.ttf"],
    "mono": ["C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf"],
    "monob": ["C:/Windows/Fonts/consolab.ttf", "C:/Windows/Fonts/consola.ttf"],
}

_FONTS: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
_FONT_USED: dict[str, str] = {}


def font(family: str, size: int) -> ImageFont.FreeTypeFont:
    """按 (family, size) 缓存字体对象；全部候选都找不到时给出可读报错。"""
    key = (family, size)
    cached = _FONTS.get(key)
    if cached is not None:
        return cached
    for candidate in FONT_CANDIDATES[family]:
        path = Path(candidate)
        if path.is_file():
            loaded = ImageFont.truetype(str(path), size, index=0)
            _FONTS[key] = loaded
            _FONT_USED.setdefault(family, str(path))
            return loaded
    raise RuntimeError(
        f"找不到 {family} 字体，尝试过: {', '.join(FONT_CANDIDATES[family])}\n"
        "本项目依赖 Windows 自带的 msyh.ttc / msyhbd.ttc / consola.ttf。"
    )


_PROBE = 128
_NOTDEF_CACHE: dict[int, bytes] = {}
_GLYPH_CACHE: dict[tuple[int, str], bool] = {}


def _glyph(fnt: ImageFont.FreeTypeFont, ch: str) -> bytes:
    probe = Image.new("L", (_PROBE, _PROBE), 0)
    ImageDraw.Draw(probe).text((16, 16), ch, font=fnt, fill=255)
    return probe.tobytes()


def has_glyph(fnt: ImageFont.FreeTypeFont, ch: str) -> bool:
    """这个字体到底有没有这个字形？（缺字时 Pillow 会画 .notdef 豆腐块）

    做法：把字符的位图和 U+E123（私用区，必然缺字）的位图比一比，结果缓存住。
    """
    if ch in " \t\n":
        return True
    key = (id(fnt), ch)
    hit = _GLYPH_CACHE.get(key)
    if hit is None:
        fid = id(fnt)
        notdef = _NOTDEF_CACHE.get(fid)
        if notdef is None:
            notdef = _glyph(fnt, "\ue123")
            _NOTDEF_CACHE[fid] = notdef
        hit = _glyph(fnt, ch) != notdef
        _GLYPH_CACHE[key] = hit
    return hit


def assert_glyphs() -> None:
    """开跑前先确认中文字体没选错、也没有缺字。"""
    samples = [
        ("sans", 16, "麦当劳菜单仓库深圳湾下架涨价降价今天结的每次追踪博物馆"),
        ("sansb", 16, "给麦当劳菜单建一个仓库今天结论"),
        ("mono", 15, "0123456789¥.+-%@~→↑↓·"),
        ("monob", 14, "0123456789mcdiff git@:→↑↓"),
    ]
    for family, size, sample in samples:
        fnt = font(family, size)
        missing = [ch for ch in sample if not has_glyph(fnt, ch)]
        if missing:
            raise RuntimeError(
                f"字体 {_FONT_USED.get(family)} 缺少字形「{''.join(missing)}」——"
                "画出来会是豆腐块，请检查 msyh.ttc 是否可用。"
            )


# --------------------------------------------------------------------------- #
# 文字绘制（一行里混排不同字体）
# --------------------------------------------------------------------------- #

Seg = tuple[str, ImageFont.FreeTypeFont, tuple[int, int, int]]


def S(text: str, fnt: ImageFont.FreeTypeFont, color: tuple[int, int, int]) -> Seg:
    return (text, fnt, color)


def segs_width(segs: list[Seg]) -> float:
    return sum(fnt.getlength(text) for text, fnt, _ in segs)


def draw_rich(draw: ImageDraw.ImageDraw, x: float, baseline: float,
              segs: list[Seg], align: str = "l") -> float:
    """沿基线画一串（文字, 字体, 颜色）；align: l / r / m。返回最右侧 x。

    每次落笔前逐字确认字形存在——宁可报错，也不让中文变成豆腐块。
    """
    width = segs_width(segs)
    if align == "r":
        x -= width
    elif align == "m":
        x -= width / 2
    for text, fnt, color in segs:
        for ch in text:
            if not has_glyph(fnt, ch):
                raise RuntimeError(
                    f"字体 {fnt.getname()[0]} {fnt.size}px 里没有「{ch}」"
                    f"（U+{ord(ch):04X}），会画成豆腐块。"
                )
        if text:
            draw.text((x, baseline), text, font=fnt, fill=color, anchor="ls")
            x += fnt.getlength(text)
    return x


def mix(color_a: tuple[int, int, int], color_b: tuple[int, int, int],
        t: float) -> tuple[int, int, int]:
    t = clamp01(t)
    return tuple(int(round(color_a[i] + (color_b[i] - color_a[i]) * t)) for i in range(3))


def clamp01(value: float) -> float:
    return 0.0 if value < 0 else (1.0 if value > 1 else value)


def ease_out(p: float) -> float:
    return 1.0 - (1.0 - clamp01(p)) ** 3


def gradient_line(width: int, height: int,
                  stops: list[tuple[float, tuple[int, int, int]]]) -> Image.Image:
    """水平渐变条（用在顶部装饰）。"""
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    for x in range(width):
        t = x / max(1, width - 1)
        color = stops[-1][1]
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                k = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
                color = tuple(int(round(c0[j] + (c1[j] - c0[j]) * k)) for j in range(3))
                break
        draw.line([(x, 0), (x, height - 1)], fill=color)
    return img


def gradient_strip(width: int, height: int,
                   stops: list[tuple[float, tuple[int, int, int]]],
                   fade: int = 150) -> Image.Image:
    """两端淡出的渐变条（RGBA），当装饰用不会显得像色带。"""
    strip = gradient_line(width, height, stops).convert("RGBA")
    mask = Image.new("L", (width, height), 255)
    mdraw = ImageDraw.Draw(mask)
    fade = max(1, min(fade, width // 2))
    for x in range(width):
        alpha = 255
        if x < fade:
            alpha = int(255 * (x / fade) ** 0.8)
        elif x >= width - fade:
            alpha = int(255 * ((width - 1 - x) / fade) ** 0.8)
        if alpha != 255:
            mdraw.line([(x, 0), (x, height - 1)], fill=alpha)
    strip.putalpha(mask)
    return strip


# 两幕共用的顶部装饰条（两端淡出，避免看起来像一条色带）
ACCENT_STRIP = gradient_strip(W, 3, DIFF_GRADIENT_STOPS)
OG_STRIP = gradient_strip(OG_W, 4, DIFF_GRADIENT_STOPS, fade=230)


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #


def money(cents: int | None) -> str:
    return "—" if cents is None else f"¥{cents / 100:.2f}"


def stamp(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16].replace("T", " ")


@dataclass
class Snapshot:
    """一份 diff 的展示层文案（数字都来自 data.json）。"""

    store_name: str
    store_code: str
    before_stamp: str
    after_stamp: str
    before_daypart: str
    after_daypart: str
    total_before: int
    total_after: int
    added: int
    removed: int
    price_up: int
    price_down: int
    changed: int
    snapshots: int
    skus: int
    gone: int
    changes: list[dict]
    picked: str = ""


def pick_head(diffs: list[dict]) -> tuple[dict, str]:
    """选出最能讲「今天谁涨价 / 谁降价 / 谁下架」的那个 diff。

    data.json 的 diffs[0] 是「变化最多的一对快照」，但菜单整点换档那种 diff
    （早餐 60 个 SKU 一起上架）变化最多却没什么故事。所以按下面的优先级挑：
    同一份里既有涨价又有降价 > 价格变化条数 > 下架条数 > 变化总数；
    如果全集里根本没有价格变化，就老实用 diffs[0]。
    """
    def kinds_of(diff: dict) -> Counter:
        return Counter((c or {}).get("kind") for c in diff.get("changes") or [])

    def score(diff: dict) -> tuple[int, int, int, int]:
        kinds = kinds_of(diff)
        up, down = kinds.get("price_up", 0), kinds.get("price_down", 0)
        return (1 if up and down else 0, up + down, kinds.get("removed", 0),
                int((diff.get("summary") or {}).get("changed") or 0))

    best = max(diffs, key=score)
    if score(best)[:2] == (0, 0):
        return diffs[0], "diffs[0]（全集没有价格变化，按「变化最多」取）"
    kinds = kinds_of(best)
    return best, (f"「{best.get('label', '')}」·{sum(kinds.values())} 处变化"
                  f"（↑{kinds.get('price_up', 0)} ↓{kinds.get('price_down', 0)} "
                  f"-{kinds.get('removed', 0)}）")


def load_snapshot() -> Snapshot:
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    diffs = raw.get("diffs") or []
    if not diffs:
        raise RuntimeError(f"{DATA_PATH} 里没有 diffs，无法生成动图")
    head, reason = pick_head(diffs)       # 讲「涨价/降价/下架」的那一组
    before = head.get("before") or {}
    after = head.get("after") or {}
    summary = head.get("summary") or {}
    totals = raw.get("totals") or {}
    store = before.get("store") or after.get("store") or {}
    museum = (raw.get("museum") or {}).get("stats") or {}
    return Snapshot(
        store_name=store.get("storeName") or "麦当劳门店",
        store_code=str(store.get("storeCode") or ""),
        before_stamp=stamp(before.get("captured_at")),
        after_stamp=stamp(after.get("captured_at")),
        before_daypart=before.get("daypart_label") or "",
        after_daypart=after.get("daypart_label") or "",
        total_before=int(summary.get("total_before") or 0),
        total_after=int(summary.get("total_after") or 0),
        added=int(summary.get("added") or 0),
        removed=int(summary.get("removed") or 0),
        price_up=int(summary.get("price_up") or 0),
        price_down=int(summary.get("price_down") or 0),
        changed=int(summary.get("changed") or len(head.get("changes") or [])),
        snapshots=int(totals.get("snapshots") or 0),
        skus=int(totals.get("skus_tracked") or museum.get("tracked") or 0),
        gone=int(totals.get("gone") or museum.get("gone") or 0),
        changes=list(head.get("changes") or []),
        picked=reason,
    )


# --------------------------------------------------------------------------- #
# 第 1 幕：终端 diff
# --------------------------------------------------------------------------- #

F_PREFIX = font("monob", 17)
F_NAME = font("sans", 16)
F_PRICE = font("mono", 15)
F_CHANGE = font("mono", 15)
F_CHANGE_ZH = font("sans", 14)
F_DELTA = font("monob", 14)
F_TAG = font("sans", 13)
F_HDR = font("mono", 15)
F_HDR_ZH = font("sans", 15)
F_ROW = font("sans", 14)
F_WIN_TITLE = font("monob", 14)
F_WIN_TITLE_ZH = font("sans", 14)
F_WIN_HINT = font("sans", 12)
F_STATUS = font("sans", 12)
F_STATUS_M = font("mono", 12)


@dataclass
class Line:
    """一行终端输出：prefix 立刻出现，其余字段按字符逐个「敲」出来。"""

    items: list[tuple[float, str, list[Seg]]]
    prefix: tuple[str, tuple[int, int, int]] | None = None
    instant: bool = False
    blank: bool = False
    tint: tuple[int, int, int] | None = None

    @property
    def total(self) -> int:
        return sum(len(text) for _, _, segs in self.items for text, _, _ in segs)


def _row(prefix: tuple[str, tuple[int, int, int]] | None,
         name: Seg | None,
         price: Seg | None = None,
         change: list[Seg] | None = None,
         delta: list[Seg] | None = None,
         tag: Seg | None = None,
         tint: tuple[int, int, int] | None = None) -> Line:
    items: list[tuple[float, str, list[Seg]]] = []
    if name is not None:
        items.append((NAME_X, "l", [name]))
    if price is not None:
        items.append((PRICE_R, "r", [price]))
    if change:
        items.append((CHANGE_X, "l", change))
    if delta:
        items.append((DELTA_R, "r", delta))
    if tag is not None:
        items.append((TAG_R, "r", [tag]))
    return Line(items=items, prefix=prefix, tint=tint)


def _change_row(change: dict) -> Line:
    kind = change.get("kind") or "modified"
    before = change.get("before") or {}
    after = change.get("after") or {}
    name = change.get("name") or before.get("name") or after.get("name") or "?"
    category = before.get("category") or after.get("category") or ""
    price_before = before.get("price_cents")
    price_after = after.get("price_cents")
    tag = S(category, F_TAG, DIM2) if category else None

    if kind == "removed":
        return _row(("-", RED), S(name, F_NAME, RED_T),
                    S(money(price_before), F_PRICE, DIM),
                    [S("下架", F_CHANGE_ZH, RED)], None, tag, ROW_TINT["removed"])

    if kind == "added":
        return _row(("+", GREEN), S(name, F_NAME, GREEN_T),
                    S(money(price_after), F_PRICE, DIM),
                    [S("上架", F_CHANGE_ZH, GREEN)], None, tag, ROW_TINT["added"])

    if kind in ("price_up", "price_down"):
        pct = change.get("delta_pct")
        if pct is None and price_before:
            pct = (price_after - price_before) / price_before * 100
        arrow = "↑" if kind == "price_up" else "↓"
        color = YELLOW if kind == "price_up" else GREEN
        return _row(("~", YELLOW), S(name, F_NAME, YELLOW),
                    S(money(price_before), F_PRICE, DIM),
                    [S("→ ", F_CHANGE, DIM2), S(money(price_after), F_CHANGE, TEXT)],
                    [S(f"{arrow} {float(pct or 0):+.1f}%", F_DELTA, color)],
                    tag, ROW_TINT["modified"])

    label = change.get("label") or "变化"
    return _row(("~", YELLOW), S(name, F_NAME, YELLOW),
                S(money(price_before), F_PRICE, DIM),
                [S(label, F_CHANGE_ZH, YELLOW_T)], None, tag, ROW_TINT["modified"])


def build_lines(snap: Snapshot) -> tuple[list[Line], int]:
    """拼出 13 行终端内容；返回 (行, 被省略的变化条数)。"""
    rows = [_change_row(c) for c in snap.changes]
    hidden = max(0, len(rows) - MAX_CHANGE_ROWS)
    rows = rows[:MAX_CHANGE_ROWS]

    lines = [
        Line(items=[(CONTENT_X, "l", [
            S("--- ", F_HDR, RED),
            S(f"{snap.before_stamp} ", F_HDR, DIM),
            S(snap.before_daypart, F_HDR_ZH, TEXT2),
        ])], instant=True),
        Line(items=[(CONTENT_X, "l", [
            S("+++ ", F_HDR, GREEN),
            S(f"{snap.after_stamp} ", F_HDR, DIM),
            S(snap.after_daypart, F_HDR_ZH, TEXT2),
        ])], instant=True),
        Line(items=[(CONTENT_X, "l", [
            S("@@ ", F_HDR, BLUE),
            S(f"{snap.total_before} → {snap.total_after} ", F_HDR, BLUE),
            S("个 SKU ", F_HDR_ZH, BLUE),
            S("· ", F_HDR, BLUE),
            S(f"+{snap.added}", F_HDR, GREEN),
            S(" ", F_HDR, BLUE),
            S(f"-{snap.removed}", F_HDR, RED),
            S(" ", F_HDR, BLUE),
            S(f"↑{snap.price_up}", F_HDR, YELLOW),
            S(" ", F_HDR, BLUE),
            S(f"↓{snap.price_down}", F_HDR, GREEN),
            S(" @@", F_HDR, BLUE),
        ])], instant=True),
        Line(items=[], blank=True),
    ]
    lines.extend(rows)
    lines.append(Line(items=[], blank=True))

    footer: list[Seg] = [
        S("→ ", F_ROW, PURPLE),
        S(f"{snap.removed} 件下架已入「麦门考古博物馆」", F_ROW, DIM),
        S(" · ", F_ROW, DIM2),
        S(f"{snap.snapshots} 次快照", F_ROW, DIM),
        S(" · ", F_ROW, DIM2),
        S(f"{snap.skus} 个 SKU 已追踪", F_ROW, DIM),
    ]
    if hidden:
        footer.insert(0, S(f"另有 {hidden} 处变化未显示 · ", F_ROW, DIM2))
    lines.append(Line(items=[(CONTENT_X, "l", footer)]))
    return lines, hidden


@dataclass
class Act1State:
    reveals: tuple[int, ...]
    cursor_line: int
    bar: float = 0.0
    done: bool = False
    cursor_on: bool = True


def simulate(lines: list[Line], step: int) -> list[Act1State]:
    """按「每帧多显示 step 个字符」推演一遍，得到逐帧状态。"""
    reveals = [0] * len(lines)
    states = [Act1State(tuple(reveals), 0)]
    for index, line in enumerate(lines):
        if line.blank:
            states.append(Act1State(tuple(reveals), index))
            continue
        if line.instant:
            reveals[index] = line.total
            states.append(Act1State(tuple(reveals), index))
            continue
        while reveals[index] < line.total:
            reveals[index] = min(line.total, reveals[index] + step)
            states.append(Act1State(tuple(reveals), index))
    return states


def build_act1_states(lines: list[Line]) -> tuple[list[Act1State], int]:
    """选一个能塞进 FRAMES_ACT1 的最慢打字速度，余下的帧留给收尾动画。"""
    tail = 4
    states: list[Act1State] = []
    step = 5
    for step in range(2, 40):
        candidate = simulate(lines, step)
        if len(candidate) <= FRAMES_ACT1 - tail:
            states = candidate
            break
    if not states:                        # 兜底：一次刷完
        step = 999
        states = [Act1State(tuple(line.total for line in lines), len(lines) - 1)]

    slack = FRAMES_ACT1 - len(states)
    if slack <= 0:
        return states[:FRAMES_ACT1], step
    out = list(states)
    last = out[-1]
    # 收尾：先把绿色的「提交完成」进度线拉满，剩下的帧让光标一闪一闪
    bar_frames = max(1, int(round(slack * 0.45)))
    for i in range(slack):
        progress = min(1.0, (i + 1) / bar_frames)
        blink = i - bar_frames + 1
        cursor_on = True if blink < 0 else (blink // 2) % 2 == 0
        out.append(Act1State(last.reveals, last.cursor_line, progress,
                             progress >= 1.0, cursor_on))
    return out, step


def build_chrome(snap: Snapshot) -> Image.Image:
    """终端窗口外壳：静态部分只画一次，之后每帧 copy。"""
    img = Image.new("RGB", (W, H), OUTER)
    draw = ImageDraw.Draw(img)

    # 内容区（#0b0c0e）
    draw.rounded_rectangle(WIN, radius=14, fill=BG)
    # 标题栏（上圆角，下沿平）
    draw.rounded_rectangle((WIN[0], WIN[1], WIN[2], WIN[1] + TITLE_H + 14),
                           radius=14, fill=SURF)
    draw.rectangle((WIN[0], WIN[1] + TITLE_H, WIN[2], WIN[1] + TITLE_H + 16), fill=BG)
    draw.line([(WIN[0] + 1, WIN[1] + TITLE_H), (WIN[2] - 1, WIN[1] + TITLE_H)], fill=BORDER)

    dot_y = WIN[1] + TITLE_H // 2
    for i, color in enumerate((_hex("ff5f57"), _hex("febc2e"), _hex("28c840"))):
        cx = WIN[0] + 24 + i * 18
        draw.ellipse((cx - 5, dot_y - 5, cx + 5, dot_y + 5), fill=color)

    draw_rich(draw, WIN[0] + 82, dot_y + 5, [
        S("mcdiff", F_WIN_TITLE, TEXT),
        S(" diff", F_WIN_TITLE, BLUE),
        S(f" · {snap.store_name}", F_WIN_TITLE_ZH, DIM),
    ])
    draw_rich(draw, WIN[2] - 18, dot_y + 5, [S("~/mcdiff", F_WIN_HINT, DIM2)], align="r")

    # 状态栏
    draw.line([(CONTENT_X, STATUS_SEP_Y), (CONTENT_R, STATUS_SEP_Y)], fill=BORDER)
    draw_rich(draw, CONTENT_X, STATUS_SEP_Y + 22, [
        S("mcdiff", F_STATUS_M, BLUE),
        S("  ·  每天 commit 一次菜单", F_STATUS, DIM2),
    ])
    draw_rich(draw, CONTENT_R, STATUS_SEP_Y + 22, [
        S(f"{snap.store_code} ", F_STATUS_M, DIM2),
        S(f"· {snap.snapshots} 次快照 · {snap.skus} 个 SKU", F_STATUS, DIM2),
    ], align="r")

    # 最后再描一遍窗口边框，边界最锐利
    draw.rounded_rectangle(WIN, radius=14, outline=BORDER, width=1)
    return img


def draw_line(draw: ImageDraw.ImageDraw, baseline: float, line: Line,
              reveal: int) -> float | None:
    """画一行已显示 reveal 个字符的内容；返回「正在生长的那个边」的 x。"""
    if reveal <= 0:
        return None
    if line.tint is not None:
        draw.rounded_rectangle((CONTENT_X - 8, baseline - 19, CONTENT_R + 6, baseline + 4),
                               radius=5, fill=line.tint)
    if line.prefix is not None:
        glyph, color = line.prefix
        draw.text((PREFIX_X, baseline), glyph, font=F_PREFIX, fill=color, anchor="ls")

    remaining = reveal
    edge: float | None = None
    for x, align, segs in line.items:
        if remaining <= 0:
            break
        visible: list[Seg] = []
        for text, fnt, color in segs:
            if remaining <= 0:
                break
            if remaining >= len(text):
                visible.append((text, fnt, color))
                remaining -= len(text)
            else:
                visible.append((text[:remaining], fnt, color))
                remaining = 0
        width = segs_width(visible)
        draw_rich(draw, x, baseline, visible, align=align)
        edge = x + width if align != "r" else x - width
    return edge


def render_act1(chrome: Image.Image, state: Act1State, lines: list[Line]) -> Image.Image:
    img = chrome.copy()
    draw = ImageDraw.Draw(img)

    cursor_x: float | None = None
    cursor_baseline = ROW_Y0 + 19
    for index, line in enumerate(lines):
        baseline = ROW_Y0 + index * ROW_H + 19
        reveal = state.reveals[index] if index < len(state.reveals) else 0
        edge = draw_line(draw, baseline, line, reveal)
        if edge is not None:
            cursor_x, cursor_baseline = edge, baseline
        elif index == state.cursor_line:
            cursor_x, cursor_baseline = CONTENT_X, baseline

    if state.bar > 0:                     # 收尾：一条绿色的完成线
        width = int((CONTENT_R - CONTENT_X) * state.bar)
        if width > 0:
            draw.rounded_rectangle((CONTENT_X, BAR_Y, CONTENT_X + width, BAR_Y + 2),
                                   radius=1, fill=GREEN if state.done else YELLOW)

    if cursor_x is not None and state.cursor_on:
        x = cursor_x + 3
        draw.rounded_rectangle((x, cursor_baseline - 13, x + 8, cursor_baseline + 3),
                               radius=2, fill=BLUE)
    return img


# --------------------------------------------------------------------------- #
# 第 2 幕：一句话结论卡
# --------------------------------------------------------------------------- #

F_H1 = font("sansb", 54)
F_H1_MONO = font("monob", 48)
F_SUB = font("sans", 19)
F_NUM = font("sansb", 72)
F_UNIT = font("sans", 19)
F_FOOT = font("sans", 16)
F_FOOT_MONO = font("mono", 16)

CARD_W, CARD_H = 214, 150
CARD_GAP = 22
CARD_Y = 212


def _composite(base: Image.Image, painter, alpha: float) -> Image.Image:
    """把 painter 画到透明层上，按 alpha 淡化后叠到 base（RGBA）。"""
    alpha = clamp01(alpha)
    if alpha <= 0.0:
        return base
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    painter(ImageDraw.Draw(layer))
    if alpha < 1.0:
        r, g, b, a = layer.split()
        a = a.point(lambda v: int(v * alpha))
        layer = Image.merge("RGBA", (r, g, b, a))
    return Image.alpha_composite(base, layer)


def _enter(frame: int, start: int, span: int = 6, distance: float = 14) -> tuple[float, float]:
    """入场：返回 (alpha, y 偏移)——淡入 + 上浮。"""
    p = ease_out((frame - start) / span)
    return p, (1.0 - p) * distance


def _cards(snap: Snapshot) -> list[tuple[str, str, tuple[int, int, int]]]:
    return [
        (str(snap.removed), "个下架", RED_T),
        (str(snap.price_up), "个涨价", YELLOW_T),
        (str(snap.price_down), "个降价", GREEN_T),
    ]


def render_act2(snap: Snapshot, frame: int) -> Image.Image:
    base = Image.new("RGBA", (W, H), BG + (255,))
    base.alpha_composite(ACCENT_STRIP, (0, 0))

    def title(dy: float):
        def painter(draw: ImageDraw.ImageDraw) -> None:
            draw_rich(draw, W / 2, 136 + dy, [
                S("今天菜单的 ", F_H1, TEXT + (255,)),
                S("diff", F_H1_MONO, BLUE + (255,)),
            ], align="m")
        return painter

    def subtitle(dy: float):
        def painter(draw: ImageDraw.ImageDraw) -> None:
            draw_rich(draw, W / 2, 180 + dy, [
                S(f"{snap.after_stamp} ", F_SUB, DIM + (255,)),
                S("· ", F_SUB, DIM2 + (255,)),
                S(snap.store_name, F_SUB, DIM + (255,)),
                S(" · ", F_SUB, DIM2 + (255,)),
                S(f"{snap.total_before} → {snap.total_after} 个 SKU", F_SUB, DIM + (255,)),
            ], align="m")
        return painter

    cards = _cards(snap)
    x0_all = (W - (CARD_W * 3 + CARD_GAP * 2)) / 2

    def card(index: int, dy: float, value: str):
        number, unit, color = cards[index]
        cx0 = x0_all + index * (CARD_W + CARD_GAP)
        fill = mix(SURF2, color, 0.05) + (255,)
        outline = mix(BORDER, color, 0.32) + (255,)

        def painter(draw: ImageDraw.ImageDraw) -> None:
            top = CARD_Y + dy
            draw.rounded_rectangle((cx0, top, cx0 + CARD_W, top + CARD_H),
                                   radius=18, fill=fill, outline=outline, width=1)
            draw_rich(draw, cx0 + CARD_W / 2, top + 98,
                      [S(value, F_NUM, color + (255,))], align="m")
            draw_rich(draw, cx0 + CARD_W / 2, top + 132,
                      [S(unit, F_UNIT, DIM + (255,))], align="m")
        return painter

    def footer(dy: float):
        def painter(draw: ImageDraw.ImageDraw) -> None:
            draw_rich(draw, W / 2, 424 + dy, [
                S("McDiff", F_FOOT_MONO, DIM + (255,)),
                S("  ·  用麦当劳 MCP 每天 commit 一次菜单", F_FOOT, DIM2 + (255,)),
            ], align="m")
        return painter

    alpha, dy = _enter(frame, 0)
    base = _composite(base, title(dy), alpha)
    alpha, dy = _enter(frame, 3)
    base = _composite(base, subtitle(dy), alpha)

    for index in range(3):
        start = 6 + index * 3
        alpha, dy = _enter(frame, start)
        target = int(cards[index][0])
        value = str(int(round(target * ease_out((frame - start) / 7.0))))
        base = _composite(base, card(index, dy, value), alpha)

    alpha, dy = _enter(frame, 16)
    base = _composite(base, footer(dy), alpha)
    return base.convert("RGB")


# --------------------------------------------------------------------------- #
# 社交分享图 1200x630
# --------------------------------------------------------------------------- #

F_OG_EYEBROW_MONO = font("monob", 21)
F_OG_H1 = font("sansb", 55)
F_OG_H1_MONO = font("monob", 48)
F_OG_SUB = font("sans", 25)
F_OG_PILL = font("sans", 20)
F_OG_PILL_MONO = font("monob", 20)
F_OG_FOOT = font("sans", 19)
F_OG_FOOT_MONO = font("mono", 19)
F_OG_MINI = font("mono", 13)


def draw_og_decoration(draw: ImageDraw.ImageDraw, x0: int, y0: int) -> None:
    """左侧装饰：一块简化的 git diff（纯色块，不写具体文案）。"""
    card_w, card_h = 356, 330
    draw.rounded_rectangle((x0, y0, x0 + card_w, y0 + card_h), radius=16,
                           fill=SURF, outline=BORDER, width=1)
    draw.rounded_rectangle((x0, y0, x0 + card_w, y0 + 36), radius=16, fill=_hex("14171d"))
    draw.rectangle((x0, y0 + 20, x0 + card_w, y0 + 36), fill=_hex("14171d"))
    draw.line([(x0, y0 + 37), (x0 + card_w, y0 + 37)], fill=BORDER)
    for i, color in enumerate((_hex("ff5f57"), _hex("febc2e"), _hex("28c840"))):
        cx = x0 + 18 + i * 15
        draw.ellipse((cx - 4, y0 + 14, cx + 4, y0 + 22), fill=color)
    draw_rich(draw, x0 + 66, y0 + 25, [S("mcdiff diff", F_OG_MINI, DIM2)])

    # (标记, 标记色, 条长比例, 条色, 是否有右侧「价格列」)
    rows = [
        ("---", RED, 0.46, _hex("3a2023"), False),
        ("+++", GREEN, 0.66, _hex("1c3524"), False),
        ("@@", BLUE, 0.90, _hex("1b3f5c"), False),
        ("-", RED, 0.62, _hex("4a2224"), True),
        ("-", RED, 0.44, _hex("4a2224"), True),
        ("~", YELLOW, 0.55, _hex("4a3a15"), True),
        ("~", YELLOW, 0.38, _hex("4a3a15"), True),
        ("+", GREEN, 0.30, _hex("1f4028"), True),
        ("@@", BLUE, 0.62, _hex("1b3f5c"), False),
    ]
    y = y0 + 58
    for marker, color, ratio, bar_color, price_col in rows:
        draw_rich(draw, x0 + 18, y + 4, [S(marker, F_OG_MINI, color)])
        draw.rounded_rectangle((x0 + 52, y - 4, x0 + 52 + int((card_w - 200) * ratio), y + 4),
                               radius=4, fill=bar_color)
        if price_col:
            draw.rounded_rectangle((x0 + card_w - 96, y - 3, x0 + card_w - 24, y + 3),
                                   radius=3, fill=_hex("252a31"))
        y += 30


def render_og(snap: Snapshot) -> Image.Image:
    img = Image.new("RGB", (OG_W, OG_H), BG)
    draw = ImageDraw.Draw(img)

    # 右上角一团极淡的蓝光，避免大片死黑
    glow = Image.new("L", (OG_W, OG_H), 0)
    gdraw = ImageDraw.Draw(glow)
    for radius in range(560, 0, -10):
        gdraw.ellipse((OG_W - 150 - radius, -160 - radius,
                       OG_W - 150 + radius, -160 + radius),
                      fill=int(9 * (1 - radius / 560)))
    img.paste(Image.new("RGB", (OG_W, OG_H), BLUE), (0, 0), glow)

    img.paste(OG_STRIP, (0, 0), OG_STRIP)
    draw_og_decoration(draw, 64, 150)

    text_x = 470
    draw_rich(draw, text_x, 214, [
        S("$ ", F_OG_EYEBROW_MONO, GREEN),
        S("mcdiff", F_OG_EYEBROW_MONO, TEXT2),
        S(" diff --today", F_OG_EYEBROW_MONO, DIM2),
    ])
    draw_rich(draw, text_x, 292, [S("给麦当劳菜单", F_OG_H1, TEXT)])
    draw_rich(draw, text_x, 368, [
        S("建一个 ", F_OG_H1, TEXT),
        S("git", F_OG_H1_MONO, BLUE),
        S(" 仓库", F_OG_H1, TEXT),
    ])
    draw_rich(draw, text_x, 424, [S("每天 commit 一次，diff 出涨价与下架", F_OG_SUB, DIM)])

    pills = [
        [S("快照 ", F_OG_PILL, DIM), S(str(snap.snapshots), F_OG_PILL_MONO, TEXT), S(" 次", F_OG_PILL, DIM)],
        [S("SKU ", F_OG_PILL, DIM), S(str(snap.skus), F_OG_PILL_MONO, TEXT), S(" 个", F_OG_PILL, DIM)],
        [S("已下架 ", F_OG_PILL, DIM), S(str(snap.gone), F_OG_PILL_MONO, RED_T), S(" 个", F_OG_PILL, DIM)],
    ]
    px = text_x
    for segs in pills:
        width = segs_width(segs)
        draw.rounded_rectangle((px, 462, px + width + 36, 512), radius=25,
                               fill=SURF2, outline=BORDER, width=1)
        draw_rich(draw, px + 18, 495, segs)
        px += width + 36 + 14

    draw.ellipse((64, 556, 74, 566), fill=GREEN)
    draw_rich(draw, 84, 568, [
        S("McDiff", F_OG_FOOT_MONO, TEXT2),
        S("  ·  麦当劳 MCP 参赛作品", F_OG_FOOT, DIM2),
    ])
    draw_rich(draw, OG_W - 64, 568,
              [S(f"{snap.total_before} → {snap.total_after} SKUs", F_OG_FOOT_MONO, DIM2)],
              align="r")
    return img


# --------------------------------------------------------------------------- #
# GIF 编码 & 自检
# --------------------------------------------------------------------------- #


def palette_source(frames: list[Image.Image]) -> Image.Image:
    """抽样若干帧竖着拼起来，用来算一份全局调色板。"""
    marks = [0, FRAMES_ACT1 // 3, FRAMES_ACT1 // 2, FRAMES_ACT1 - 1,
             FRAMES_ACT1 + 8, FRAMES_ACT1 + 16, len(frames) - 1]
    picks = sorted({min(max(0, m), len(frames) - 1) for m in marks})
    stack = Image.new("RGB", (W, H * len(picks)))
    for i, index in enumerate(picks):
        stack.paste(frames[index], (0, i * H))
    return stack


def save_gif(frames: list[Image.Image], path: Path) -> dict:
    """所有帧共用一份调色板 + 关闭抖动：相邻帧只有变化区域会被重写，体积才压得住。

    调色板从 192 色往下试（本片实测 192 色也只有 ~190 KB，远低于 2 MB 上限，
    所以优先保证文字抗锯齿和渐变的质量；真超了再逐档降）。
    """
    source = palette_source(frames)
    last_size = 0
    for colors in (192, 144, 112, 96, 64, 48, 32):
        palette_img = source.quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                                      dither=Image.Dither.NONE)
        palette_bytes = bytes(palette_img.getpalette())
        if len(palette_bytes) < 768:
            palette_bytes += bytes(768 - len(palette_bytes))
        indexed = [frame.quantize(palette=palette_img, dither=Image.Dither.NONE)
                   for frame in frames]
        indexed[0].save(path, format="GIF", save_all=True, append_images=indexed[1:],
                        duration=FRAME_MS, loop=0, optimize=True, palette=palette_bytes)
        last_size = path.stat().st_size
        if last_size < GIF_LIMIT:
            return {"colors": colors, "bytes": last_size}
    raise RuntimeError(f"GIF 压不进 {GIF_LIMIT:,} 字节（最后一次 {last_size:,} 字节）")


def inspect_gif(path: Path, frames: list[Image.Image]) -> dict:
    """用 Pillow 重新打开产物，检查帧数 / 尺寸 / 循环 / 解码是否正确。

    GIF 里完全相同的相邻帧会被合并（时长累加），所以按「时间轴」而不是帧号去
    对源帧：文件里第 k 帧的起始时刻换算成源帧号，再比对像素。
    """
    with Image.open(path) as im:
        stored = getattr(im, "n_frames", 1)
        size = im.size
        loop = im.info.get("loop")
        starts: list[int] = []
        clock = 0
        for index in range(stored):
            im.seek(index)
            starts.append(clock)
            clock += im.info.get("duration", FRAME_MS)
        total_ms = clock
        probes = sorted({0, stored // 3, (2 * stored) // 3, stored - 1})
        diffs: list[tuple[int, float]] = []
        for index in probes:
            im.seek(index)
            got = im.convert("RGB")
            src_index = min(len(frames) - 1, starts[index] // FRAME_MS)
            want = frames[src_index]
            delta = ImageChops.difference(got, want).convert("L")
            hist = delta.histogram()
            count = sum(hist) or 1
            diffs.append((index, round(sum(i * n for i, n in enumerate(hist)) / count, 2)))
    return {
        "frames": stored,
        "size": size,
        "loop": loop,
        "duration_ms": total_ms,
        "mean_diff": diffs,
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def build_frames(snap: Snapshot) -> tuple[list[Image.Image], int]:
    lines, _ = build_lines(snap)
    states, step = build_act1_states(lines)
    chrome = build_chrome(snap)
    frames = [render_act1(chrome, state, lines) for state in states]
    # 开头 3 帧从全黑淡入，循环播放时从结论卡切回终端不会太硬
    black = Image.new("RGB", (W, H), _hex("000000"))
    for index, alpha in enumerate((0.35, 0.70, 0.95)):
        frames[index] = Image.blend(black, frames[index], alpha)
    frozen = frames[-1]
    # 幕间 2 帧淡到黑，避免两幕文字叠在一起糊成一片
    frames.append(Image.blend(frozen, black, 0.62))
    frames.append(Image.blend(frozen, black, 0.90))
    for index in range(FRAMES_ACT2):
        frames.append(render_act2(snap, index))
    return frames, step


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                  # 老终端不支持也不影响生成
        pass

    if not DATA_PATH.is_file():
        print(f"[error] 找不到数据文件 {DATA_PATH}")
        return 2

    assert_glyphs()
    snap = load_snapshot()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    print("McDiff 媒体生成器")
    print(f"   数据文件  {DATA_PATH}")
    print("   字体      " + " · ".join(f"{k}={v}" for k, v in sorted(_FONT_USED.items())))
    print(f"   采集窗口  {snap.before_stamp} {snap.before_daypart} → "
          f"{snap.after_stamp} {snap.after_daypart}")
    print(f"   选用 diff {snap.picked}")
    print(f"   变化      +{snap.added} 上架 / -{snap.removed} 下架 / "
          f"↑{snap.price_up} 涨价 / ↓{snap.price_down} 降价")

    frames, cast_speed = build_frames(snap)
    print(f"\n[1/2] 渲染 {len(frames)} 帧（{FRAMES_ACT1} 帧终端 @ {cast_speed} 字/帧 + "
          f"{FRAMES_ACT2} 帧结论卡，{FRAME_MS}ms/帧）→ 编码 GIF")
    gif_info = save_gif(frames, GIF_PATH)
    gif_check = inspect_gif(GIF_PATH, frames)

    print("[2/2] 渲染 1200x630 分享图 → PNG")
    render_og(snap).save(OG_PATH, format="PNG", optimize=True)

    gif_bytes = GIF_PATH.stat().st_size
    og_bytes = OG_PATH.stat().st_size
    total_ms = gif_check["duration_ms"]

    print("\n" + "─" * 72)
    print(f"demo.gif  {GIF_PATH}")
    print(f"           {gif_check['size'][0]}x{gif_check['size'][1]} · "
          f"渲染 {len(frames)} 帧 / 文件内 {gif_check['frames']} 帧 · "
          f"{total_ms / 1000:.2f}s · loop={gif_check['loop']} · "
          f"{gif_info['colors']} 色调色板")
    print(f"           {gif_bytes:,} 字节（{gif_bytes / 1024:.0f} KB），"
          f"上限 {GIF_LIMIT:,} · 解码抽查(帧,平均像素差) {gif_check['mean_diff']}")
    print(f"og.png    {OG_PATH}")
    print(f"           1200x630 · {og_bytes:,} 字节（{og_bytes / 1024:.0f} KB），"
          f"上限 {PNG_LIMIT:,}")
    print("─" * 72)

    ok = True
    if gif_bytes >= GIF_LIMIT:
        print(f"[FAIL] demo.gif {gif_bytes:,} 字节 ≥ {GIF_LIMIT:,}")
        ok = False
    if og_bytes >= PNG_LIMIT:
        print(f"[FAIL] og.png {og_bytes:,} 字节 ≥ {PNG_LIMIT:,}")
        ok = False
    if not 720 <= gif_check["size"][0] <= 800:
        print(f"[FAIL] demo.gif 宽度 {gif_check['size'][0]} 不在 720~800")
        ok = False
    if gif_check["frames"] <= 60:
        print(f"[FAIL] demo.gif 帧数 {gif_check['frames']} ≤ 60")
        ok = False
    if not 5000 <= total_ms <= 8000:
        print(f"[FAIL] demo.gif 时长 {total_ms / 1000:.2f}s 不在 5~8s")
        ok = False
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
