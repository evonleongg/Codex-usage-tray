"""Generate the application icon at multiple Windows-friendly sizes."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUT = Path(__file__).with_name("codex-usage.ico")
PNG = Path(__file__).with_name("codex-usage.png")
SIZE = 512


def font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in (
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(canvas)

# Two-layer rounded-square body: deliberately high contrast at tiny sizes.
draw.rounded_rectangle((30, 30, 482, 482), radius=112, fill="#0B1220")
draw.rounded_rectangle((42, 42, 470, 470), radius=100, fill="#182A4A")
draw.rounded_rectangle((56, 56, 456, 456), radius=88, fill="#172C52")

# A soft blue halo behind the letter and three usage bars.
draw.ellipse((88, 88, 424, 424), fill="#1E4B87")
draw.ellipse((102, 102, 410, 410), fill="#204F91")

# Crisp C mark.
mark = "C"
fnt = font(248)
bbox = draw.textbbox((0, 0), mark, font=fnt)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
draw.text(((SIZE - tw) / 2 - 6, 103 - bbox[1]), mark, font=fnt, fill="#F8FAFC")

# Usage bars make the purpose legible without relying on tiny text.
bar_x = 288
for x, top, color in ((bar_x, 315, "#59A5FF"), (bar_x + 38, 286, "#7DD3FC"), (bar_x + 76, 250, "#35D39E")):
    draw.rounded_rectangle((x, top, x + 21, 361), radius=10, fill=color)

canvas.save(PNG)
canvas.save(OUT, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)])
print(OUT)
