"""Link-preview (Open Graph) images: 1200x630 PNG in the site's navy + tonal-blob style,
so pasting a share link into iMessage/GroupMe/Discord shows a card instead of a bare URL."""
import os

from PIL import Image, ImageDraw, ImageFont

FONTS = os.path.join(os.path.dirname(__file__), "static", "fonts")
WIDTH, HEIGHT = 1200, 630
NAVY, NAVY_HI, NAVY_LO = "#1e3264", "#2c4889", "#15264f"
LIME, WHITE = "#f4ff5c", "#ffffff"


def _inter(size: int, weight: str = "ExtraBold") -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(os.path.join(FONTS, "InterTight.ttf"), size)
    font.set_variation_by_name(weight)
    return font


def _fit(draw: ImageDraw.ImageDraw, text: str, font_for, start: int, max_width: int) -> ImageFont.FreeTypeFont:
    size = start
    while size > 18 and draw.textlength(text, font=font_for(size)) > max_width:
        size -= 2
    return font_for(size)


def render(path: str, heading: str, subtitle: str) -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    draw = ImageDraw.Draw(img)
    draw.ellipse((WIDTH - 470, -250, WIDTH + 170, 390), fill=NAVY_HI)
    draw.ellipse((WIDTH - 560, HEIGHT - 150, WIDTH - 300, HEIGHT + 110), fill=NAVY_LO)

    left, max_width = 72, WIDTH - 72 * 2
    title = ImageFont.truetype(os.path.join(FONTS, "ArchivoBlack-Regular.ttf"), 132)
    draw.text((left, 84), heading, font=_fit(draw, heading, lambda s: _inter(s), 44, max_width), fill=WHITE)
    draw.text((left, 150), "OBU STU", font=title, fill=LIME)
    draw.text((left, 290), "WRAPPED", font=title, fill=LIME)
    draw.text((left, 480), subtitle, font=_fit(draw, subtitle, lambda s: _inter(s), 38, max_width), fill=WHITE)
    draw.text((left, 545), "stu.exposed", font=_inter(30, "Bold"), fill="#c9d3ef")

    tmp = path + ".tmp"
    img.save(tmp, "PNG", optimize=True)
    os.replace(tmp, path)
