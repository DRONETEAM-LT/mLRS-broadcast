#!/usr/bin/env python3
"""Generate las_splash.h: the 1-bit splash artwork for the TX display.

The whole logo lockup is traced from las_logo.png and downscaled - nothing is
redrawn and no display fonts are used, so the symbol and the wordmark keep the
original's proportions and spacing exactly. Only the credit line underneath is
typeset here.

The panel is 128x64 and 1 bit per pixel, so the brand colours become fill
styles: white ink is drawn solid, orange ink hollow. The split has to happen
before the image is reduced to monochrome - once it is grey the two tones are
indistinguishable.

Orange is outlined by default, so LIGHT reads solid and A SKY hollow. Two
alternatives exist because a 1px outline is not the only way to suggest a
second tone: --orange checker fills with a 50% checkerboard (legible on the
wordmark, but it thins the small dots), and --orange auto outlines the dots but
fills the wordmark.

    python las_splash_generate.py                     # las_logo.png beside this file
    python las_splash_generate.py --orange checker    # 50% dither instead
    python las_splash_generate.py --preview out.png
"""

import argparse
import os

from PIL import Image, ImageChops, ImageDraw, ImageFont

W, H = 128, 64
HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

DEFAULT_LOGO = os.path.join(HERE, "las_logo.png")

ORANGE_DOC = {
    "checker": "50% checkerboard (reads as a lighter tone)",
    "auto":   "hollow in the symbol, solid in the wordmark",
    "hollow": "hollow (1px outline)",
    "solid":  "solid",
}

# --- layout ---------------------------------------------------------------
# The logo is placed at an explicit scale and centred, rather than stretched to
# fill a box: the artwork carries its own margins and filling the panel put the
# wordmark hard against the edge.
LOGO_SCALE = 0.5
# The source PNG is cropped flush: it has ink on row 0 and column 0, so the
# outer dots and the tops of the letters sit hard against the frame. Padding it
# with black restores breathing room. Done here rather than by editing the
# artwork, so las_logo.png stays exactly as supplied.
LOGO_PAD = (3, 3, 3, 0)             # left, top, right, bottom
LOGO_AREA_BOTTOM = 52               # logo is centred in rows 0..LOGO_AREA_BOTTOM
# Tahoma is a screen face and stays crisp this small; Arial turns to mush at a
# 7-8px cap height. 1px strokes, which is the thin-line look wanted here.
CREDIT = dict(text="based on mLRS", y=54, cap=8, font="tahoma.ttf")

# Ink classification for the source artwork.
BG_MIN = 24                         # brightest channel below this = background
NEUTRAL_SAT = 0.25                  # saturation under this = white ink, else orange
INK_LEVEL = 100                      # coverage at/above this becomes a set pixel


def as_mask(img):
    """Anything -> 1-bit mask where set pixels are ink."""
    return img.convert("L").point(lambda v: 255 if v >= 128 else 0, mode="1")


def outline_of(mask):
    """1px boundary of a mask: dilate(mask) minus mask."""
    lum = mask.convert("L")
    dil = Image.new("L", lum.size, 0)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            dil = ImageChops.lighter(dil, ImageChops.offset(lum, dx, dy))
    return as_mask(ImageChops.subtract(dil, lum))


def checker_of(mask, phase=0):
    """Fill a mask with a 50% checkerboard.

    On a 1-bit panel this is the nearest thing to a second tone: every other
    pixel lit reads as a lighter shade of the same shape. Unlike an outline it
    does not need the shape to be thick enough to have an inside, so small
    letterforms stay readable while still reading as distinct from solid ink.
    """
    w, h = mask.size
    patt = Image.new("L", (w, h), 0)
    pp = patt.load()
    for y in range(h):
        for x in range((y + phase) % 2, w, 2):
            pp[x, y] = 255
    return as_mask(ImageChops.multiply(mask.convert("L"), patt))


def flatten(path):
    """Open artwork and composite any transparency onto the brand's dark ground."""
    src = Image.open(path)
    if src.mode in ("RGBA", "LA", "P"):
        src = src.convert("RGBA")
        ground = Image.new("RGBA", src.size, (0, 0, 0, 255))
        src = Image.alpha_composite(ground, src)
    return src.convert("RGB")


def pad(rgb, margins):
    """Add black margins (left, top, right, bottom) around the artwork."""
    l, t, r, b = margins
    if not any(margins):
        return rgb
    out = Image.new("RGB", (rgb.width + l + r, rgb.height + t + b), (0, 0, 0))
    out.paste(rgb, (l, t))
    return out


def split_by_colour(rgb):
    """Separate the artwork into (white ink, orange ink) COVERAGE maps.

    Greyscale, not 1-bit, and that matters: the maps are downscaled before they
    are thresholded, so the resampler still has the anti-aliasing to work with.
    Thresholding first and resampling a hard 0/255 image afterwards eats the
    thinnest strokes - the arm of the T and the diagonal of the Y vanish.

    Every non-background pixel contributes its full brightness as coverage to
    whichever tone it belongs to, so nothing is dropped at shape edges. The
    tone is decided by saturation: near-neutral is white ink, warm is orange.
    """
    w, h = rgb.size
    px = rgb.load()
    white = Image.new("L", (w, h), 0)
    orange = Image.new("L", (w, h), 0)
    wp, op = white.load(), orange.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            mx, mn = max(r, g, b), min(r, g, b)
            if mx < BG_MIN:
                continue                                   # background
            sat = 0.0 if mx == 0 else (mx - mn) / float(mx)
            if sat < NEUTRAL_SAT:
                wp[x, y] = mx                              # white ink
            else:
                op[x, y] = mx                              # warm/orange ink
    return white, orange


def symbol_wordmark_split(mask):
    """x of the widest all-blank column run: the gap between symbol and text."""
    w, h = mask.size
    px = mask.load()
    blank = [not any(px[x, y] for y in range(h)) for x in range(w)]
    best = (0, None)
    run = None
    for x in range(w + 1):
        if x < w and blank[x]:
            run = x if run is None else run
        elif run is not None:
            if x - run > best[0]:
                best = (x - run, (run + x) // 2)
            run = None
    return best[1]


def trace_lockup(path, scale, orange_style="hollow"):
    """Downscale the whole logo by `scale`, keeping the two tones distinct.

    The artwork is NOT cropped to its ink: the original's own margins are part
    of the design, and cropping them made the mark fill the panel edge to edge.
    Both tones are resampled as greyscale coverage and thresholded only at the
    end, so thin strokes survive the reduction.

    orange_style:
      "checker" 50% checkerboard: a lighter tone that survives small sizes.
      "hollow" outline everywhere: LIGHT solid, A SKY hollow, orange dots rings.
      "auto"   hollow in the symbol, solid in the wordmark. A 1px outline reads
               well on the dots but fragments on letterforms once small.
      "solid"  fill everywhere; white and orange become indistinguishable.
    """
    rgb = pad(flatten(path), LOGO_PAD)
    white, orange = split_by_colour(rgb)

    sw, sh = rgb.size
    tw, th = max(1, round(sw * scale)), max(1, round(sh * scale))
    white = white.resize((tw, th), Image.LANCZOS)
    orange = orange.resize((tw, th), Image.LANCZOS)

    def ink(cov):
        return cov.point(lambda v: 255 if v >= INK_LEVEL else 0, mode="1")

    white_m, orange_m = ink(white), ink(orange)

    split_x = None
    if orange_style == "auto":
        combined = as_mask(ImageChops.lighter(white_m.convert("L"),
                                             orange_m.convert("L")))
        split_x = symbol_wordmark_split(combined)

    if orange_style == "checker":
        orange_m = checker_of(orange_m)
    elif orange_style == "hollow":
        orange_m = outline_of(orange_m)
    elif orange_style == "auto" and split_x is not None:
        left = Image.new("1", (tw, th), 0)          # symbol side
        left.paste(orange_m.crop((0, 0, split_x, th)), (0, 0))
        right = Image.new("1", (tw, th), 0)         # wordmark side
        right.paste(orange_m.crop((split_x, 0, tw, th)), (split_x, 0))
        orange_m = as_mask(ImageChops.lighter(outline_of(left).convert("L"),
                                             right.convert("L")))

    art = as_mask(ImageChops.lighter(white_m.convert("L"), orange_m.convert("L")))
    return art, (sw, sh), scale, split_x


def render_line(text, font_name, cap_px):
    """Typeset a single line at a given cap height, cropped tight."""
    path = os.path.join(FONTS, font_name)
    size = cap_px
    while size < cap_px * 4:
        f = ImageFont.truetype(path, size)
        b = f.getbbox("H")
        if (b[3] - b[1]) >= cap_px:
            break
        size += 1
    font = ImageFont.truetype(path, size)
    pad = 8
    canvas = Image.new("L", (W * 4, cap_px * 5 + pad * 2), 0)
    ImageDraw.Draw(canvas).text((pad, pad), text, font=font, fill=255)
    mask = as_mask(canvas)
    bbox = mask.getbbox()
    return mask.crop(bbox) if bbox else mask


def compose(logo_path, orange_style="hollow", scale=LOGO_SCALE):
    canvas = Image.new("1", (W, H), 0)
    art, src_size, scale, split_x = trace_lockup(logo_path, scale, orange_style)
    x = (W - art.width) // 2
    y = max(0, (LOGO_AREA_BOTTOM - art.height) // 2)
    canvas.paste(art, (x, y))

    credit = render_line(CREDIT["text"], CREDIT["font"], CREDIT["cap"])
    canvas.paste(credit, ((W - credit.width) // 2, CREDIT["y"]))
    return canvas, dict(logo=art.size, source=src_size, scale=scale,
                        credit=credit.size, split_x=split_x,
                        orange_style=orange_style)


def to_c_bytes(img):
    """Row-major, MSB first, one bit per pixel: the gdisp_drawbitmap format."""
    w, h = img.size
    px = img.load()
    out = bytearray()
    for y in range(h):
        bit = acc = 0
        for x in range(w):
            acc = (acc << 1) | (1 if px[x, y] else 0)
            bit += 1
            if bit == 8:
                out.append(acc); bit = acc = 0
        if bit:
            out.append(acc << (8 - bit))
    return bytes(out)


def emit_header(img, path, info):
    data = to_c_bytes(img)
    w, h = img.size
    lines = [
        "//*******************************************************",
        "// LAS splash artwork for the TX display",
        "// GENERATED by las_splash_generate.py - do not edit by hand",
        "//",
        "// 1 bit per pixel, row-major, MSB first: the gdisp_drawbitmap format.",
        "// The logo is traced from las_logo.png and downscaled, not redrawn.",
        "// The panel is monochrome, so the brand colours become fill styles:",
        f"//   white ink  -> solid",
        f"//   orange ink -> {ORANGE_DOC[info['orange_style']]}",
        f"// source {info['source'][0]}x{info['source'][1]} scaled by "
        f"{info['scale']:.3f} to {info['logo'][0]}x{info['logo'][1]}",
        "//*******************************************************",
        "#pragma once",
        "",
        f"#define LAS_SPLASH_W  {w}",
        f"#define LAS_SPLASH_H  {h}",
        "",
        f"const uint8_t las_splash_{w}x{h}_bw[] = {{",
    ]
    for i in range(0, len(data), 16):
        lines.append("    " + ", ".join(f"0x{b:02X}" for b in data[i:i + 16]) + ",")
    lines += ["};", ""]
    with open(path, "w", newline="\n") as fh:
        fh.write("\n".join(lines))
    return len(data)


def ascii_preview(img):
    px = img.load()
    return "\n".join("".join("#" if px[x, y] else "." for x in range(img.width))
                     for y in range(img.height))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--logo", default=DEFAULT_LOGO)
    ap.add_argument("--scale", type=float, default=LOGO_SCALE,
                    help=f"downscale factor for the logo (default {LOGO_SCALE})")
    ap.add_argument("--orange", choices=("hollow", "checker", "auto", "solid"), default="hollow",
                    help="how orange ink is drawn. hollow (default) outlines it "
                         "everywhere, matching the brand: LIGHT solid, A SKY "
                         "hollow. auto keeps the dots hollow but fills the "
                         "wordmark, which is easier to read at this size. "
                         "solid fills everything and loses the distinction.")
    ap.add_argument("--out", default=os.path.join(HERE, "las_splash.h"))
    ap.add_argument("--preview", help="write a 5x PNG to eyeball")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    img, info = compose(args.logo, args.orange, args.scale)
    n = emit_header(img, args.out, info)
    print(f"wrote {args.out}: {img.width}x{img.height}, {n} bytes")
    print(f"  logo    {info['source'][0]}x{info['source'][1]} -> "
          f"{info['logo'][0]}x{info['logo'][1]}  (scale {info['scale']:.3f}), "
          f"orange={args.orange}"
          + (f", symbol/text split at x={info['split_x']}" if info['split_x'] else ""))
    print(f"  credit  {info['credit'][0]}x{info['credit'][1]}")
    if args.preview:
        img.convert("L").resize((img.width * 5, img.height * 5), Image.NEAREST).save(args.preview)
        print(f"  preview {args.preview}")
    if not args.quiet:
        print()
        print(ascii_preview(img))
