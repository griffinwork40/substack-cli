#!/usr/bin/env python3
"""
The Goblin Files — cover art, v2 "Pixel Goblin" direction (standalone).

Bold 8-bit MASCOT system: the pixel goblin (from the logo) is the recurring hero
on every cover, wearing a per-post PROP, on a per-post color "flag" with a solid
coverline bar + a thin teal accent, and a grain texture pass.

Deterministic: palette-indexed sprite grids -> solid SVG rects -> rsvg-convert
-> ImageMagick. Local only; nothing is uploaded.

Usage:
  python3 pixel_studio.py goblin               # master sprite (QA)
  python3 pixel_studio.py race night           # chosen slugs
  python3 pixel_studio.py                       # all 6 + contact + mobile sheets
"""
from __future__ import annotations
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

W, H = 2400, 1600
CX = W / 2
SAFE = 1360
SX0, SY0 = (W - SAFE) / 2, (H - SAFE) / 2
BAR_Y = 1300
OUT = Path(__file__).parent / "out" / "pixel"
FONT_MONO = "Geist Mono"
LABEL_FONT = "/Users/griffinlong/Library/Fonts/CaskaydiaCoveNerdFont-Regular.ttf"

PAL = {
    ".": None,
    "d": "#17401d", "g": "#48a83e", "l": "#61bf4b",   # goblin greens
    "k": "#0e1c24", "e": "#17BEBB", "w": "#f2ead4",   # visor / eye / teeth
    "y": "#f2c24e", "o": "#c1852c",                    # gold headphones
    "r": "#e0483a", "s": "#c9d2dc", "p": "#ff7a59",   # red / steel / coral
    "b": "#3a2c1a",                                    # brown (palm trunk)
}

GW = 25


def _R(*segs):
    s = "".join(ch * n for ch, n in segs)
    assert len(s) == GW, f"row width {len(s)} != {GW}: {s!r}"
    return s


GOBLIN = [
    _R((".", 10), ("y", 5), (".", 10)),                                  # 0 band apex
    _R((".", 8), ("y", 2), (".", 5), ("y", 2), (".", 8)),                # 1 band
    _R((".", 6), ("y", 2), (".", 9), ("y", 2), (".", 6)),                # 2 band
    _R((".", 4), ("y", 2), (".", 2), ("g", 2), (".", 5), ("g", 2), (".", 2), ("y", 2), (".", 4)),  # 3 ear nubs
    _R((".", 4), ("d", 1), ("g", 15), ("d", 1), (".", 4)),               # 4 head top
    _R((".", 2), ("y", 2), ("d", 1), ("g", 15), ("d", 1), ("y", 2), (".", 2)),  # 5 cups
    _R((".", 2), ("y", 2), ("d", 1), ("g", 15), ("d", 1), ("y", 2), (".", 2)),  # 6 cups
    _R((".", 2), ("o", 2), ("d", 1), ("g", 1), ("l", 1), ("g", 11), ("l", 1), ("g", 1), ("d", 1), ("o", 2), (".", 2)),  # 7 cheeks
    _R((".", 4), ("d", 1), ("k", 15), ("d", 1), (".", 4)),               # 8 visor top
    _R((".", 4), ("d", 1), ("k", 2), ("e", 2), ("k", 7), ("e", 2), ("k", 2), ("d", 1), (".", 4)),  # 9 eyes
    _R((".", 4), ("d", 1), ("k", 15), ("d", 1), (".", 4)),               # 10 visor bot
    _R((".", 4), ("d", 1), ("g", 15), ("d", 1), (".", 4)),               # 11
    _R((".", 4), ("d", 1), ("g", 6), ("l", 3), ("g", 6), ("d", 1), (".", 4)),  # 12 nose
    _R((".", 4), ("d", 1), ("k", 15), ("d", 1), (".", 4)),               # 13 mouth top
    _R((".", 4), ("d", 1), ("k", 2), ("w", 2), ("k", 1), ("w", 2), ("k", 1), ("w", 2), ("k", 1), ("w", 2), ("k", 2), ("d", 1), (".", 4)),  # 14 teeth
    _R((".", 4), ("d", 1), ("k", 15), ("d", 1), (".", 4)),               # 15 mouth bot
    _R((".", 4), ("d", 1), ("g", 15), ("d", 1), (".", 4)),               # 16
    _R((".", 4), ("d", 1), ("g", 15), ("d", 1), (".", 4)),               # 17
    _R((".", 5), ("d", 1), ("g", 13), ("d", 1), (".", 5)),               # 18 jaw
    _R((".", 6), ("d", 13), (".", 6)),                                   # 19 bottom
]

# prop sprites live in sprites.py (this file is at its size ceiling).
# imported by name so `from pixel_studio import FILECAB` keeps working.
from sprites import (FLAGPOLE, MAGNIFIER, BRIEFCASE, HOUSE, PUZZLE, PALM,
                     FILECAB, FOLDER, RECEIPT, ROBOT, CRATE, WRENCH,
                     WORKBENCH, HAMMER)


def shadow_pal(dark: str, rim: str, glow: str) -> dict:
    """Three-tone silhouette for a crowd-size goblin (body/rim/visor-glow)."""
    ramp = {"y": rim, "o": rim, "e": glow, "w": glow}
    return {ch: (None if v is None else ramp.get(ch, dark)) for ch, v in PAL.items()}


# ---------------------------------------------------------------------------
# Sprite engine
# ---------------------------------------------------------------------------
def _pad(rows):
    w = max(len(r) for r in rows)
    return [r.ljust(w, ".") for r in rows], w


def sprite(rows, px, ox, oy, pal=PAL):
    rows, w = _pad(rows)
    out = ['<g shape-rendering="crispEdges">']
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            col = pal.get(ch)
            if col:
                out.append(f'<rect x="{ox+i*px:.1f}" y="{oy+j*px:.1f}" '
                           f'width="{px+0.6:.1f}" height="{px+0.6:.1f}" fill="{col}"/>')
    out.append("</g>")
    return "".join(out)


def dims(rows, px):
    rows, w = _pad(rows)
    return w * px, len(rows) * px


# ---------------------------------------------------------------------------
# Flags + chrome
# ---------------------------------------------------------------------------
@dataclass
class Flag:
    sky: str
    sky2: str
    bar: str
    accent: str = "#17BEBB"
    ink: str = "#F7F0E2"


FLAGS = {
    "violet": Flag("#4a1e78", "#7c3170", "#180a26"),
    "amber":  Flag("#b0691a", "#dc9a30", "#2a1706"),
    "night":  Flag("#131e45", "#26356a", "#070b1e"),
    "teal":   Flag("#0e6b62", "#18a89b", "#052321", accent="#ff7a59"),
    "crimson": Flag("#6d1a1a", "#9c3420", "#230a0a"),
    "coral":  Flag("#c8503f", "#ef9a55", "#2a0f10"),
    "steel":  Flag("#2c4a5e", "#4f82a0", "#0f1820"),
    # post 8: the only primary the family was missing. Gold accent, not teal,
    # so the ledger reads warm against green instead of blending into it.
    "moss":   Flag("#14532d", "#3f9b52", "#06180d", accent="#f2c24e"),
    # post 9: hot magenta. Teal accent against pink, distinct from violet's plum.
    "neon":   Flag("#7a1246", "#e0559a", "#220714"),
    # post 10: electric indigo. Bright/saturated, reads "machine" at thumbnail.
    "forge":  Flag("#1a2980", "#3d5af1", "#0a1133"),
    # post 11: warm copper. Reads "workshop / craft / wood" at thumbnail.
    "copper": Flag("#7a4419", "#c47b38", "#2a1506"),
}


def flag_bg(f: Flag, slug: str) -> str:
    return (
        f'<defs><linearGradient id="sky_{slug}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{f.sky}"/>'
        f'<stop offset="100%" stop-color="{f.sky2}"/></linearGradient></defs>'
        f'<rect width="{W}" height="{H}" fill="url(#sky_{slug})"/>'
        f'<rect x="0" y="{BAR_Y-10}" width="{W}" height="10" fill="{f.accent}"/>'
        f'<rect x="0" y="{BAR_Y}" width="{W}" height="{H-BAR_Y}" fill="{f.bar}"/>'
    )


def wordmark(f: Flag) -> str:
    return (f'<text x="{SX0+6}" y="{SY0+58}" font-family="{FONT_MONO}" '
            f'font-weight="bold" font-size="46" letter-spacing="3" '
            f'fill="{f.ink}" opacity="0.92">THE GOBLIN FILES</text>')


def kicker(f: Flag, text: str) -> str:
    return (f'<text x="{CX}" y="1466" text-anchor="middle" font-family="{FONT_MONO}" '
            f'font-weight="bold" font-size="72" letter-spacing="1" '
            f'fill="{f.ink}">{text}</text>')


def sun(cx, cy, r, col="#ffd98a") -> str:
    return (f'<circle cx="{cx}" cy="{cy}" r="{r*1.7:.0f}" fill="{col}" opacity="0.22"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r:.0f}" fill="{col}"/>')


def moon(cx, cy, r, bg) -> str:
    return (f'<circle cx="{cx}" cy="{cy}" r="{r*1.6:.0f}" fill="#e6ecfa" opacity="0.18"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r:.0f}" fill="#e6ecfa"/>'
            f'<circle cx="{cx+r*0.42:.0f}" cy="{cy-r*0.3:.0f}" r="{r*0.92:.0f}" fill="{bg}"/>')


def stars(n, seed, ymax=1180) -> str:
    random.seed(seed)
    out = []
    for _ in range(n):
        x, y = random.uniform(120, W-120), random.uniform(140, ymax)
        out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="6" height="6" '
                   f'fill="#eaf0ff" opacity="{random.uniform(.3,.9):.2f}"/>')
    return "".join(out)


# ---------------------------------------------------------------------------
# Specs + scenes
# ---------------------------------------------------------------------------
@dataclass
class Spec:
    slug: str
    flag: str
    kicker: str


SPECS = [
    Spec("race", "violet", "THE DEFAULT CAME LAST"),
    Spec("company", "amber", "ONE GOBLIN, WHOLE ORG"),
    Spec("night", "night", "IT RUNS WHILE I SLEEP"),
    Spec("gap", "teal", "WHERE FRAMEWORKS STOP"),
    Spec("audit", "crimson", "IT HAD BEEN LYING TO ME"),
    Spec("field", "coral", "ONE-MAN AI LAB"),
    Spec("memory", "steel", "IT HAD AMNESIA"),
    Spec("ledger", "moss", "THE HELP WASN'T FREE"),
    Spec("bots", "neon", "THE ROBOTS LIKED IT"),
    Spec("rsi", "forge", "IT WROTE ITS OWN FIX"),
    Spec("infra", "copper", "MARKET SIZE: ONE"),
]

GPX = 40  # goblin pixel size on covers


def scene(spec: Spec, f: Flag) -> str:
    gw, gh = dims(GOBLIN, GPX)                 # 1000 x 800
    ox, oy = CX - gw/2, 290                    # 700..1700 ; bottom 1090
    pre, post = [], [sprite(GOBLIN, GPX, ox, oy)]
    if spec.slug == "race":
        base = 1086
        for i, (h, c) in enumerate([(210, "#17BEBB"), (210, "#17BEBB"), (150, "#d98099")]):
            bx = 536 + i * 56
            post.append(f'<rect x="{bx}" y="{base-h}" width="40" height="{h}" fill="{c}"/>')
            post.append(f'<rect x="{bx}" y="{base-h}" width="40" height="12" fill="#fff" opacity="0.35"/>')
        post.append(sprite(FLAGPOLE, 26, 1560, 560))
    elif spec.slug == "company":
        post.append(sprite(HOUSE, 30, 524, 560))            # house (home), left
        post.append(sprite(BRIEFCASE, 30, 1558, 566))       # briefcase (company), right
    elif spec.slug == "night":
        pre.append(stars(46, 3))
        pre.append(moon(1650, 470, 96, f.sky))
        post.append(f'<text x="600" y="520" font-family="{FONT_MONO}" font-weight="bold" '
                    f'font-size="120" fill="#e6ecfa" opacity="0.9">z z</text>')
    elif spec.slug == "gap":
        post.append(sprite(PUZZLE, 34, 1560, 560))          # coral puzzle piece
        # faint "missing slot" to the left
        post.append(f'<rect x="556" y="760" width="150" height="150" fill="none" '
                    f'stroke="{f.accent}" stroke-width="8" stroke-dasharray="20 16"/>')
    elif spec.slug == "audit":
        post.append(sprite(MAGNIFIER, 42, 1520, 560))
        post.append(sprite(["drrrd", "rkrkr", "rrrrr", "r.r.r"], 34, 600, 900))
    elif spec.slug == "field":
        pre.append(sun(1660, 540, 120))
        post.append(sprite(PALM, 30, 520, 760))
        post.append(sprite(PALM, 26, 1720, 800))
    elif spec.slug == "memory":
        post.append(sprite(FILECAB, 30, 1580, 556))   # filing cabinet (the files), right
        post.append(sprite(FOLDER, 30, 486, 812))     # manila folder, lower-left
    elif spec.slug == "ledger":
        # dim subagents on the horizon; pre so the hero occludes the middle ones.
        shp = shadow_pal("#0b3319", "#1d6b3a", "#17BEBB")
        for i in range(5):
            pre.append(sprite(GOBLIN, 12, 540 + i * 258, 1006, pal=shp))
        post.append(sprite(RECEIPT, 24, 1660, 620))   # the itemised bill, right
    elif spec.slug == "bots":
        # scanner queue on the horizon, each leaving with a copy of the package.
        for i in range(4):
            x = 570 + i * 330
            pre.append(sprite(ROBOT, 24, x, 1130))
            pre.append(sprite(CRATE, 13, x + 132, 1158))
        post.append(sprite(CRATE, 32, 1590, 620))     # the package itself, right
    elif spec.slug == "rsi":
        # the goblin watching a smaller version of itself (the pattern it noticed)
        # and a wrench on the right (the tool it built for itself).
        pre.append(stars(25, 42))
        shp = shadow_pal("#0f1d4a", "#2840a8", "#17BEBB")
        pre.append(sprite(GOBLIN, 14, 530, 960, pal=shp))  # shadow self, left
        post.append(sprite(WRENCH, 30, 1580, 560))          # the self-built tool
    elif spec.slug == "infra":
        # the goblin at its workbench, hammer nearby: building its own tools.
        post.append(sprite(WORKBENCH, 30, 486, 780))        # workbench, left
        post.append(sprite(HAMMER, 30, 1590, 560))          # hammer prop, right
    return "".join(pre) + "".join(post)


def build_svg(spec: Spec, mode="cover") -> str:
    f = FLAGS[spec.flag]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">']
    if mode == "goblin":
        gw, gh = dims(GOBLIN, 46)
        parts += [f'<rect width="{W}" height="{H}" fill="#2a2340"/>',
                  sprite(GOBLIN, 46, CX-gw/2, H/2-gh/2), "</svg>"]
        return "".join(parts)
    parts += [flag_bg(f, spec.slug), scene(spec, f), wordmark(f),
              kicker(f, spec.kicker), "</svg>"]
    return "".join(parts)


# ---------------------------------------------------------------------------
# Render + review artifacts
# ---------------------------------------------------------------------------
def render(spec_or_mode) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    if spec_or_mode == "goblin":
        svg, name = build_svg(Spec("goblin", "violet", ""), "goblin"), "goblin"
    else:
        svg, name = build_svg(spec_or_mode), spec_or_mode.slug
    svg_path, raw, png = OUT/f"{name}.svg", OUT/f"{name}.raw.png", OUT/f"{name}.png"
    svg_path.write_text(svg, encoding="utf-8")
    subprocess.run(["rsvg-convert", "-w", str(W), "-h", str(H), str(svg_path),
                    "-o", str(raw)], check=True)
    subprocess.run(["magick", str(raw),
                    "(", "-size", f"{W}x{H}", "xc:gray50", "-seed", "5",
                    "-attenuate", "0.45", "+noise", "Gaussian", "-colorspace", "Gray", ")",
                    "-compose", "soft-light", "-composite", "-quality", "92", str(png)],
                   check=True)
    raw.unlink(missing_ok=True)
    return png


def contact(specs):
    out = OUT/"contact.png"
    subprocess.run(["magick", "montage", *[str(OUT/f"{s.slug}.png") for s in specs],
                    "-tile", "3x2", "-geometry", "440x293+16+28", "-background", "#0d0d0f",
                    "-font", LABEL_FONT, "-pointsize", "20", "-fill", "#cfd2da",
                    "-label", "%t", str(out)], check=True)
    return out


def mobile(specs):
    (OUT/"mobile").mkdir(exist_ok=True)
    crops = []
    for s in specs:
        c = OUT/"mobile"/f"{s.slug}.png"
        subprocess.run(["magick", str(OUT/f"{s.slug}.png"), "-gravity", "center",
                        "-crop", f"{H}x{H}+0+0", "+repage", "-resize", "300x300", str(c)],
                       check=True)
        crops.append(str(c))
    out = OUT/"mobile"/"sheet.png"
    subprocess.run(["magick", "montage", *crops, "-tile", "6x1", "-geometry", "300x300+8+8",
                    "-background", "#0d0d0f", "-font", LABEL_FONT, str(out)], check=True)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args == ["goblin"]:
        print("  rendered", render("goblin").name); return
    specs = [s for s in SPECS if not args or s.slug in args]
    for s in specs:
        print("  rendered", render(s).name)
    if len(specs) == len(SPECS):
        print("  contact:", contact(specs).name)
        print("  mobile :", mobile(specs).name)


if __name__ == "__main__":
    main()
