"""ChainProxy.icns generator.

Produces a macOS .icns at <repo>/ChainProxy.app/Contents/Resources/ChainProxy.icns
from a procedurally drawn glyph (rounded blue square + 3-node chain + exit arrow).

Run:    python3 scripts/make_icon.py
"""
from PIL import Image, ImageDraw, ImageFilter
from pathlib import Path
import subprocess
import tempfile

SIZE = 1024
BG_TOP = (51, 149, 255)
BG_BOT = (0, 102, 204)
RADIUS = int(SIZE * 0.225)
WHITE = (255, 255, 255)
INNER = BG_TOP

def make_master(size=SIZE):
    # Vertical gradient
    grad = Image.new('RGB', (1, size))
    for y in range(size):
        t = y / (size - 1)
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
        grad.putpixel((0, y), (r, g, b))
    grad = grad.resize((size, size))

    # Rounded mask
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size, size], radius=RADIUS, fill=255)

    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    # Soft top highlight for depth
    hl = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle(
        [0, 0, size, int(size * 0.35)],
        radius=RADIUS, fill=(255, 255, 255, 32))
    hl = hl.filter(ImageFilter.GaussianBlur(radius=size * 0.05))
    hl_clipped = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    hl_clipped.paste(hl, (0, 0), mask)
    img = Image.alpha_composite(img, hl_clipped)

    # Chain glyph
    glyph = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glyph)
    cy = int(size * 0.50)
    node_r = int(size * 0.105)
    line_w = int(size * 0.045)
    inner_r = int(node_r * 0.28)        # smaller, port-like dot
    xs = [int(size * 0.235), int(size * 0.50), int(size * 0.765)]

    # Connecting lines (clean, no chevrons)
    for x1, x2 in zip(xs[:-1], xs[1:]):
        # Avoid drawing inside the white circles to keep edges crisp
        g.line([(x1 + node_r, cy), (x2 - node_r, cy)],
                fill=WHITE, width=line_w)

    # Arrow tail extending right of the last node, ending in arrowhead
    tail_x0 = xs[-1] + node_r
    tail_x1 = int(size * 0.92)
    g.line([(tail_x0, cy), (tail_x1 - int(node_r * 0.55), cy)],
            fill=WHITE, width=line_w)
    # Arrowhead
    ah = int(node_r * 0.55)
    g.polygon(
        [(tail_x1, cy),
         (tail_x1 - ah, cy - ah),
         (tail_x1 - ah * 6 // 10, cy),
         (tail_x1 - ah, cy + ah)],
        fill=WHITE,
    )

    # Solid white circles with small inner port dot
    for x in xs:
        g.ellipse([x - node_r, cy - node_r, x + node_r, cy + node_r],
                  fill=WHITE)
        g.ellipse([x - inner_r, cy - inner_r, x + inner_r, cy + inner_r],
                  fill=INNER)

    img = Image.alpha_composite(img, glyph)
    return img

def main():
    repo_root = Path(__file__).resolve().parent.parent
    out = repo_root / "ChainProxy.app" / "Contents" / "Resources" / "ChainProxy.icns"
    out.parent.mkdir(parents=True, exist_ok=True)

    master = make_master(1024)
    targets = [
        ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
    ]

    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "ChainProxy.iconset"
        iconset.mkdir()
        for name, px in targets:
            master.resize((px, px), Image.LANCZOS).save(iconset / name, "PNG")
        if out.exists():
            out.unlink()
        subprocess.check_call(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(out)])
    print(f"✓ {out}  ({out.stat().st_size:,} bytes)")

if __name__ == "__main__":
    main()
