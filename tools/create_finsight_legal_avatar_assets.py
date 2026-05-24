#!/usr/bin/env python3
from __future__ import annotations

import math
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


ASSET_DIR = Path("/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts")
CANDIDATE = Path(
    "/home/maoyd/.codex/generated_images/019e443b-be04-7fe0-9134-5fb5bdc53731/"
    "ig_01264d19e9fab05c016a0d6b0ab55c8197a9f40691f5c26531.png"
)

SIZE = 768
FRAME_COUNT = 44
DURATION_MS = 68
ACCENT = (212, 175, 55)
SECONDARY = (128, 28, 47)
INK = (44, 52, 66)


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
    dist = np.sqrt(((rgb - key) ** 2).sum(axis=2))

    # The source background is hot pink, not a pure #ff00ff. Some ear/skin
    # pixels are close by Euclidean distance, so require a pink-channel
    # dominance signal before treating a boundary-connected pixel as background.
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    pink_dominance = np.minimum(red, blue) - green
    background_like = (dist < 45.0) | (
        (dist < 190.0)
        & (pink_dominance > 42.0)
        & (red > 165.0)
        & (blue > 76.0)
    )
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
    soft_bg = bg_mask.filter(ImageFilter.GaussianBlur(0.7))
    alpha = Image.eval(soft_bg, lambda px: 255 - px)

    alpha_arr = np.array(alpha)
    alpha_arr[:2, :] = 0
    alpha_arr[-2:, :] = 0
    alpha_arr[:, :2] = 0
    alpha_arr[:, -2:] = 0
    alpha_arr = remove_small_alpha_islands(alpha_arr)

    return Image.fromarray(np.dstack([rgb.astype(np.uint8), alpha_arr]), "RGBA")


def connected_background(background_like: np.ndarray, *, seed_bottom: bool = True) -> np.ndarray:
    h, w = background_like.shape
    connected = np.zeros((h, w), dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    for x in range(w):
        if background_like[0, x]:
            queue.append((0, x))
        if seed_bottom and background_like[h - 1, x]:
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
    return connected


def remove_checkerboard(image: Image.Image) -> Image.Image:
    image = fit_square(image, SIZE)
    rgb_arr = np.array(image.convert("RGB"), dtype=np.uint8)
    rgb = rgb_arr.astype(np.float32)
    maxc = rgb.max(axis=2)
    minc = rgb.min(axis=2)
    chroma = maxc - minc
    luminance = rgb.mean(axis=2)

    # The generated image has a drawn transparency grid: very bright, low-chroma
    # cells connected to the image border. Use connectivity so white shirt,
    # silver glasses, and the lapel pin are preserved.
    background_like = (luminance > 224.0) & (chroma < 22.0)
    boundary_bg = connected_background(background_like, seed_bottom=False)

    bg_mask = Image.fromarray((boundary_bg * 255).astype(np.uint8), "L")
    bg_mask = bg_mask.filter(ImageFilter.MaxFilter(3))
    soft_bg = bg_mask.filter(ImageFilter.GaussianBlur(0.75))
    alpha = Image.eval(soft_bg, lambda px: 255 - px)

    alpha_arr = np.array(alpha)
    alpha_arr[:2, :] = 0
    alpha_arr[-2:, :] = 0
    alpha_arr[:, :2] = 0
    alpha_arr[:, -2:] = 0

    return Image.fromarray(np.dstack([rgb_arr, alpha_arr]), "RGBA")


def scale_center(image: Image.Image, scale: float, y_offset: int = 0) -> Image.Image:
    size = image.size[0]
    scaled_size = round(size * scale)
    enlarged = image.resize((scaled_size, scaled_size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - scaled_size) // 2
    y = (size - scaled_size) // 2 + y_offset
    canvas.alpha_composite(enlarged, (x, y))
    return canvas


def legal_effect_layer(size: int, phase: float) -> Image.Image:
    pulse = (math.sin(phase) + 1) / 2
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    halo_draw = ImageDraw.Draw(halo)
    inset = round(size * (0.18 - pulse * 0.012))
    halo_draw.ellipse(
        (inset, inset, size - inset, size - inset),
        outline=(*ACCENT, round(34 + pulse * 28)),
        width=round(size * 0.008),
    )
    seal_inset = round(size * (0.255 + pulse * 0.006))
    halo_draw.ellipse(
        (seal_inset, seal_inset, size - seal_inset, size - seal_inset),
        outline=(*SECONDARY, round(18 + pulse * 18)),
        width=round(size * 0.004),
    )
    halo = halo.filter(ImageFilter.GaussianBlur(round(size * 0.018)))
    layer = Image.alpha_composite(layer, halo)

    # A restrained scales-of-justice line motif, kept behind the portrait.
    cx = size // 2
    top_y = round(size * 0.285)
    bar_y = round(size * 0.36 + math.sin(phase) * size * 0.003)
    width = round(size * 0.28)
    alpha = round(22 + pulse * 20)
    draw.line((cx, top_y, cx, bar_y + round(size * 0.06)), fill=(*INK, alpha), width=2)
    draw.line((cx - width, bar_y, cx + width, bar_y), fill=(*ACCENT, alpha), width=3)
    for side in (-1, 1):
        x = cx + side * round(width * 0.72)
        draw.line((x, bar_y, x - side * round(size * 0.035), bar_y + round(size * 0.07)), fill=(*ACCENT, alpha), width=2)
        draw.line((x, bar_y, x + side * round(size * 0.035), bar_y + round(size * 0.07)), fill=(*ACCENT, alpha), width=2)
        draw.arc(
            (
                x - round(size * 0.055),
                bar_y + round(size * 0.052),
                x + round(size * 0.055),
                bar_y + round(size * 0.105),
            ),
            start=0,
            end=180,
            fill=(*SECONDARY, round(alpha * 0.8)),
            width=2,
        )

    for idx in range(3):
        angle = phase * (0.55 + idx * 0.09) + idx * 2.2
        radius = size * (0.31 + idx * 0.04)
        x = size / 2 + math.cos(angle) * radius
        y = size / 2 + math.sin(angle) * radius * 0.78
        dot = round(size * (0.0065 + idx * 0.0016))
        color = ACCENT if idx != 1 else SECONDARY
        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=(*color, round(48 + pulse * 38)))

    return layer


def make_frames(base: Image.Image) -> list[Image.Image]:
    frames: list[Image.Image] = []
    for idx in range(FRAME_COUNT):
        phase = 2 * math.pi * idx / FRAME_COUNT
        breath = 1.0 + 0.010 * ((math.sin(phase) + 1) / 2)
        bob = round(-SIZE * 0.0045 * math.sin(phase))
        frame = scale_center(base, breath, bob)
        frame = ImageEnhance.Brightness(frame).enhance(1.0 + 0.012 * math.sin(phase + 0.35))
        frame = Image.alpha_composite(legal_effect_layer(SIZE, phase), frame)
        frames.append(frame.convert("RGBA"))
    return frames


def save_animation(base: Image.Image) -> None:
    frames = make_frames(base)
    webp = ASSET_DIR / "finsight-legal-avatar-animated-transparent.webp"
    gif = ASSET_DIR / "finsight-legal-avatar-animated-transparent.gif"
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
    if not CANDIDATE.exists():
        raise FileNotFoundError(CANDIDATE)

    selected_out = ASSET_DIR / "finsight-legal-avatar-selected-native-checker-source.png"
    transparent_out = ASSET_DIR / "finsight-legal-avatar-transparent.png"

    source = Image.open(CANDIDATE)
    source.save(selected_out)
    transparent = remove_checkerboard(source)
    transparent.save(transparent_out)
    print(selected_out)
    print(transparent_out)
    save_animation(transparent)


if __name__ == "__main__":
    main()
