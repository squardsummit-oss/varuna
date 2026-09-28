"""Build VARUNA's brand files from the team's logo, deterministically.

    uv run python tools/brand_assets.py            # write every file below
    uv run python tools/brand_assets.py --check    # exit 1 if any committed file differs

Reads the team's logo (never modified; `--source` names another): `docs/brand/varuna-logo.png`
if the repository carries it there, else `Varuna Logo.png` at the repository root, where the team
supplied it. Either path must be committed for a clean clone to rebuild or `--check` these files;
neither is under `public/`, because nothing in the app loads the 1.6 MB original. It writes:

  apps/command/public/brand/varuna-lockup.png               full logo trimmed to its artwork, 640 px
  apps/command/public/brand/varuna-mark-192.png             the "V" emblem alone, square, 192 px
  apps/command/public/brand/varuna-mark-64.png              the emblem at 64 px, 256 colours, ~3 KB
  apps/command/public/brand/varuna-app-icon-192.png         emblem on a rounded --ink tile (manifest)
  apps/command/public/brand/varuna-app-icon-512.png         the same tile at 512 px (manifest)
  apps/command/public/brand/varuna-app-icon-maskable-512.png  full-bleed --ink, emblem in the safe zone
  apps/command/public/icon.svg                              the 192 px tile wrapped in an SVG
  apps/command/app/favicon.ico                              16/32/48 px tile
  apps/command/app/apple-icon.png                           180 px, emblem on opaque --ink

Nothing here is redrawn: every file is the supplied artwork cropped and resampled. The logo is a
photographic illustration, so there is no vector form of it; `icon.svg` stays an SVG only because
`layout.tsx` and `sw.js` already name it, and it carries the 192 px tile as a PNG.

The supplied PNG is RGBA and its background is transparent: alpha is 0 on 76 % of pixels. Its RGB
channels still hold a blue haze under those transparent pixels, which no viewer that respects
alpha shows. Every derived file clears RGB where alpha is 0 and resamples premultiplied, so that
haze never bleeds into an edge.

Deterministic: Pillow's PNG and ICO encoders and its octree quantiser have no clock or random
input, so two runs on the same logo and the same `tokens.json` write the same bytes. `--check`
rebuilds everything in memory and compares it with what is on disk.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parents[1]

# The emblem and the lettering are separated by rows with no opaque pixel (752-780 in the source);
# everything above this row is the emblem.
EMBLEM_LAST_ROW = 765
# Faint haze (alpha 16 or less) sits far outside the artwork; it does not decide the crop.
ALPHA_FLOOR = 16
# Margin around the artwork, as a share of the square's side (mark) or of the artwork (lockup).
MARK_PAD = 0.02
LOCKUP_PAD = 0.02
LOCKUP_WIDTH = 640
# A maskable icon's safe zone is a centred circle 80 % of the side across; the largest square
# inside it is 0.8 / sqrt(2) = 0.566 of the side, so the emblem square is drawn at 0.56.
MASKABLE_FILL = 0.56

# Where the logo is read from, in order: a copy kept under `docs/brand/` (tracked, never served),
# then the file the team supplied at the root. Committing either one lets a clean clone rebuild
# and `--check` the brand files; `tools/test_brand_assets.py` fails in CI when neither is there.
LOGO_IN_REPO = Path("docs/brand/varuna-logo.png")
LOGO_AT_ROOT = Path("Varuna Logo.png")


def default_source(root: Path) -> Path:
    """The logo `--source` defaults to: the `docs/brand/` copy when present, else the root file.

    When neither exists the `docs/brand/` path is returned, so the error names a tracked place to
    put it.
    """
    for rel in (LOGO_IN_REPO, LOGO_AT_ROOT):
        if (root / rel).is_file():
            return root / rel
    return root / LOGO_IN_REPO


def ink(root: Path) -> tuple[int, int, int]:
    """`--ink` from `tokens.json`, so the icon tiles are the app's own background."""
    tokens = json.loads((root / "packages" / "tokens" / "tokens.json").read_text("utf8"))
    hexv = tokens["color"]["base"]["ink"]["value"].lstrip("#")
    return (int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16))


def load_clean(source: Path) -> Image.Image:
    im = Image.open(source).convert("RGBA")
    a = np.asarray(im).copy()
    a[a[..., 3] == 0, :3] = 0
    return Image.fromarray(a, "RGBA")


def resize(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Premultiplied Lanczos, so transparent pixels carry no colour into the edges."""
    return im.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")


def bbox(im: Image.Image, last_row: int | None = None) -> tuple[int, int, int, int]:
    al = np.asarray(im)[..., 3]
    if last_row is not None:
        al = al[:last_row]
    ys, xs = np.where(al > ALPHA_FLOOR)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def square(im: Image.Image, box: tuple[int, int, int, int], pad: float) -> Image.Image:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    side = round(max(w, h) / (1 - 2 * pad))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im.crop(box), ((side - w) // 2, (side - h) // 2))
    return canvas


def tile(
    mark: Image.Image, size: int, colour: tuple[int, int, int], radius: float, fill: float
) -> Image.Image:
    """The emblem on a square of `colour`; `radius` as a share of the side, 0 for a full square."""
    scale = 4
    big = size * scale
    base = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=int(radius * big), fill=255
    )
    base.paste(Image.new("RGBA", (big, big), (*colour, 255)), (0, 0), mask)
    inner = round(big * fill)
    base.alpha_composite(resize(mark, (inner, inner)), ((big - inner) // 2, (big - inner) // 2))
    out = resize(base, (size, size))
    if size <= 64:
        out = out.filter(ImageFilter.UnsharpMask(radius=0.6, percent=60, threshold=2))
    return out


def png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build(root: Path, source: Path) -> dict[Path, bytes]:
    """Every brand file, keyed by its path under `root`."""
    brand = Path("apps/command/public/brand")
    app = Path("apps/command/app")
    im = load_clean(source)
    width, height = im.size
    out: dict[Path, bytes] = {}

    lx0, ly0, lx1, ly1 = bbox(im)
    padx, pady = int((lx1 - lx0) * LOCKUP_PAD), int((ly1 - ly0) * LOCKUP_PAD)
    lock = im.crop(
        (max(0, lx0 - padx), max(0, ly0 - pady), min(width, lx1 + padx), min(height, ly1 + pady))
    )
    lock_size = (LOCKUP_WIDTH, round(LOCKUP_WIDTH * lock.height / lock.width))
    out[brand / "varuna-lockup.png"] = png(resize(lock, lock_size))

    sq = square(im, bbox(im, EMBLEM_LAST_ROW), MARK_PAD)
    out[brand / "varuna-mark-192.png"] = png(resize(sq, (192, 192)))
    m64 = resize(sq, (64, 64)).filter(ImageFilter.UnsharpMask(radius=0.6, percent=50, threshold=2))
    # /rural sends this one on a 2G budget, and every screen's header draws it: a 256-colour
    # palette is indistinguishable at 64 px and cuts it from about 7.5 KB to about 3 KB.
    out[brand / "varuna-mark-64.png"] = png(
        m64.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
    )

    colour = ink(root)
    icon192 = png(tile(sq, 192, colour, 0.22, 0.86))
    out[brand / "varuna-app-icon-192.png"] = icon192
    out[brand / "varuna-app-icon-512.png"] = png(tile(sq, 512, colour, 0.22, 0.86))
    out[brand / "varuna-app-icon-maskable-512.png"] = png(tile(sq, 512, colour, 0.0, MASKABLE_FILL))
    out[app / "apple-icon.png"] = png(tile(sq, 180, colour, 0.0, 0.80))

    b64 = base64.b64encode(icon192).decode("ascii")
    out[Path("apps/command/public/icon.svg")] = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192" width="192" height="192"'
        ' role="img" aria-label="VARUNA"><title>VARUNA</title>'
        f'<image width="192" height="192" href="data:image/png;base64,{b64}"/></svg>\n'
    ).encode()

    ico = io.BytesIO()
    tile(sq, 256, colour, 0.22, 0.90).save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    out[app / "favicon.ico"] = ico.getvalue()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO, help="repository root")
    parser.add_argument(
        "--source",
        type=Path,
        help=f"the logo (default: <root>/{LOGO_IN_REPO.as_posix()}, else <root>/{LOGO_AT_ROOT})",
    )
    parser.add_argument("--check", action="store_true", help="compare with disk; write nothing")
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    source: Path = args.source or default_source(root)
    if not source.is_file():
        print(
            f"{source} not found: copy the team's logo there byte for byte, or pass --source.",
            file=sys.stderr,
        )
        return 2

    files = build(root, source)
    stale = []
    for rel, data in files.items():
        path = root / rel
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                stale.append(rel.as_posix())
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"{rel.as_posix()}  {len(data)} bytes")
    if args.check:
        if stale:
            print("out of date: " + ", ".join(stale), file=sys.stderr)
            print("rebuild with: uv run python tools/brand_assets.py", file=sys.stderr)
            return 1
        print(f"{len(files)} brand files match {source.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
