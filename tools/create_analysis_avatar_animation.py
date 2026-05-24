#!/usr/bin/env python3
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter


SRC = Path(
    "/home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts/"
    "finsight-analysis-avatar-static-draft.png"
)
OUT_DIR = Path(
    "/home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts"
)
WEBP_OUT = OUT_DIR / "finsight-analysis-avatar-animated-draft.webp"
GIF_OUT = OUT_DIR / "finsight-analysis-avatar-animated-draft.gif"


def fit_square(image: Image.Image, size: int) -> Image.Image:
    image = image.convert("RGBA")
    scale = size / min(image.size)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - size) // 2
    top = (resized.height - size) // 2
    return resized.crop((left, top, left + size, top + size))


def scale_center(image: Image.Image, scale: float, y_offset: int = 0) -> Image.Image:
    size = image.size[0]
    scaled_size = round(size * scale)
    enlarged = image.resize((scaled_size, scaled_size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - scaled_size) // 2
    y = (size - scaled_size) // 2 + y_offset
    canvas.alpha_composite(enlarged, (x, y))
    return canvas


def glow_layer(size: int, phase: float) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    pulse = (math.sin(phase) + 1) / 2
    alpha = round(26 + pulse * 34)

    # Slow-moving analyst graph highlight behind the head.
    pts = []
    for i in range(-1, 8):
        x = round(i * size / 6 + pulse * 20)
        y = round(size * (0.56 - 0.12 * math.sin(i * 1.2 + phase)))
        pts.append((x, y))
    draw.line(pts, fill=(255, 194, 80, alpha), width=4, joint="curve")

    # Soft blue halo for a premium chatbot-avatar feel.
    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    halo_draw = ImageDraw.Draw(halo)
    inset = round(size * (0.16 - pulse * 0.015))
    halo_draw.ellipse(
        (inset, inset, size - inset, size - inset),
        outline=(90, 170, 255, round(46 + pulse * 34)),
        width=5,
    )
    halo = halo.filter(ImageFilter.GaussianBlur(12))
    layer = Image.alpha_composite(layer, halo)
    return layer


def make_frames() -> list[Image.Image]:
    base = fit_square(Image.open(SRC), 512)
    frames: list[Image.Image] = []
    count = 44
    for idx in range(count):
        phase = 2 * math.pi * idx / count
        breath = 1.0 + 0.012 * ((math.sin(phase) + 1) / 2)
        bob = round(-2 * math.sin(phase))
        frame = scale_center(base, breath, bob)

        # Slight premium light variation without changing the face identity.
        brightness = 1.0 + 0.018 * math.sin(phase + 0.4)
        frame = ImageEnhance.Brightness(frame).enhance(brightness)

        layer = glow_layer(512, phase)
        frame = Image.alpha_composite(frame, layer)

        # A tiny diagonal specular sweep in the upper-right background.
        sweep = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(sweep)
        x = round(350 + 80 * ((idx % count) / count))
        draw.line((x, 40, x + 90, 180), fill=(255, 225, 140, 26), width=3)
        sweep = sweep.filter(ImageFilter.GaussianBlur(1.2))
        frame = Image.alpha_composite(frame, sweep)

        frames.append(frame.convert("RGBA"))
    return frames


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frames = make_frames()
    frames[0].save(
        WEBP_OUT,
        save_all=True,
        append_images=frames[1:],
        duration=70,
        loop=0,
        lossless=False,
        quality=90,
        method=6,
    )
    frames[0].save(
        GIF_OUT,
        save_all=True,
        append_images=frames[1:],
        duration=70,
        loop=0,
        optimize=True,
    )
    print(WEBP_OUT)
    print(GIF_OUT)


if __name__ == "__main__":
    main()
