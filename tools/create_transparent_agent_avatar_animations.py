#!/usr/bin/env python3
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


OUT_DIR = Path(
    "/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts"
)

SIZE = 768
FRAME_COUNT = 40
DURATION_MS = 70

JOBS = [
    {
        "slug": "analysis",
        "src": OUT_DIR / "finsight-analysis-avatar-static-draft.png",
        "accent": (90, 170, 255),
        "secondary": (255, 194, 80),
        "rect": (0.08, 0.05, 0.84, 0.94),
        "expand": 4,
    },
    {
        "slug": "factchecker",
        "src": OUT_DIR / "finsight-factchecker-v2a-avatar-static.png",
        "accent": (34, 197, 94),
        "secondary": (110, 231, 183),
        "rect": (0.07, 0.05, 0.86, 0.94),
        "expand": 4,
    },
    {
        "slug": "tracking",
        "src": OUT_DIR / "finsight-tracking-v2a-avatar-static.png",
        "accent": (20, 184, 166),
        "secondary": (249, 115, 22),
        "rect": (0.07, 0.05, 0.86, 0.94),
        "expand": 4,
    },
]


def fit_square(image: Image.Image, size: int) -> Image.Image:
    image = image.convert("RGB")
    scale = size / min(image.size)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - size) // 2
    top = (resized.height - size) // 2
    return resized.crop((left, top, left + size, top + size))


def remove_background(image: Image.Image, job: dict) -> Image.Image:
    rgb = np.array(image.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    rx, ry, rw, rh = job["rect"]
    rect = (
        round(w * rx),
        round(h * ry),
        round(w * rw),
        round(h * rh),
    )
    mask = np.full((h, w), cv2.GC_PR_BGD, dtype=np.uint8)
    border = round(w * 0.025)
    mask[:border, :] = cv2.GC_BGD
    mask[-border:, :] = cv2.GC_BGD
    mask[:, :border] = cv2.GC_BGD
    mask[:, -border:] = cv2.GC_BGD

    fg_x1 = round(w * 0.22)
    fg_x2 = round(w * 0.78)
    fg_y1 = round(h * 0.12)
    fg_y2 = round(h * 0.9)
    mask[fg_y1:fg_y2, fg_x1:fg_x2] = cv2.GC_PR_FGD

    bg_model = np.zeros((1, 65), np.float64)
    fg_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(bgr, mask, rect, bg_model, fg_model, 7, cv2.GC_INIT_WITH_MASK)

    alpha = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_OPEN, kernel, iterations=1)
    alpha = cv2.dilate(alpha, kernel, iterations=job.get("expand", 3))
    alpha = cv2.GaussianBlur(alpha, (0, 0), 1.3)

    rgba = np.dstack([rgb, alpha])
    return Image.fromarray(rgba, "RGBA")


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
    inset = round(size * (0.18 - pulse * 0.012))
    halo_draw.ellipse(
        (inset, inset, size - inset, size - inset),
        outline=(*accent, round(30 + pulse * 24)),
        width=round(size * 0.008),
    )
    halo = halo.filter(ImageFilter.GaussianBlur(round(size * 0.018)))
    layer = Image.alpha_composite(layer, halo)

    for idx in range(3):
        angle = phase * (0.55 + idx * 0.08) + idx * 2.1
        radius = size * (0.33 + idx * 0.035)
        x = size / 2 + math.cos(angle) * radius
        y = size / 2 + math.sin(angle) * radius * 0.78
        dot = round(size * (0.008 + idx * 0.002))
        draw.ellipse(
            (x - dot, y - dot, x + dot, y + dot),
            fill=(*secondary, round(52 + pulse * 38)),
        )

    return layer


def make_frames(job: dict) -> list[Image.Image]:
    square = fit_square(Image.open(job["src"]), SIZE)
    base = remove_background(square, job)
    base.save(OUT_DIR / f"finsight-{job['slug']}-avatar-transparent.png")

    frames: list[Image.Image] = []
    for idx in range(FRAME_COUNT):
        phase = 2 * math.pi * idx / FRAME_COUNT
        breath = 1.0 + 0.011 * ((math.sin(phase) + 1) / 2)
        bob = round(-SIZE * 0.005 * math.sin(phase))
        frame = scale_center(base, breath, bob)
        frame = ImageEnhance.Brightness(frame).enhance(1.0 + 0.012 * math.sin(phase + 0.35))
        frame = Image.alpha_composite(halo_layer(SIZE, phase, job), frame)
        frames.append(frame.convert("RGBA"))
    return frames


def save_animation(job: dict) -> None:
    frames = make_frames(job)
    webp = OUT_DIR / f"finsight-{job['slug']}-avatar-animated-transparent.webp"
    gif = OUT_DIR / f"finsight-{job['slug']}-avatar-animated-transparent.gif"
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
