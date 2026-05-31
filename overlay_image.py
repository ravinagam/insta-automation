#!/usr/bin/env python3
"""
overlay_image.py — Branded text overlay for Instagram content pipeline (Phase 1)

Required:  pip install Pillow
Compatible: Python 3.8+

Usage:
    python3 overlay_image.py \
        --input  /path/to/input.jpg \
        --output /path/to/output.jpg \
        --title  "Product Name" \
        --price  "₹999" \
        --tagline "Limited time offer"
"""

import argparse
import sys
import traceback
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


# ── Font search order (bold) ───────────────────────────────────────────────────
BOLD_FONT_PATHS = [
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

# ── Font search order (italic/regular for tagline) ────────────────────────────
ITALIC_FONT_PATHS = [
    "/usr/share/fonts/truetype/montserrat/Montserrat-Italic.ttf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def find_font_path(paths: list) -> str | None:
    """Return the first existing font path from the list, or None."""
    for p in paths:
        if Path(p).exists():
            return p
    return None


def load_font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    """Load a TrueType font at given size; fall back to default if unavailable."""
    if path:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()


def measure_text(draw: ImageDraw.Draw, text: str, font) -> tuple[int, int]:
    """Return (pixel_width, pixel_height) for given text + font."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def fit_text(
    draw: ImageDraw.Draw,
    text: str,
    font_path: str | None,
    initial_size: int,
    max_width: int,
    italic: bool = False,
) -> tuple:
    """
    Shrink font until text fits within max_width pixels.
    If it still overflows at minimum size, truncates with '...'.
    Returns (font_object, display_text).
    """
    size = initial_size
    min_size = max(8, initial_size // 4)  # never shrink below 25% of original

    while size >= min_size:
        font = load_font(font_path, size)
        w, _ = measure_text(draw, text, font)
        if w <= max_width:
            return font, text

        # Text still overflows — try truncating with ellipsis at this size
        truncated = text
        while len(truncated) > 4:
            truncated = truncated[:-1]
            wt, _ = measure_text(draw, truncated + "...", font)
            if wt <= max_width:
                return font, truncated + "..."

        # Truncation still couldn't fix it — try smaller font
        size -= max(1, size // 10)

    # Last resort: use minimum size with truncation
    font = load_font(font_path, min_size)
    return font, text[:12] + "..."


def draw_gradient_bar(image: Image.Image, bar_height: int) -> Image.Image:
    """
    Composite a semi-transparent dark gradient strip over the bottom bar_height pixels.
    Alpha: 0 (transparent) at the top of the bar → 190 (~75%) at the very bottom.
    The image must be in RGBA mode.
    """
    W, H = image.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bar_top = H - bar_height

    for y_offset in range(bar_height):
        # y_offset=0 → top of bar (transparent); y_offset=bar_height-1 → bottom (opaque)
        alpha = int(190 * (y_offset / max(bar_height - 1, 1)))
        y = bar_top + y_offset
        draw.line([(0, y), (W - 1, y)], fill=(0, 0, 0, alpha))

    return Image.alpha_composite(image, overlay)


def draw_centered(
    draw: ImageDraw.Draw,
    text: str,
    font,
    y: int,
    W: int,
    color: tuple,
) -> None:
    """Draw text centered horizontally at vertical pixel position y."""
    text_w, _ = measure_text(draw, text, font)
    x = max(0, (W - text_w) // 2)
    draw.text((x, y), text, font=font, fill=color)


def process_image(
    input_path: str,
    output_path: str,
    title: str,
    price: str,
    tagline: str,
) -> None:
    """
    Full processing pipeline:
      1. Open image → RGBA
      2. Draw gradient bar (bottom 28%)
      3. Measure + fit text layers
      4. Draw title / price / tagline
      5. Save as high-quality JPEG
    """

    # ── Open and convert ──────────────────────────────────────────────────────
    img = Image.open(input_path)
    img = img.convert("RGBA")
    W, H = img.size
    orientation = "portrait (9:16)" if H > W else "landscape (16:9)"
    print(f"  Input: {W}x{H} pixels ({orientation})")

    # ── Gradient bar over bottom 28% ──────────────────────────────────────────
    bar_height = int(H * 0.28)
    img = draw_gradient_bar(img, bar_height)

    # ── Resolve font paths ────────────────────────────────────────────────────
    bold_path   = find_font_path(BOLD_FONT_PATHS)
    italic_path = find_font_path(ITALIC_FONT_PATHS)

    if bold_path:
        print(f"  Font (bold):   {bold_path}")
    else:
        print("  Font (bold):   built-in fallback (no TTF found)")

    if italic_path:
        print(f"  Font (italic): {italic_path}")

    # ── Font sizes proportional to image height ───────────────────────────────
    title_size   = int(H * 0.05)   # 5% of height  → product name
    price_size   = int(H * 0.08)   # 8% of height  → price most prominent
    tagline_size = int(H * 0.035)  # 3.5% of height → subtitle, smallest

    max_text_w = int(W * 0.90)     # text must fit within 90% of image width

    # Temporary draw surface for measurement (no visible effect)
    draw = ImageDraw.Draw(img)

    title_font,   title_text   = fit_text(draw, title,   bold_path,   title_size,   max_text_w)
    price_font,   price_text   = fit_text(draw, price,   bold_path,   price_size,   max_text_w)
    tagline_font, tagline_text = fit_text(draw, tagline, italic_path, tagline_size, max_text_w, italic=True)

    # ── Vertical text positions (relative to H) ───────────────────────────────
    # Bar spans H*0.72 → H*1.00 (28% of height)
    # Positions from spec: title=0.74, price=0.82, tagline=0.91
    title_y   = int(H * 0.74)
    price_y   = int(H * 0.82)
    tagline_y = int(H * 0.91)

    # ── Draw text layers ──────────────────────────────────────────────────────
    # Title: white bold
    draw_centered(draw, title_text, title_font, title_y, W, color=(255, 255, 255, 255))

    # Price: gold (#FFD700) bold — most prominent
    draw_centered(draw, price_text, price_font, price_y, W, color=(255, 215, 0, 255))

    # Tagline: white italic (or best available)
    draw_centered(draw, tagline_text, tagline_font, tagline_y, W, color=(255, 255, 255, 230))

    # ── Convert to RGB and save ───────────────────────────────────────────────
    # JPEG does not support alpha, so flatten to white background first
    background = Image.new("RGB", (W, H), (255, 255, 255))
    background.paste(img.convert("RGB"), (0, 0))
    background.save(output_path, "JPEG", quality=95, optimize=True, progressive=True)

    print(f"SUCCESS: output saved to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply branded text overlay to a product image for Instagram."
    )
    parser.add_argument("--input",   required=True, help="Path to source image (JPG/PNG/WEBP)")
    parser.add_argument("--output",  required=True, help="Path for branded output JPEG")
    parser.add_argument("--title",   required=True, help="Product name — shown at top of bar")
    parser.add_argument("--price",   required=True, help="Price string e.g. ₹999 or $29.99")
    parser.add_argument("--tagline", required=True, help="Supporting subtitle text")
    args = parser.parse_args()

    # Validate input exists
    if not Path(args.input).exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Ensure output directory exists
    out_dir = Path(args.output).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        process_image(
            input_path=args.input,
            output_path=args.output,
            title=args.title,
            price=args.price,
            tagline=args.tagline,
        )
    except Exception:
        print("ERROR: Image overlay failed — traceback below:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
