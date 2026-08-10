from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUTPUT = Path(__file__).with_name("font-comparison.png")
FONT_DIR = Path(r"F:\Font")
FONTS = [
    ("01", "Peter Obscure", FONT_DIR / "0204-LNTH-PeterObscure.ttf", "#ff4fd8"),
    ("02", "Darley Sans", FONT_DIR / "DarleySans-Regular.otf", "#00c9c8"),
    ("03", "Minecraft Regular", FONT_DIR / "MinecraftRegular-3.ttf", "#ffd928"),
]

WIDTH = 1500
HEIGHT = 1130
PAPER = "#f4efd9"
INK = "#111111"
MUTED = "#665f52"

ui_regular = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 22)
ui_bold = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 26)
ui_small = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 17)


def load_fit(draw, path, text, max_size, max_width):
    size = max_size
    while size >= 14:
        font = ImageFont.truetype(str(path), size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
        size -= 2
    return ImageFont.truetype(str(path), 14)


image = Image.new("RGB", (WIDTH, HEIGHT), "#817e77")
draw = ImageDraw.Draw(image)

draw.rounded_rectangle((36, 32, WIDTH - 36, HEIGHT - 32), radius=8, fill=PAPER, outline=INK, width=5)
draw.text((76, 68), "SO SÁNH FONT CHO RETRO ADMIN DASHBOARD", font=ui_bold, fill=INK)
draw.text(
    (76, 107),
    "Mẫu thử trực tiếp với tiêu đề, nhãn và nội dung tiếng Việt.",
    font=ui_regular,
    fill=MUTED,
)

card_top = 162
card_height = 284
gap = 26

for index, (number, name, path, accent) in enumerate(FONTS):
    top = card_top + index * (card_height + gap)
    bottom = top + card_height

    draw.rounded_rectangle(
        (72, top, WIDTH - 72, bottom),
        radius=7,
        fill="#fffdf3",
        outline=INK,
        width=4,
    )
    draw.rectangle((72, top, 158, bottom), fill=accent, outline=INK, width=4)
    draw.text((96, top + 24), number, font=ui_bold, fill=INK)
    draw.text((188, top + 20), name, font=ui_bold, fill=INK)
    draw.text((188, top + 57), path.name, font=ui_small, fill=MUTED)

    sample_x = 188
    sample_width = WIDTH - 278
    heading = "TỔNG QUAN HỆ THỐNG"
    heading_font = load_fit(draw, path, heading, 54, sample_width)
    body_font = ImageFont.truetype(str(path), 25)
    small_font = ImageFont.truetype(str(path), 19)

    draw.text((sample_x, top + 94), heading, font=heading_font, fill=INK)
    draw.line((sample_x, top + 161, WIDTH - 104, top + 161), fill=INK, width=3)
    draw.text(
        (sample_x, top + 176),
        "Hành động chờ duyệt · Kết nối ổn định · Bộ nhớ & lưu trữ",
        font=body_font,
        fill=INK,
    )
    draw.text(
        (sample_x, top + 224),
        "Nguyễn Nhật Tân  •  1.842 sự kiện  •  Cập nhật 5 phút trước",
        font=small_font,
        fill=MUTED,
    )

draw.text(
    (76, HEIGHT - 72),
    "Đánh giá theo 3 tiêu chí: chất retro · khả năng đọc · độ phù hợp với dashboard quản trị.",
    font=ui_small,
    fill=INK,
)

image.save(OUTPUT)
print(OUTPUT)
