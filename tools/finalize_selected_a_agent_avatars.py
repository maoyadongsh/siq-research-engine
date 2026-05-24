#!/usr/bin/env python3
from __future__ import annotations

import math
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


ASSET_DIR = Path("/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts")
CANDIDATE_DIR = ASSET_DIR / "beauty-candidates"

SIZE = 768
FRAME_COUNT = 44
DURATION_MS = 68

JOBS = [
    {
        "slug": "analysis",
        "src": CANDIDATE_DIR / "finsight-analysis-a-source-magenta.png",
        "accent": (90, 170, 255),
        "secondary": (255, 194, 80),
        "shape": "graph",
    },
    {
        "slug": "factchecker",
        "src": CANDIDATE_DIR / "finsight-factchecker-a-source-magenta.png",
        "accent": (34, 197, 94),
        "secondary": (110, 231, 183),
        "shape": "scan",
    },
    {
        "slug": "tracking",
        "src": CANDIDATE_DIR / "finsight-tracking-a-source-magenta.png",
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


def sample_background_key(rgb: np.ndarray) -> np.ndarray:
    samples = np.concatenate(
        [
            rgb[:32, :32].reshape(-1, 3),
            rgb[:32, -32:].reshape(-1, 3),
            rgb[-32:, :32].reshape(-1, 3),
            rgb[-32:, -32:].reshape(-1, 3),
        ],
        axis=0,
    )
    return np.median(samples, axis=0).astype(np.float32)


def remove_small_alpha_islands(alpha_arr: np.ndarray, min_area: int = 1800) -> np.ndarray:
    h, w = alpha_arr.shape
    visible = alpha_arr > 8
    visited = np.zeros((h, w), dtype=bool)
    cleaned = alpha_arr.copy()

    for start_y in range(h):
        for start_x in range(w):
            if visited[start_y, start_x] or not visible[start_y, start_x]:
                continue
            pixels: list[tuple[int, int]] = []
            queue: deque[tuple[int, int]] = deque([(start_y, start_x)])
            visited[start_y, start_x] = True
            while queue:
                y, x = queue.popleft()
                pixels.append((y, x))
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and visible[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((ny, nx))
            if len(pixels) < min_area:
                ys, xs = zip(*pixels)
                cleaned[np.array(ys), np.array(xs)] = 0
    return cleaned


def remove_magenta(image: Image.Image) -> Image.Image:
    image = fit_square(image, SIZE)
    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    rgb = rgba[:, :, :3].astype(np.float32)
    key = sample_background_key(rgb)
    diff = rgb - key
    dist = np.sqrt((diff ** 2).sum(axis=2))

    # Only remove the magenta area connected to the image boundary, so pink lips,
    # skin blush, and warm clothing accents are preserved.
    background_like = dist < 145.0
    h, w = background_like.shape
    connected = np.zeros((h, w), dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    for x in range(w):
        if background_like[0, x]:
            queue.append((0, x))
        if background_like[h - 1, x]:
            queue.append((h - 1, x))
    for y in range(h):
        if background_like[y, 0]:
            queue.append((y, 0))
        if background_like[y, w - 1]:
            queue.append((y, w - 1))

    while queue:
        y, x = queue.popleft()
        if connected[y, x] or not background_like[y, x]:
            continue
        connected[y, x] = True
        if y > 0:
            queue.append((y - 1, x))
        if y + 1 < h:
            queue.append((y + 1, x))
        if x > 0:
            queue.append((y, x - 1))
        if x + 1 < w:
            queue.append((y, x + 1))

    bg_mask = Image.fromarray((connected * 255).astype(np.uint8), "L")
    bg_mask = bg_mask.filter(ImageFilter.MaxFilter(3))
    soft_bg = bg_mask.filter(ImageFilter.GaussianBlur(0.8))
    alpha = Image.eval(soft_bg, lambda px: 255 - px)

    edge = (np.array(soft_bg).astype(np.float32) / 255.0)[:, :, None]
    rgb[:, :, 0:1] = np.clip(rgb[:, :, 0:1] - edge * 145, 0, 255)
    rgb[:, :, 2:3] = np.clip(rgb[:, :, 2:3] - edge * 145, 0, 255)
    rgb[:, :, 1:2] = np.clip(rgb[:, :, 1:2] + edge * 18, 0, 255)

    # Force fully transparent image boundaries. This prevents faint color haze in
    # transparent WebP frames and makes browser compositing match assistant.
    alpha_arr = np.array(alpha)
    alpha_arr[:2, :] = 0
    alpha_arr[-2:, :] = 0
    alpha_arr[:, :2] = 0
    alpha_arr[:, -2:] = 0
    alpha_arr = remove_small_alpha_islands(alpha_arr)

    result = Image.fromarray(np.dstack([rgb.astype(np.uint8), alpha_arr]), "RGBA")
    return result


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
    inset = round(size * (0.18 - pulse * 0.012))
    halo_draw.ellipse(
        (inset, inset, size - inset, size - inset),
        outline=(*accent, round(32 + pulse * 26)),
        width=round(size * 0.008),
    )
    halo = halo.filter(ImageFilter.GaussianBlur(round(size * 0.02)))
    layer = Image.alpha_composite(layer, halo)

    if job["shape"] == "graph":
        pts = []
        for i in range(-1, 8):
            x = round(i * size / 6 + pulse * size * 0.026)
            y = round(size * (0.58 - 0.10 * math.sin(i * 1.2 + phase)))
            pts.append((x, y))
        draw.line(pts, fill=(*secondary, round(18 + pulse * 22)), width=round(size * 0.005))
    elif job["shape"] == "scan":
        progress = (phase / (2 * math.pi)) % 1
        y = round(size * (0.23 + 0.46 * progress))
        draw.rectangle((round(size * 0.2), y, round(size * 0.8), y + 3), fill=(*secondary, 26))
        draw.rectangle((round(size * 0.24), y + 12, round(size * 0.76), y + 14), fill=(*accent, 18))
    else:
        cx = cy = size // 2
        angle = phase * 0.72
        for idx, radius in enumerate((0.26, 0.34, 0.42)):
            r = round(size * radius)
            alpha = round(16 + pulse * 18 - idx * 3)
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(*accent, alpha), width=2)
        x2 = cx + round(math.cos(angle) * size * 0.42)
        y2 = cy + round(math.sin(angle) * size * 0.42)
        draw.line((cx, cy, x2, y2), fill=(*secondary, 32), width=round(size * 0.005))

    for idx in range(2):
        angle = phase * (0.65 + idx * 0.08) + idx * 2.4
        radius = size * (0.33 + idx * 0.055)
        x = size / 2 + math.cos(angle) * radius
        y = size / 2 + math.sin(angle) * radius * 0.78
        dot = round(size * (0.006 + idx * 0.002))
        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=(*secondary, round(45 + pulse * 38)))

    return layer


def make_frames(job: dict, base: Image.Image) -> list[Image.Image]:
    frames: list[Image.Image] = []
    for idx in range(FRAME_COUNT):
        phase = 2 * math.pi * idx / FRAME_COUNT
        breath = 1.0 + 0.010 * ((math.sin(phase) + 1) / 2)
        bob = round(-SIZE * 0.0045 * math.sin(phase))
        frame = scale_center(base, breath, bob)
        frame = ImageEnhance.Brightness(frame).enhance(1.0 + 0.012 * math.sin(phase + 0.35))
        frame = Image.alpha_composite(effect_layer(SIZE, phase, job), frame)
        frames.append(frame.convert("RGBA"))
    return frames


def save_animation(job: dict, base: Image.Image) -> None:
    frames = make_frames(job, base)
    webp = ASSET_DIR / f"finsight-{job['slug']}-avatar-animated-transparent.webp"
    gif = ASSET_DIR / f"finsight-{job['slug']}-avatar-animated-transparent.gif"
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
    for job in JOBS:
        source = Image.open(job["src"])
        transparent = remove_magenta(source)
        static_out = ASSET_DIR / f"finsight-{job['slug']}-avatar-transparent.png"
        selected_out = ASSET_DIR / f"finsight-{job['slug']}-avatar-selected-a-source-magenta.png"
        source.save(selected_out)
        transparent.save(static_out)
        print(selected_out)
        print(static_out)
        save_animation(job, transparent)


if __name__ == "__main__":
    main()
