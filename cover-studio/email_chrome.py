#!/usr/bin/env python3
"""Email header banner and footer strip for The Goblin Files.

Matches the pixel_studio system: dark background, goblin sprite, teal accent,
monospace type, grain pass. Publication-level assets, not per-post.

Header: 1456x200 (Substack recommended email banner width).
Footer: 1456x120 (compact sign-off strip).

Usage:
    python3 email_chrome.py           # renders both
    python3 email_chrome.py header    # header only
    python3 email_chrome.py footer    # footer only
"""
from pathlib import Path
import subprocess, sys
from pixel_studio import GOBLIN, GW, PAL, FONT_MONO, sprite

OUT = Path(__file__).parent / "out" / "pixel"
BG = "#0e1c24"
TEAL = "#17BEBB"
INK = "#F7F0E2"
GOLD = "#f2c24e"


def text(x, y, s, size=32, fill=INK, anchor="start", weight="normal"):
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{FONT_MONO}" '
            f'font-weight="{weight}" font-size="{size}" fill="{fill}">{s}</text>')


def render(name, w, h, svg_body):
    OUT.mkdir(parents=True, exist_ok=True)
    svg_p = OUT / f"{name}.svg"
    raw_p = OUT / f"{name}.raw.png"
    png_p = OUT / f"{name}.png"
    full = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">'
            f'<rect width="{w}" height="{h}" fill="{BG}"/>'
            f'{svg_body}</svg>')
    svg_p.write_text(full, encoding="utf-8")
    subprocess.run(["rsvg-convert", "-w", str(w), "-h", str(h),
                    str(svg_p), "-o", str(raw_p)], check=True)
    subprocess.run(["magick", str(raw_p),
                    "(", "-size", f"{w}x{h}", "xc:gray50", "-seed", "7",
                    "-attenuate", "0.35", "+noise", "Gaussian",
                    "-colorspace", "Gray", ")",
                    "-compose", "soft-light", "-composite",
                    "-quality", "92", str(png_p)], check=True)
    raw_p.unlink(missing_ok=True)
    print(f"  rendered {png_p.name}")


def header():
    """1100x220 email banner: goblin left, wordmark center, teal accent bottom."""
    w, h = 1100, 220
    parts = []
    # teal accent line at bottom
    parts.append(f'<rect x="0" y="{h-4}" width="{w}" height="4" fill="{TEAL}"/>')
    # goblin sprite, small (px=8), left side
    parts.append(sprite(GOBLIN, 8, 24, 10))
    # wordmark
    parts.append(text(240, 90, "THE GOBLIN FILES", size=42, fill=INK, weight="bold"))
    # tagline
    parts.append(text(240, 130, "beachy code goblin building shit in daytona",
                       size=20, fill=TEAL))
    parts.append(text(240, 158, "music always on", size=20, fill=GOLD))
    return w, h, "".join(parts)


def footer():
    """1456x120 email footer: teal accent top, sign-off, copyright."""
    w, h = 1456, 120
    parts = []
    # teal accent line at top
    parts.append(f'<rect x="0" y="0" width="{w}" height="4" fill="{TEAL}"/>')
    # sign-off
    parts.append(text(w // 2, 52, "Griffin, Daytona Beach, FL",
                       size=22, fill=INK, anchor="middle"))
    # copyright
    parts.append(text(w // 2, 88, "griffinlong.substack.com",
                       size=16, fill=TEAL, anchor="middle"))
    return w, h, "".join(parts)


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which in ("header", "both"):
        w, h, body = header()
        render("email-header", w, h, body)
    if which in ("footer", "both"):
        w, h, body = footer()
        render("email-footer", w, h, body)


if __name__ == "__main__":
    main()
