"""
report_generator.py
Generates a "Daily Report on black paper" image for SP4RK's daily moderation reports.
Returns a BytesIO PNG so the caller can pass it straight to discord.File.
"""

import io
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFont

# ── Palette ────────────────────────────────────────────────────────────────────
BG          = (8,  8,  12)       # near-black paper
PANEL       = (16, 16, 24)       # slightly lighter panel
BORDER      = (40, 40, 60)       # subtle border
ACCENT      = (79, 142, 247)     # SP4RK blue
ACCENT2     = (108, 63, 197)     # purple
GOLD        = (255, 200, 60)     # highlight numbers
WHITE       = (230, 235, 255)    # primary text
MUTED       = (110, 115, 145)    # secondary text
RED         = (220, 60, 60)
GREEN       = (60, 200, 100)
DIVIDER     = (35, 35, 55)


def _font(size: int) -> ImageFont.ImageFont:
    """Return the best available font at the requested point size."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/nix/store",  # skip nix paths (too complex to discover)
    ]
    for path in candidates:
        if path.startswith("/nix"):
            continue
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    # Pillow 10+ supports size on the default font
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _font_regular(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        return draw.textlength(text, font=font)
    except Exception:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]


def _rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width=1):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill,
                           outline=outline, width=width)


def generate_report_image(
    period: str,
    date_str: str,
    warnings_total: int,
    member_count: int,
    uptime_str: str,
    active_features: int,
    total_features: int,
    recent_actions: int,
    bot_status: str = "Online",
) -> io.BytesIO:
    """
    Render the daily report image and return it as a BytesIO PNG.

    Parameters
    ----------
    period          : e.g. "🌅 Morning Report"
    date_str        : e.g. "2024-06-01"
    warnings_total  : int, all-time warning count
    member_count    : int, total guild members (-1 if unknown)
    uptime_str      : e.g. "5h 30m"
    active_features : number of ON features
    total_features  : total configurable features
    recent_actions  : mod actions logged in this report window
    bot_status      : "Online" | "Offline"
    """

    W, H = 820, 480
    img = Image.new("RGB", (W, H), BG)
    d   = ImageDraw.Draw(img)

    # ── Outer border ────────────────────────────────────────────────────────────
    _rounded_rect(d, [4, 4, W - 5, H - 5], 14, fill=None, outline=BORDER, width=2)

    # ── Top accent bar ──────────────────────────────────────────────────────────
    _rounded_rect(d, [4, 4, W - 5, 56], 14, fill=PANEL, outline=BORDER, width=1)
    # Accent stripe
    d.rectangle([4, 44, W - 5, 56], fill=ACCENT)

    # Logo / title area
    f_title  = _font(22)
    f_bold   = _font(16)
    f_label  = _font(11)
    f_stat   = _font(28)
    f_unit   = _font_regular(12)
    f_small  = _font_regular(11)

    d.text((22, 14), "⚡ SP4RK", font=f_title, fill=WHITE)
    # "DAILY REPORT" badge right-aligned
    badge_text = "DAILY MODERATION REPORT"
    bw = _text_w(d, badge_text, f_bold)
    d.text((W - bw - 22, 19), badge_text, font=f_bold, fill=ACCENT)

    # ── Date / Period row ───────────────────────────────────────────────────────
    d.text((22, 66), f"📅  {date_str}", font=f_unit, fill=MUTED)
    period_w = _text_w(d, period, f_bold)
    d.text((W - period_w - 22, 63), period, font=f_bold, fill=GOLD)

    # ── Divider ─────────────────────────────────────────────────────────────────
    d.line([(20, 96), (W - 20, 96)], fill=DIVIDER, width=1)

    # ── Stats panels (two rows of 3) ────────────────────────────────────────────
    stats = [
        ("⚠️  Warnings", str(warnings_total),       "all-time",  GOLD  if warnings_total > 0 else GREEN),
        ("👥  Members",  str(member_count) if member_count >= 0 else "N/A", "in server", ACCENT),
        ("⏱️  Uptime",   uptime_str,                 "since boot", GREEN),
        ("🛡️  Features", f"{active_features}/{total_features}", "active",  ACCENT2),
        ("📋  Actions",  str(recent_actions),        "this window", MUTED if recent_actions == 0 else WHITE),
        ("🔴  Status"  if bot_status != "Online" else "🟢  Status",
                         bot_status, "bot",         GREEN if bot_status == "Online" else RED),
    ]

    col_w   = (W - 40) // 3
    row_h   = 130
    padding = 10

    for i, (label, value, unit, color) in enumerate(stats):
        row = i // 3
        col = i  % 3
        x0 = 20 + col * col_w
        y0 = 108 + row * row_h
        x1 = x0 + col_w - padding
        y1 = y0 + row_h - padding

        _rounded_rect(d, [x0, y0, x1, y1], 10, fill=PANEL, outline=BORDER, width=1)

        # label
        d.text((x0 + 14, y0 + 12), label, font=f_label, fill=MUTED)
        # value (large)
        try:
            f_val = _font(30)
        except Exception:
            f_val = f_stat
        vw = _text_w(d, value, f_val)
        panel_w = x1 - x0
        d.text((x0 + (panel_w - vw) // 2, y0 + 38), value, font=f_val, fill=color)
        # unit (small, centered)
        uw = _text_w(d, unit, f_small)
        d.text((x0 + (panel_w - uw) // 2, y0 + 80), unit, font=f_small, fill=MUTED)

    # ── Bottom divider ──────────────────────────────────────────────────────────
    d.line([(20, H - 52), (W - 20, H - 52)], fill=DIVIDER, width=1)

    # ── Footer ──────────────────────────────────────────────────────────────────
    footer = "Byte Labs  ·  Automated by SP4RK  ·  This report is generated every 8 hours"
    fw = _text_w(d, footer, f_small)
    d.text(((W - fw) // 2, H - 36), footer, font=f_small, fill=MUTED)

    # ── Subtle corner watermark ─────────────────────────────────────────────────
    ts = datetime.utcnow().strftime("%H:%M UTC")
    d.text((W - _text_w(d, ts, f_small) - 22, H - 36), ts, font=f_small, fill=BORDER)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
