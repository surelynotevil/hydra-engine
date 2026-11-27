"""Puts one quote on the fixed mascot card and turns it into a 15-second silent Reel video.

The image never changes: only the quote text does. Music is attached by Instagram at publish time
(audio_configuration), so the video itself carries a silent audio track.
"""
import json, os, subprocess
from PIL import Image, ImageDraw, ImageFont

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import HEAD, CFG, FONTS, OUT  # noqa: E402


def _font(size):
    f = ImageFont.truetype(os.path.join(FONTS, "Lora.ttf"), size)
    try:
        f.set_variation_by_name("Medium")
    except Exception:  # noqa: BLE001
        pass
    return f


def _wrap(draw, text, font, width):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def card(quote, out_png):
    """Draw the quote in the plain top area of the head's card.png. Font shrinks for long quotes."""
    c = CFG["card"]
    im = Image.open(os.path.join(HEAD, "card.png")).convert("RGB")
    if im.size != (1080, 1920):
        im = im.resize((1080, 1920), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = c["text_box"]
    for size in range(c["font_max"], c["font_min"] - 1, -2):
        font = _font(size)
        lines = _wrap(d, quote, font, x1 - x0)
        lh = int(size * 1.32)
        if len(lines) * lh <= (y1 - y0):
            break
    total = len(lines) * lh
    y = y0 + ((y1 - y0) - total) // 2
    color = tuple(c["text_rgb"])
    for line in lines:
        w = d.textlength(line, font=font)
        d.text(((1080 - w) / 2, y), line, font=font, fill=color)
        y += lh
    hf = _font(c["handle_size"])
    handle = "@" + CFG["instagram_username"]
    hx, hy = c["handle_xy"]
    d.text(((1080 - d.textlength(handle, font=hf)) / 2 if hx < 0 else hx - d.textlength(handle, font=hf), hy), handle, font=hf, fill=tuple(c["handle_rgb"]))
    im.save(out_png)
    return out_png


def video(png, out_mp4, seconds=None):
    """Static image -> H.264 Reel (1080x1920, 30 fps) with a silent AAC track."""
    seconds = seconds or CFG["reel_seconds"]
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", png,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(seconds), "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", out_mp4,
    ], check=True)
    return out_mp4


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "I bought a plant to keep me company while I read. Now we both just sit here, slowly turning toward the window."
    os.makedirs(OUT, exist_ok=True)
    p = card(q, os.path.join(OUT, "test.png"))
    print(video(p, os.path.join(OUT, "test.mp4")))
