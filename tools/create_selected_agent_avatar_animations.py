#!/usr/bin/env python3
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


OUT_DIR = Path(
    "/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts"
)

JOBS = [
    {
        "slug": "factchecker",
        "src": OUT_DIR / "finsight-factchecker-v2a-avatar-static.png",
        "accent": (34, 197, 94),
        "secondary": (110, 231, 183),
        "shape": "scan",
    },
    {
        "slug": "tracking",
        "src": OUT_DIR / "finsight-tracking-v2a-avatar-static.png",
        "accent": (20, 184, 166),
        "secondary": (249, 115, 22),
        "shape": "radar",
    },
]


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


def effect_layer(size: int, phase: float, job: dict) -> Image.Image:
    accent = job["accent"]
    secondary = job["secondary"]
    pulse = (math.sin(phase) + 1) / 2
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    halo_draw = ImageDraw.Draw(halo)
    inset = round(size * (0.16 - pulse * 0.018))
    halo_draw.ellipse(
        (inset, inset, size - inset, size - inset),
        outline=(*accent, round(44 + pulse * 32)),
        width=5,
    )
    halo = halo.filter(ImageFilter.GaussianBlur(12))
    layer = Image.alpha_composite(layer, halo)

    if job["shape"] == "scan":
        y = round(size * (0.22 + 0.48 * ((phase / (2 * math.pi)) % 1)))
        draw.rectangle((80, y, size - 80, y + 3), fill=(*secondary, 34))
        draw.rectangle((105, y + 10, size - 105, y + 11), fill=(*accent, 22))
        for i in range(3):
            x = round(size * (0.2 + i * 0.22 + 0.02 * math.sin(phase + i)))
            draw.rounded_rectangle(
                (x, 88 + i * 62, x + 54, 104 + i * 62),
                radius=5,
                outline=(*accent, round(36 + pulse * 30)),
                width=2,
            )
    else:
        cx = cy = size // 2
        for idx, radius in enumerate((132, 176, 218)):
            alpha = round(20 + pulse * 20 - idx * 3)
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                outline=(*accent, alpha),
                width=2,
            )
        angle = phase * 0.7
        x2 = cx + round(math.cos(angle) * 220)
        y2 = cy + round(math.sin(angle) * 220)
        draw.line((cx, cy, x2, y2), fill=(*secondary, 40), width=3)
        for idx in range(4):
            a = angle + idx * 1.35
            r = 132 + idx * 26
            x = cx + round(math.cos(a) * r)
            y = cy + round(math.sin(a) * r)
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(*secondary, round(72 + pulse * 48)))

    return layer.filter(ImageFilter.GaussianBlur(0.2))


def make_frames(job: dict) -> list[Image.Image]:
    base = fit_square(Image.open(job["src"]), 512)
    frames: list[Image.Image] = []
    count = 44
    for idx in range(count):
        phase = 2 * math.pi * idx / count
        breath = 1.0 + 0.012 * ((math.sin(phase) + 1) / 2)
        bob = round(-2 * math.sin(phase))
        frame = scale_center(base, breath, bob)
        frame = ImageEnhance.Brightness(frame).enhance(1.0 + 0.015 * math.sin(phase + 0.35))
        frame = Image.alpha_composite(frame, effect_layer(512, phase, job))
        frames.append(frame.convert("RGBA"))
    return frames


def save_animation(job: dict) -> None:
    frames = make_frames(job)
    webp = OUT_DIR / f"finsight-{job['slug']}-avatar-animated-draft.webp"
    gif = OUT_DIR / f"finsight-{job['slug']}-avatar-animated-draft.gif"
    frames[0].save(
        webp,
        save_all=True,
        append_images=frames[1:],
        duration=70,
        loop=0,
        lossless=False,
        quality=90,
        method=6,
    )
    frames[0].save(
        gif,
        save_all=True,
        append_images=frames[1:],
        duration=70,
        loop=0,
        optimize=True,
    )
    print(webp)
    print(gif)


def main() -> None:
    for job in JOBS:
        save_animation(job)


if __name__ == "__main__":
    main()
