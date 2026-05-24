#!/usr/bin/env python3
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


OUT_DIR = Path(
    "/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts"
)

SIZE = 768
FRAME_COUNT = 48
DURATION_MS = 64

JOBS = [
    {
        "slug": "analysis",
        "src": OUT_DIR / "finsight-analysis-avatar-static-draft.png",
        "accent": (90, 170, 255),
        "secondary": (255, 194, 80),
        "shape": "graph",
    },
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


def halo_layer(size: int, phase: float, job: dict) -> Image.Image:
    accent = job["accent"]
    secondary = job["secondary"]
    pulse = (math.sin(phase) + 1) / 2
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    halo_draw = ImageDraw.Draw(halo)
    inset = round(size * (0.16 - pulse * 0.014))
    halo_draw.ellipse(
        (inset, inset, size - inset, size - inset),
        outline=(*accent, round(38 + pulse * 26)),
        width=round(size * 0.009),
    )
    halo = halo.filter(ImageFilter.GaussianBlur(round(size * 0.021)))
    layer = Image.alpha_composite(layer, halo)

    if job["shape"] == "graph":
        pts = []
        for i in range(-1, 8):
            x = round(i * size / 6 + pulse * size * 0.028)
            y = round(size * (0.56 - 0.11 * math.sin(i * 1.2 + phase)))
            pts.append((x, y))
        draw.line(pts, fill=(*secondary, round(20 + pulse * 24)), width=round(size * 0.006))
        x = round(size * (0.68 + 0.12 * ((phase / (2 * math.pi)) % 1)))
        draw.line(
            (x, round(size * 0.08), x + round(size * 0.12), round(size * 0.25)),
            fill=(*secondary, 18),
            width=round(size * 0.005),
        )

    elif job["shape"] == "scan":
        progress = (phase / (2 * math.pi)) % 1
        y = round(size * (0.22 + 0.48 * progress))
        draw.rectangle((round(size * 0.16), y, round(size * 0.84), y + 3), fill=(*secondary, 28))
        draw.rectangle((round(size * 0.2), y + 12, round(size * 0.8), y + 14), fill=(*accent, 18))
        for i in range(3):
            x = round(size * (0.2 + i * 0.22 + 0.018 * math.sin(phase + i)))
            top = round(size * (0.17 + i * 0.12))
            draw.rounded_rectangle(
                (x, top, x + round(size * 0.105), top + round(size * 0.03)),
                radius=round(size * 0.01),
                outline=(*accent, round(28 + pulse * 24)),
                width=2,
            )

    else:
        cx = cy = size // 2
        angle = phase * 0.72
        for idx, radius in enumerate((0.26, 0.34, 0.42)):
            r = round(size * radius)
            alpha = round(16 + pulse * 18 - idx * 3)
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(*accent, alpha), width=2)
        x2 = cx + round(math.cos(angle) * size * 0.42)
        y2 = cy + round(math.sin(angle) * size * 0.42)
        draw.line((cx, cy, x2, y2), fill=(*secondary, 34), width=round(size * 0.005))
        for idx in range(4):
            a = angle + idx * 1.35
            r = round(size * (0.25 + idx * 0.045))
            x = cx + round(math.cos(a) * r)
            y = cy + round(math.sin(a) * r)
            dot = round(size * 0.008)
            draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=(*secondary, round(56 + pulse * 42)))

    return layer.filter(ImageFilter.GaussianBlur(0.25))


def make_frames(job: dict) -> list[Image.Image]:
    base = fit_square(Image.open(job["src"]), SIZE)
    frames: list[Image.Image] = []
    for idx in range(FRAME_COUNT):
        phase = 2 * math.pi * idx / FRAME_COUNT
        breath = 1.0 + 0.01 * ((math.sin(phase) + 1) / 2)
        bob = round(-SIZE * 0.004 * math.sin(phase))
        frame = scale_center(base, breath, bob)
        frame = ImageEnhance.Brightness(frame).enhance(1.0 + 0.012 * math.sin(phase + 0.35))
        frame = Image.alpha_composite(frame, halo_layer(SIZE, phase, job))
        frames.append(frame.convert("RGBA"))
    return frames


def save_animation(job: dict) -> None:
    frames = make_frames(job)
    webp = OUT_DIR / f"finsight-{job['slug']}-avatar-animated.webp"
    gif = OUT_DIR / f"finsight-{job['slug']}-avatar-animated.gif"
    frames[0].save(
        webp,
        save_all=True,
        append_images=frames[1:],
        duration=DURATION_MS,
        loop=0,
        lossless=False,
        quality=98,
        method=6,
        exact=True,
    )
    frames[0].save(
        gif,
        save_all=True,
        append_images=frames[1:],
        duration=DURATION_MS,
        loop=0,
        optimize=False,
    )
    print(webp)
    print(gif)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for job in JOBS:
        save_animation(job)


if __name__ == "__main__":
    main()
