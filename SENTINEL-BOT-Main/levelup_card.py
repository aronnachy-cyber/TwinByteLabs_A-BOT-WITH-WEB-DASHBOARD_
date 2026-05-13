"""
levelup_card.py -- generates a level-up announcement image for SP4RK.

Usage (async):
    buf = await build_levelup_card(username, avatar_url, old_level, new_level)
    # buf is a discord.File-ready BytesIO
"""

from __future__ import annotations
import io
import os
import asyncio
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

_HERE    = os.path.dirname(os.path.abspath(__file__))
_BG_PATH = os.path.join(_HERE, "assets", "levelup_bg.png")
_FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), size)
    except Exception:
        return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def _shadow_centered(draw, y, text, font, fill, canvas_w, shadow_off=4):
    tw, _ = _text_size(draw, text, font)
    x = (canvas_w - tw) // 2
    draw.text((x + shadow_off, y + shadow_off), text, font=font, fill=(0, 0, 0, 180))
    draw.text((x, y), text, font=font, fill=fill)


def _make_circle_avatar(img: Image.Image, size: int) -> Image.Image:
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _add_ring(img: Image.Image, color: tuple, ring_w: int = 6) -> Image.Image:
    size = img.width
    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, size - 1, size - 1),
                                  outline=(*color, 255), width=ring_w)
    img.paste(ring, (0, 0), ring)
    return img


async def _fetch_avatar(url: str, size: int) -> Optional[Image.Image]:
    if not url:
        return None
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return _make_circle_avatar(Image.open(io.BytesIO(data)), size)
    except Exception as e:
        print(f"[LVL-CARD] avatar fetch failed: {e}")
    return None


async def build_levelup_card(
    username: str,
    avatar_url: str,
    old_level: int,
    new_level: int,
    guild_name: str = "",
) -> io.BytesIO:
    AVATAR_SIZE = 130
    avatar_task = asyncio.ensure_future(_fetch_avatar(avatar_url, AVATAR_SIZE))

    loop = asyncio.get_event_loop()
    bg, canvas_w, canvas_h = await loop.run_in_executor(
        None, _build_sync, username, old_level, new_level, guild_name, AVATAR_SIZE
    )

    avatar_img = await avatar_task
    if avatar_img:
        avatar_img = _add_ring(avatar_img, (255, 205, 50), ring_w=6)
        ax = (canvas_w - AVATAR_SIZE) // 2
        bg.paste(avatar_img, (ax, 50), avatar_img)

    buf = io.BytesIO()
    bg.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _build_sync(username: str, old_level: int, new_level: int,
                guild_name: str, avatar_size: int):
    bg = Image.open(_BG_PATH).convert("RGBA")
    canvas_w, canvas_h = bg.size  # 1382 x 768

    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 95))
    bg = Image.alpha_composite(bg, overlay)

    draw = ImageDraw.Draw(bg)

    WHITE      = (255, 255, 255, 255)
    GOLD       = (255, 210, 50,  255)
    LIGHT_GREY = (210, 210, 210, 255)

    f_title   = _font(80, bold=True)
    f_levels  = _font(62, bold=True)
    f_arrow   = _font(52, bold=True)
    f_congrats= _font(44, bold=True)
    f_user    = _font(36, bold=False)
    f_guild   = _font(24, bold=False)

    # glow behind "LEVEL UP!" title
    title_y   = avatar_size + 70
    title_txt = "LEVEL  UP!"
    glow_offsets = [(-3, -3), (3, 3), (-3, 3), (3, -3), (0, 3), (3, 0)]
    tw, _ = _text_size(draw, title_txt, f_title)
    tx = (canvas_w - tw) // 2
    for ox, oy in glow_offsets:
        draw.text((tx + ox, title_y + oy), title_txt, font=f_title,
                  fill=(255, 140, 10, 80))
    _shadow_centered(draw, title_y, title_txt, f_title, GOLD, canvas_w)

    # "Level X  ->  Level Y"
    lv_y      = title_y + 105
    from_txt  = f"Level {old_level}"
    arrow_txt = "  \u2192  "
    to_txt    = f"Level {new_level}"
    fw, _  = _text_size(draw, from_txt,  f_levels)
    aw, _  = _text_size(draw, arrow_txt, f_arrow)
    tw2, _ = _text_size(draw, to_txt,    f_levels)
    total  = fw + aw + tw2
    sx     = (canvas_w - total) // 2

    draw.text((sx + 4,        lv_y + 4), from_txt,  font=f_levels, fill=(0, 0, 0, 160))
    draw.text((sx + fw + 4,   lv_y + 4), arrow_txt, font=f_arrow,  fill=(0, 0, 0, 160))
    draw.text((sx + fw + aw + 4, lv_y + 4), to_txt, font=f_levels, fill=(0, 0, 0, 160))

    draw.text((sx,            lv_y), from_txt,  font=f_levels, fill=LIGHT_GREY)
    draw.text((sx + fw,       lv_y), arrow_txt, font=f_arrow,  fill=GOLD)
    draw.text((sx + fw + aw,  lv_y), to_txt,    font=f_levels, fill=WHITE)

    # "Congratulations!"
    cong_y = lv_y + 92
    _shadow_centered(draw, cong_y, "Congratulations!", f_congrats, GOLD, canvas_w)

    # "@username"
    user_y = cong_y + 68
    _shadow_centered(draw, user_y, f"@{username}", f_user, WHITE, canvas_w)

    # guild name bottom-right
    if guild_name:
        gw, _ = _text_size(draw, guild_name, f_guild)
        draw.text((canvas_w - gw - 36, canvas_h - 46), guild_name,
                  font=f_guild, fill=(180, 180, 180, 200))

    return bg, canvas_w, canvas_h
