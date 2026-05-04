"""ChainProxy.ico generator (Windows).

Reuses the same procedural glyph as scripts/make_icon.py and packs it into
a multi-resolution Windows .ico (16/32/48/64/128/256). Pillow handles the
.ico container natively.

Run:    py scripts\\make_icon_windows.py
"""
from pathlib import Path
import sys

# Reuse make_master from the macOS icon script
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icon import make_master  # noqa: E402


def main():
    repo_root = Path(__file__).resolve().parent.parent
    out = repo_root / "icon.ico"

    master = make_master(1024)

    # Standard Windows .ico sizes. 256 is required for File Explorer's
    # extra-large icon view; 16/32 for taskbar.
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

    # Pillow's ICO encoder accepts a `sizes` list and resizes the source for
    # each entry. We feed it the largest master and let it down-scale; Pillow
    # 9+ uses HAMMING by default which is acceptable for icon-sized images.
    master.save(out, format="ICO", sizes=sizes)
    print(f"OK  {out}  ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
