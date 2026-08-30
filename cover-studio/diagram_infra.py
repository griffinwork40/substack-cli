#!/usr/bin/env python3
"""In-body diagram for post 11: personal infrastructure software.

Four tools that form the author's self-built stack: agent harness, terminal,
DB inspector, browser (in progress). Thesis: minimum viable market is 1.

Reuses the pixel_studio chrome + grain pass. Local only."""
from pathlib import Path
import subprocess
from pixel_studio import FONT_MONO

W, H = 2400, 1000
OUT = Path(__file__).parent / "out" / "pixel"
BG = "#0e1c24"       # dark base (same as covers)
BAR = "#142028"      # slightly lighter card bg
COPPER = "#c47b38"   # warm copper accent
TEAL, INK, GOLD = "#17BEBB", "#F7F0E2", "#f2c24e"


def text(x, y, s, size=32, fill=INK, anchor="start", weight="normal", op=1.0):
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{FONT_MONO}" '
            f'font-weight="{weight}" font-size="{size}" fill="{fill}" opacity="{op}">{s}</text>')


def block(x, y, w, h, fill, op=1.0):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" opacity="{op}"/>'


def build_svg():
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        f'<rect width="{W}" height="{H}" fill="{BG}"/>',
        # copper rule + darker footer bar
        f'<rect x="0" y="{H-70}" width="{W}" height="6" fill="{COPPER}"/>',
        f'<rect x="0" y="{H-64}" width="{W}" height="64" fill="#080f14"/>',
        # byline
        text(60, 86, "THE GOBLIN FILES", 34, INK, weight="bold", op=0.85),
        # title + subtitle
        text(W/2, 190, "PERSONAL INFRASTRUCTURE", 72, INK, "middle", "bold"),
        text(W/2, 246, "the tools everything else runs inside of", 34, INK, "middle", op=0.85),
        # horizontal divider under header
        f'<rect x="150" y="278" width="{W-300}" height="3" fill="{COPPER}" opacity="0.55"/>',
    ]

    # --- four tool blocks ---
    tools = [
        ("AGENT HARNESS", "764 versions",        TEAL, 1.0),
        ("TERMINAL (UMBER)", "42 files, 19 scripts", GOLD, 1.0),
        ("DB INSPECTOR",  "",                    INK,  1.0),
        ("BROWSER",       "(in progress)",        INK,  0.42),
    ]

    cols = 4
    gap = 40
    total_gap = gap * (cols + 1)
    col_w = (W - total_gap) // cols
    box_y, box_h = 320, 420
    accent_h = 8

    for i, (label, sub, col, op) in enumerate(tools):
        x = gap + i * (col_w + gap)
        # card background (solid dark, high contrast with text)
        p.append(block(x, box_y, col_w, box_h, BAR, op=op))
        # top accent bar
        p.append(block(x, box_y, col_w, accent_h, col, op=op))
        # tool name, vertically centered in card
        cy = box_y + box_h // 2 - 10
        p.append(text(x + col_w / 2, cy, label, 44, col, "middle", "bold", op=op))
        # sub-label
        if sub:
            p.append(text(x + col_w / 2, cy + 64, sub, 30, INK, "middle", op=op * 0.82))

    # --- bottom thesis row ---
    thesis_y = 820
    p.append(text(W/2, thesis_y,
                  "minimum viable market:", 42, INK, "middle", "bold"))
    p.append(text(W/2, thesis_y + 70,
                  "1", 96, GOLD, "middle", "bold"))

    p.append("</svg>")
    return "".join(p)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    svg = OUT / "infra-diagram.svg"
    raw = OUT / "infra-diagram.raw.png"
    png = OUT / "infra-diagram.png"
    svg.write_text(build_svg(), encoding="utf-8")
    subprocess.run(["rsvg-convert", "-w", str(W), "-h", str(H),
                    str(svg), "-o", str(raw)], check=True)
    subprocess.run(["magick", str(raw),
                    "(", "-size", f"{W}x{H}", "xc:gray50", "-seed", "7",
                    "-attenuate", "0.45", "+noise", "Gaussian", "-colorspace", "Gray", ")",
                    "-compose", "soft-light", "-composite", "-quality", "92", str(png)],
                   check=True)
    raw.unlink(missing_ok=True)
    print("  rendered", png.name)


if __name__ == "__main__":
    main()
