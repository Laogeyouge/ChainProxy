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
WHITE = (255, 255, 255)
INNER = BG_TOP
# Apple icon convention (macOS Big Sur+): the rounded square only fills
# ~80% of the canvas; the remaining ~10% per side is transparent padding.
# Without this, our icon visually outsizes every native app in the Dock.
INNER_FRAC = 0.824
RADIUS_FRAC = 0.225  # of the inner content size, not the canvas

def _make_inner(inner: int) -> Image.Image:
    """Draw the rounded square with chain glyph at `inner` × `inner` pixels."""
    radius = int(inner * RADIUS_FRAC)

    # Vertical gradient
    grad = Image.new('RGB', (1, inner))
    for y in range(inner):
        t = y / (inner - 1)
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
        grad.putpixel((0, y), (r, g, b))
    grad = grad.resize((inner, inner))

    mask = Image.new('L', (inner, inner), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, inner, inner], radius=radius, fill=255)

    img = Image.new('RGBA', (inner, inner), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    # Soft top highlight for depth
    hl = Image.new('RGBA', (inner, inner), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle(
        [0, 0, inner, int(inner * 0.35)],
        radius=radius, fill=(255, 255, 255, 32))
    hl = hl.filter(ImageFilter.GaussianBlur(radius=inner * 0.05))
    hl_clipped = Image.new('RGBA', (inner, inner), (0, 0, 0, 0))
    hl_clipped.paste(hl, (0, 0), mask)
    img = Image.alpha_composite(img, hl_clipped)

    # Chain glyph
    glyph = Image.new('RGBA', (inner, inner), (0, 0, 0, 0))
    g = ImageDraw.Draw(glyph)
    cy = int(inner * 0.50)
    node_r = int(inner * 0.105)
    line_w = int(inner * 0.045)
    inner_r = int(node_r * 0.28)
    xs = [int(inner * 0.235), int(inner * 0.50), int(inner * 0.765)]

    for x1, x2 in zip(xs[:-1], xs[1:]):
        g.line([(x1 + node_r, cy), (x2 - node_r, cy)],
                fill=WHITE, width=line_w)

    tail_x0 = xs[-1] + node_r
    tail_x1 = int(inner * 0.92)
    g.line([(tail_x0, cy), (tail_x1 - int(node_r * 0.55), cy)],
            fill=WHITE, width=line_w)
    ah = int(node_r * 0.55)
    g.polygon(
        [(tail_x1, cy),
         (tail_x1 - ah, cy - ah),
         (tail_x1 - ah * 6 // 10, cy),
         (tail_x1 - ah, cy + ah)],
        fill=WHITE,
    )

    for x in xs:
        g.ellipse([x - node_r, cy - node_r, x + node_r, cy + node_r],
                  fill=WHITE)
        g.ellipse([x - inner_r, cy - inner_r, x + inner_r, cy + inner_r],
                  fill=INNER)

    return Image.alpha_composite(img, glyph)


def make_master(size=SIZE):
    """Full canvas with the rounded square inset to ~82.4% per Apple's
    macOS icon template — without this padding, the Dock renders our icon
    visually larger than every native app."""
    inner_size = int(size * INNER_FRAC)
    offset = (size - inner_size) // 2
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    canvas.paste(_make_inner(inner_size), (offset, offset),
                 _make_inner(inner_size))
    return canvas

def main():
    repo_root = Path(__file__).resolve().parent.parent
    out = repo_root / "ChainProxy.app" / "Contents" / "Resources" / "ChainProxy.icns"
    out.parent.mkdir(parents=True, exist_ok=True)

    master = make_master(1024)

    # Also save a 512×512 PNG at the repo root so GitHub's README can render it
    # (.icns isn't supported by GitHub's image renderer).
    png_out = repo_root / "icon.png"
    master.resize((512, 512), Image.LANCZOS).save(png_out, "PNG")
    print(f"✓ {png_out}  ({png_out.stat().st_size:,} bytes)")
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
