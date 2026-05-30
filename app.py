"""
app.py — Branded overlay microservice for Instagram Pipeline Phase 1
Runs on Render.com free tier (Python + FFmpeg available)

POST /process   → downloads Drive file, overlays text, returns binary
GET  /health    → liveness check
"""

import io
import os
import re
import subprocess
import tempfile
import traceback
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_file
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# ── Optional API key auth (set API_KEY env var on Render) ─────────────────────
API_KEY = os.environ.get("API_KEY", "")


def check_auth():
    if not API_KEY:
        return True  # no key configured → open (not recommended for production)
    return request.headers.get("X-API-Key") == API_KEY


# ── Font paths ─────────────────────────────────────────────────────────────────
BOLD_FONTS = [
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
ITALIC_FONTS = [
    "/usr/share/fonts/truetype/montserrat/Montserrat-Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def find_font(paths):
    return next((p for p in paths if Path(p).exists()), None)


def load_font(path, size):
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def measure(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def fit_text(draw, text, font_path, initial_size, max_width):
    size = initial_size
    min_size = max(8, initial_size // 4)
    while size >= min_size:
        font = load_font(font_path, size)
        w, _ = measure(draw, text, font)
        if w <= max_width:
            return font, text
        truncated = text
        while len(truncated) > 4:
            truncated = truncated[:-1]
            wt, _ = measure(draw, truncated + "...", font)
            if wt <= max_width:
                return font, truncated + "..."
        size -= max(1, size // 10)
    font = load_font(font_path, min_size)
    return font, text[:12] + "..."


def draw_gradient_bar(image, bar_height):
    W, H = image.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    bar_top = H - bar_height
    for y_offset in range(bar_height):
        alpha = int(190 * (y_offset / max(bar_height - 1, 1)))
        draw.line([(0, bar_top + y_offset), (W - 1, bar_top + y_offset)],
                  fill=(0, 0, 0, alpha))
    return Image.alpha_composite(image, overlay)


def draw_centered(draw, text, font, y, W, color):
    tw, _ = measure(draw, text, font)
    draw.text(((W - tw) // 2, y), text, font=font, fill=color)


def overlay_image_bytes(img_bytes, title, price, tagline):
    """Apply text overlay to raw image bytes. Returns JPEG bytes."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    W, H = img.size

    img = draw_gradient_bar(img, int(H * 0.28))

    bold_path   = find_font(BOLD_FONTS)
    italic_path = find_font(ITALIC_FONTS)
    max_w = int(W * 0.90)

    draw = ImageDraw.Draw(img)

    title_font,   title_text   = fit_text(draw, title,   bold_path,   int(H * 0.07), max_w)
    price_font,   price_text   = fit_text(draw, price,   bold_path,   int(H * 0.08), max_w)
    tagline_font, tagline_text = fit_text(draw, tagline, italic_path, int(H * 0.05), max_w)

    draw_centered(draw, title_text,   title_font,   int(H * 0.74), W, (255, 255, 255, 255))
    draw_centered(draw, price_text,   price_font,   int(H * 0.82), W, (255, 215,   0, 255))
    draw_centered(draw, tagline_text, tagline_font, int(H * 0.91), W, (255, 255, 255, 230))

    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=95, optimize=True, progressive=True)
    out.seek(0)
    return out


def overlay_video(input_path, output_path, title, price, tagline):
    """Run FFmpeg overlay on a video file. Raises on failure."""
    bold_font = find_font(BOLD_FONTS) or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    # Shell-escape text for FFmpeg drawtext (escape : \ ' special chars)
    def esc(s):
        return s.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")

    title_e   = esc(title)
    price_e   = esc(price)
    tagline_e = esc(tagline)

    vf = (
        f"drawbox=x=0:y=ih*0.72:w=iw:h=ih*0.28:color=black@0.75:t=fill,"
        f"drawtext=fontfile={bold_font}:text='{title_e}':"
        f"fontcolor=white:fontsize=h*0.07:x=(w-text_w)/2:y=h*0.74:box=0,"
        f"drawtext=fontfile={bold_font}:text='{price_e}':"
        f"fontcolor=#FFD700:fontsize=h*0.08:x=(w-text_w)/2:y=h*0.82:box=0,"
        f"drawtext=fontfile={bold_font}:text='{tagline_e}':"
        f"fontcolor=white:fontsize=h*0.05:x=(w-text_w)/2:y=h*0.91:box=0"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", vf,
        "-codec:a", "copy",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{result.stderr[-1000:]}")


def download_drive_file(url, timeout=60):
    """
    Download from a Google Drive direct URL.
    Handles the 'virus scan warning' redirect for large files.
    Returns raw bytes.
    """
    session = requests.Session()
    response = session.get(url, stream=True, timeout=timeout, allow_redirects=True)
    response.raise_for_status()

    # Google warns about large files with an HTML confirmation page
    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type:
        # Extract confirmation token and retry
        token_match = re.search(r'confirm=([0-9A-Za-z_\-]+)', response.text)
        if token_match:
            confirm_url = url + "&confirm=" + token_match.group(1)
            response = session.get(confirm_url, stream=True, timeout=timeout)
            response.raise_for_status()

    return response.content


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "insta-overlay-api"}), 200


@app.route("/process", methods=["POST"])
def process():
    if not check_auth():
        return jsonify({"error": "Unauthorized — X-API-Key header required"}), 401

    data = request.get_json(force=True, silent=True) or {}

    image_url = data.get("image_url", "").strip()
    title     = data.get("title", "").strip()
    price     = data.get("price", "").strip()
    tagline   = data.get("tagline", "").strip()
    file_type = data.get("file_type", "image").strip().lower()

    if not image_url:
        return jsonify({"error": "image_url is required"}), 400
    if not title and not price and not tagline:
        return jsonify({"error": "At least one of title/price/tagline is required"}), 400

    try:
        raw = download_drive_file(image_url)
    except Exception as e:
        return jsonify({"error": f"Download failed: {str(e)}"}), 502

    try:
        if file_type == "video":
            with tempfile.TemporaryDirectory() as tmp:
                in_path  = os.path.join(tmp, "input.mp4")
                out_path = os.path.join(tmp, "output.mp4")
                with open(in_path, "wb") as f:
                    f.write(raw)
                overlay_video(in_path, out_path, title, price, tagline)
                return send_file(
                    out_path,
                    mimetype="video/mp4",
                    as_attachment=True,
                    download_name="branded_output.mp4"
                )
        else:
            result = overlay_image_bytes(raw, title, price, tagline)
            return send_file(
                result,
                mimetype="image/jpeg",
                as_attachment=True,
                download_name="branded_output.jpg"
            )

    except Exception:
        tb = traceback.format_exc()
        app.logger.error(tb)
        return jsonify({"error": "Processing failed", "detail": tb[-500:]}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
