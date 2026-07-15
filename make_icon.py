"""Generate icon.ico — red rounded badge with a white play triangle."""
from PIL import Image, ImageDraw

BASE = 256


def draw_icon(size=BASE):
    ss = 4  # supersample for smooth edges
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # rounded square, vertical red gradient
    pad = int(s * 0.04)
    radius = int(s * 0.24)
    top, bottom = (255, 61, 61), (218, 24, 24)
    grad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / s
        c = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
        gd.line([(0, y), (s, y)], fill=c + (255,))
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([pad, pad, s - pad, s - pad], radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # white play triangle, optically centered
    cx, cy = s / 2 + s * 0.03, s / 2
    w, h = s * 0.30, s * 0.34
    d.polygon(
        [(cx - w / 2, cy - h / 2), (cx - w / 2, cy + h / 2), (cx + w / 2, cy)],
        fill=(255, 255, 255, 255),
    )
    return img.resize((size, size), Image.LANCZOS)


icon = draw_icon()
icon.save(
    "icon.ico",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
icon.save("icon.png")
print("icon.ico / icon.png generated")
