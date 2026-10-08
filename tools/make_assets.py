"""Generate the image assets: the app icon (PNG + multi-size ICO) and small UI glyphs.

Run from the repository root:  python tools/make_assets.py
The generated files are committed, so this only needs to run when the design changes.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "heic2jpeg" / "resources"
SCALE = 4  # draw large, then downsample for smooth edges
SIZE = 256 * SCALE


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient(size, top_left, bottom_right):
    """Diagonal gradient square."""
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            px[x, y] = lerp(top_left, bottom_right, (x + y) / 126)
    return small.resize((size, size), Image.Resampling.BICUBIC)


def rounded_mask(size, box, radius):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    return mask


def draw_icon() -> Image.Image:
    s = SCALE
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # Background tile with a soft drop shadow.
    tile_box = (16 * s, 16 * s, 240 * s, 240 * s)
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (16 * s, 22 * s, 240 * s, 246 * s), radius=52 * s, fill=(20, 30, 70, 90)
    )
    icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6 * s)))
    bg = gradient(SIZE, (64, 140, 255), (124, 77, 255)).convert("RGBA")
    icon.paste(bg, (0, 0), rounded_mask(SIZE, tile_box, 52 * s))

    # White "photo" card.
    card_box = (52 * s, 62 * s, 204 * s, 186 * s)
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle(card_box, radius=18 * s, fill=(255, 255, 255, 255))

    # Picture inside the card: sky, sun and mountains, clipped to the card interior.
    inner_box = (64 * s, 74 * s, 192 * s, 174 * s)
    scene = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scene)
    sky = gradient(SIZE, (214, 230, 255), (232, 222, 255)).convert("RGBA")
    scene.paste(sky, (0, 0))
    sd.ellipse((146 * s, 86 * s, 172 * s, 112 * s), fill=(255, 196, 61, 255))
    sd.polygon([(58 * s, 178 * s), (108 * s, 112 * s), (158 * s, 178 * s)], fill=(76, 110, 245, 255))
    sd.polygon([(112 * s, 178 * s), (152 * s, 128 * s), (198 * s, 178 * s)], fill=(124, 77, 255, 255))
    icon.paste(scene, (0, 0), rounded_mask(SIZE, inner_box, 10 * s))

    # Small arrow badge in the corner hinting at "convert".
    badge_box = (162 * s, 150 * s, 226 * s, 214 * s)
    d.ellipse(badge_box, fill=(255, 255, 255, 255))
    d.ellipse((168 * s, 156 * s, 220 * s, 208 * s), fill=(22, 163, 74, 255))
    cx, cy = 194 * s, 182 * s
    d.line([(cx - 12 * s, cy), (cx + 10 * s, cy)], fill=(255, 255, 255, 255), width=7 * s)
    d.polygon(
        [(cx + 14 * s, cy), (cx + 2 * s, cy - 12 * s), (cx + 2 * s, cy + 12 * s)],
        fill=(255, 255, 255, 255),
    )

    return icon.resize((256, 256), Image.Resampling.LANCZOS)


def draw_check(px: int) -> Image.Image:
    """White check mark for checked checkboxes (drawn on the accent colour)."""
    big = px * SCALE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    points = [(0.22 * big, 0.52 * big), (0.42 * big, 0.71 * big), (0.78 * big, 0.31 * big)]
    ImageDraw.Draw(image).line(points, fill=(255, 255, 255, 255), width=round(0.13 * big), joint="curve")
    return image.resize((px, px), Image.Resampling.LANCZOS)


def draw_chevron(px: int, color: tuple[int, int, int]) -> Image.Image:
    """Down-pointing chevron for combo boxes."""
    big = px * SCALE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    points = [(0.2 * big, 0.36 * big), (0.5 * big, 0.66 * big), (0.8 * big, 0.36 * big)]
    ImageDraw.Draw(image).line(points, fill=(*color, 255), width=round(0.12 * big), joint="curve")
    return image.resize((px, px), Image.Resampling.LANCZOS)


def save_hidpi(draw, name: str, px: int, *args) -> None:
    """Save name.png and name@2x.png; Qt picks the sharp one on high-DPI screens."""
    draw(px, *args).save(OUT_DIR / f"{name}.png")
    draw(px * 2, *args).save(OUT_DIR / f"{name}@2x.png")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    save_hidpi(draw_check, "check", 18)
    save_hidpi(draw_chevron, "chevron-light", 14, (102, 112, 133))
    save_hidpi(draw_chevron, "chevron-dark", 14, (154, 164, 182))
    icon = draw_icon()
    icon.save(OUT_DIR / "app.png")
    icon.save(
        OUT_DIR / "app.ico",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Wrote assets to {OUT_DIR}")


if __name__ == "__main__":
    main()
