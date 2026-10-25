"""Build transparent glyph + horizontal wordmark lockup from the generated assets."""
from PIL import Image, ImageDraw, ImageFont

ROOT = "C:/Users/michael/Documents/pic2edit"
GLYPH = f"{ROOT}/Minimal_software_logo_GLYPH_wi_2026-10-25T10-41-17.png"
FONT = "C:/Windows/Fonts/arialbd.ttf"  # bold, clean latin wordmark


def white_to_transparent(src_path, dst_path):
    im = Image.open(src_path).convert("RGB")
    w, h = im.size
    px = im.load()
    out = Image.new("RGBA", (w, h))
    opx = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            lum = (r + g + b) / 3.0
            if lum >= 248:           # near-white background -> transparent
                a = 0
            elif lum >= 230:         # soft anti-alias fade
                a = int((lum - 230) / (248 - 230) * 255)
            else:
                a = 255
            opx[x, y] = (r, g, b, a)
    out.save(dst_path)
    print("saved", dst_path, out.mode, out.size)


def horizontal_lockup(glyph_png, dst_path):
    glyph = Image.open(glyph_png).convert("RGBA")
    gh = 180
    gw = int(glyph.size[0] * gh / glyph.size[1])
    glyph = glyph.resize((gw, gh), Image.LANCZOS)

    font = ImageFont.truetype(FONT, 150)
    word = "pic2edit"
    tmp = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(tmp)
    tb = d.textbbox((0, 0), word, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]

    pad = 40
    gap = 36
    total_w = gw + gap + tw + pad * 2
    total_h = max(gh, th) + pad * 2
    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))

    # glyph left, vertically centered
    gy = (total_h - gh) // 2
    canvas.alpha_composite(glyph, (pad, gy))

    # wordmark right, vertically centered, deep blue
    tx = pad + gw + gap
    ty = (total_h - th) // 2 - tb[1]
    d2 = ImageDraw.Draw(canvas)
    d2.text((tx, ty), word, font=font, fill=(21, 101, 192, 255))
    canvas.save(dst_path)
    print("saved", dst_path, canvas.mode, canvas.size)


if __name__ == "__main__":
    white_to_transparent(GLYPH, f"{ROOT}/pic2edit-logo-transparent.png")
    horizontal_lockup(f"{ROOT}/pic2edit-logo-transparent.png",
                      f"{ROOT}/pic2edit-logo-horizontal.png")
