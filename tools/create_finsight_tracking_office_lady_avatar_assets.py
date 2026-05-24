#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import shutil
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


COMFY = "http://127.0.0.1:8188"
WORKFLOW = Path("/home/maoyd/ComfyUI/user/default/workflows/01_flux2_text_to_image_gb10.api.json")
COMFY_OUTPUT = Path("/home/maoyd/ComfyUI/output")
PROJECT_OUT = Path("/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts")
ARCHIVE_OUT = Path("/home/maoyd/douge_ai_agent/agent-avatar-archive-20260520/tracking")
CANDIDATE_DIR = PROJECT_OUT / "beauty-candidates" / "tracking-office-lady-20260520"

SIZE = 768
FRAME_COUNT = 44
DURATION_MS = 68
ACCENT = (20, 184, 166)
SECONDARY = (249, 115, 22)
COOL_INK = (38, 58, 82)

NEGATIVE = (
    "low quality, blurry, ugly, unattractive, generic face, uncanny face, deformed face, distorted eyes, "
    "asymmetric eyes, bad anatomy, childlike, baby face, old, harsh wrinkles, masculine woman, western face, "
    "caucasian, plastic toy, doll, mascot costume, flat 2d icon, overdone influencer makeup, heavy makeup, "
    "vulgar, nude, lingerie, swimsuit, cleavage emphasis, explicit, fetish, text, logo, watermark, cropped head, "
    "full body, hands, extra people, messy background, office room background, scenery, charts, HUD, floating panels, "
    "props, white hair, silver hair, gray hair, grey hair, platinum hair, short hair, bob haircut, ponytail, "
    "average face, plain face, broad face, heavy build, stocky body, bulky shoulders, round bulky silhouette"
)

STYLE = (
    "FinSight_tracking AI agent avatar, transparent-background source candidate, mature professional premium cartoon character, "
    "semi-realistic 3D portrait, refined Chinese aesthetic, breathtakingly beautiful adult East Asian woman, ultra high attractiveness, "
    "stunningly gorgeous face, top-tier elegant beauty, long flowing hair only, slender graceful build, slim neck, delicate narrow shoulders, "
    "exquisite facial features, harmonious natural proportions, elegant almond eyes, refined nose bridge, soft refined lips, "
    "smooth luminous skin, high-end cinematic beauty lighting, shoulder-up close-up portrait, centered, clean slender silhouette, "
    "crisp edges, business appropriate, polished but not plastic, suitable for chatbot avatar, no text, no logo, "
    "isolated on a perfectly flat solid #ff00ff chroma-key background, no shadows on background, no gradients"
)

JOBS = [
    {
        "slug": "tracking-office-a",
        "seed": 202605201101,
        "prompt": (
            f"{STYLE}. A-share secondary-market financial tracking and early-warning chief officer, "
            "long glossy black hair falling over one shoulder, poised and alert eyes, slim delicate rim glasses, "
            "very slim elegant office lady, charcoal tailored blazer with slender waistline, crisp white silk blouse with tasteful open collar, subtle teal lapel accent, "
            "elegant office lady aura, slightly alluring through refined tailoring, calm evidence-chain risk controller, "
            "financial metrics consistency, citation discipline, structured warning-level temperament"
        ),
    },
    {
        "slug": "tracking-office-b",
        "seed": 202605201102,
        "prompt": (
            f"{STYLE}. Continuous financial tracking director for A-share listed companies, "
            "waist-length deep chestnut brown hair with soft side part, confident composed gaze, no glasses, "
            "slim refined office lady figure, graphite pencil blazer with narrow waist, ivory blouse, slender teal neck scarf, tiny orange alert-pin detail, "
            "absolute office lady elegance, beautiful and mildly seductive but still formal, "
            "expert in tracking items, metric panels, alert reports, update records, and audit-ready source evidence"
        ),
    },
    {
        "slug": "tracking-office-c",
        "seed": 202605201103,
        "prompt": (
            f"{STYLE}. Premium finance surveillance office lady, A-share financial risk monitoring specialist, "
            "long straight black hair, intelligent sharp eyes, refined red-brown lips, delicate transparent glasses, "
            "slender graceful posture, fitted black blazer over white blouse, slim waist impression, teal cuff accent, restrained orange risk-warning brooch, "
            "serene but formidable, mature beauty, evidence-first analyst who detects unit, scale, and period inconsistencies"
        ),
    },
    {
        "slug": "tracking-office-d",
        "seed": 202605201104,
        "prompt": (
            f"{STYLE}. FinSight_tracking financial early-warning office lady, "
            "long wavy black hair with glossy volume, captivating calm eyes, subtle natural makeup, no glasses, "
            "slim elegant silhouette, dark charcoal tailored blazer with narrow waistline, elegant satin white blouse with tasteful neckline, slim teal jewelry accent, "
            "refined orange warning-detail on lapel, stunning mature beauty with restrained sensual confidence, "
            "professional risk-observation commander for tracking lists, sentiment monitoring, metrics panels, and four-level alerts"
        ),
    },
]

DEFAULT_SELECTED_SLUG = "tracking-office-d"


def http_json(method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFY}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_ready() -> None:
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            http_json("GET", "/system_stats")
            return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise RuntimeError("ComfyUI API is not ready")


def submit(job: dict) -> str:
    prompt = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    prompt["4"]["inputs"]["text"] = job["prompt"]
    prompt["6"]["inputs"]["text"] = NEGATIVE
    prompt["8"]["inputs"]["seed"] = job["seed"]
    prompt["8"]["inputs"]["steps"] = 32
    prompt["5"]["inputs"]["guidance"] = 4.2
    prompt["11"]["inputs"]["filename_prefix"] = f"finsight_agents_tracking_office_lady/{job['slug']}"
    response = http_json("POST", "/prompt", {"prompt": prompt, "client_id": str(uuid.uuid4())})
    return response["prompt_id"]


def wait_output(prompt_id: str) -> Path:
    deadline = time.time() + 1200
    while time.time() < deadline:
        history = http_json("GET", f"/history/{prompt_id}")
        item = history.get(prompt_id)
        if item:
            for node_output in item.get("outputs", {}).values():
                for image in node_output.get("images", []):
                    return COMFY_OUTPUT / (image.get("subfolder") or "") / image["filename"]
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {prompt_id}")


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
    soft_bg = bg_mask.filter(ImageFilter.GaussianBlur(0.8))
    alpha = Image.eval(soft_bg, lambda px: 255 - px)

    edge = (np.array(soft_bg).astype(np.float32) / 255.0)[:, :, None]
    rgb[:, :, 0:1] = np.clip(rgb[:, :, 0:1] - edge * 145, 0, 255)
    rgb[:, :, 2:3] = np.clip(rgb[:, :, 2:3] - edge * 145, 0, 255)
    rgb[:, :, 1:2] = np.clip(rgb[:, :, 1:2] + edge * 18, 0, 255)

    alpha_arr = np.array(alpha)
    alpha_arr[:2, :] = 0
    alpha_arr[-2:, :] = 0
    alpha_arr[:, :2] = 0
    alpha_arr[:, -2:] = 0
    alpha_arr = remove_small_alpha_islands(alpha_arr)

    return Image.fromarray(np.dstack([rgb.astype(np.uint8), alpha_arr]), "RGBA")


def tracking_effect_layer(size: int, phase: float) -> Image.Image:
    pulse = (math.sin(phase) + 1) / 2
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    halo_draw = ImageDraw.Draw(halo)
    inset = round(size * (0.18 - pulse * 0.012))
    halo_draw.ellipse(
        (inset, inset, size - inset, size - inset),
        outline=(*ACCENT, round(32 + pulse * 28)),
        width=round(size * 0.008),
    )
    for idx, radius in enumerate((0.285, 0.365)):
        r = round(size * (radius + pulse * 0.004))
        halo_draw.ellipse(
            (size // 2 - r, size // 2 - r, size // 2 + r, size // 2 + r),
            outline=(*COOL_INK, round(12 + pulse * 10 - idx * 3)),
            width=2,
        )
    halo = halo.filter(ImageFilter.GaussianBlur(round(size * 0.018)))
    layer = Image.alpha_composite(layer, halo)

    cx = cy = size // 2
    angle = phase * 0.72
    x2 = cx + round(math.cos(angle) * size * 0.42)
    y2 = cy + round(math.sin(angle) * size * 0.42)
    draw.line((cx, cy, x2, y2), fill=(*SECONDARY, 32), width=round(size * 0.005))

    for idx in range(4):
        a = angle + idx * 1.38
        r = round(size * (0.255 + idx * 0.046))
        x = cx + round(math.cos(a) * r)
        y = cy + round(math.sin(a) * r * 0.80)
        dot = round(size * (0.006 + idx * 0.0012))
        color = SECONDARY if idx in (0, 3) else ACCENT
        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=(*color, round(50 + pulse * 42)))

    progress = (phase / (2 * math.pi)) % 1
    y = round(size * (0.27 + 0.40 * progress))
    draw.rectangle((round(size * 0.24), y, round(size * 0.76), y + 3), fill=(*ACCENT, 16))

    return layer


def scale_center(image: Image.Image, scale: float, y_offset: int = 0) -> Image.Image:
    size = image.size[0]
    scaled_size = round(size * scale)
    enlarged = image.resize((scaled_size, scaled_size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - scaled_size) // 2
    y = (size - scaled_size) // 2 + y_offset
    canvas.alpha_composite(enlarged, (x, y))
    return canvas


def make_frames(base: Image.Image) -> list[Image.Image]:
    frames: list[Image.Image] = []
    for idx in range(FRAME_COUNT):
        phase = 2 * math.pi * idx / FRAME_COUNT
        breath = 1.0 + 0.010 * ((math.sin(phase) + 1) / 2)
        bob = round(-SIZE * 0.0045 * math.sin(phase))
        frame = scale_center(base, breath, bob)
        frame = ImageEnhance.Brightness(frame).enhance(1.0 + 0.012 * math.sin(phase + 0.35))
        frame = Image.alpha_composite(tracking_effect_layer(SIZE, phase), frame)
        frames.append(frame.convert("RGBA"))
    return frames


def save_animation(base: Image.Image) -> tuple[Path, Path]:
    frames = make_frames(base)
    webp = PROJECT_OUT / "finsight-tracking-avatar-animated-transparent.webp"
    gif = PROJECT_OUT / "finsight-tracking-avatar-animated-transparent.gif"
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
    return webp, gif


def make_contact_sheet(paths: list[tuple[dict, Path]]) -> Path:
    thumbs = []
    for job, path in paths:
        image = Image.open(path).convert("RGB")
        image.thumbnail((300, 300), Image.Resampling.LANCZOS)
        thumbs.append((job, image.copy()))

    cols = 2
    rows = 2
    cell_w = 380
    cell_h = 370
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (246, 248, 251))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        font = ImageFont.load_default()

    for idx, (job, image) in enumerate(thumbs):
        col = idx % cols
        row = idx // cols
        x0 = col * cell_w
        y0 = row * cell_h
        x = x0 + (cell_w - image.width) // 2
        y = y0 + 18
        sheet.paste(image, (x, y))
        draw.text((x0 + 22, y0 + 325), f"tracking / {job['slug']}", fill=(28, 38, 54), font=font)

    out = CANDIDATE_DIR / "finsight-tracking-office-lady-contact-sheet.jpg"
    sheet.save(out, quality=94)
    return out


def generate_candidates() -> dict[str, Path]:
    wait_ready()
    PROJECT_OUT.mkdir(parents=True, exist_ok=True)
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)

    final_paths: list[tuple[dict, Path]] = []
    generated: dict[str, Path] = {}
    for job in JOBS:
        prompt_id = submit(job)
        source = wait_output(prompt_id)
        target = CANDIDATE_DIR / f"finsight-{job['slug']}-source-magenta.png"
        shutil.copy2(source, target)
        final_paths.append((job, target))
        generated[job["slug"]] = target
        print(f"candidate {job['slug']}: {target}")

    print(f"contact_sheet: {make_contact_sheet(final_paths)}")
    return generated


def finalize(selected_slug: str, generated: dict[str, Path] | None = None, source_override: Path | None = None) -> None:
    source = (generated or {}).get(selected_slug)
    if source_override is not None:
        source = source_override
    if source is None:
        source = CANDIDATE_DIR / f"finsight-{selected_slug}-source-magenta.png"
    if not source.exists():
        raise FileNotFoundError(f"Selected source is missing: {source}")

    front_source = PROJECT_OUT / "finsight-tracking-avatar-selected-office-lady-source-magenta.png"
    source_alias = PROJECT_OUT / "finsight-tracking-avatar-source-magenta.png"
    selected_alias = PROJECT_OUT / "finsight-tracking-avatar-selected-a-source-magenta.png"
    shutil.copy2(source, front_source)
    shutil.copy2(source, source_alias)
    shutil.copy2(source, selected_alias)

    transparent = remove_magenta(Image.open(source))
    transparent_path = PROJECT_OUT / "finsight-tracking-avatar-transparent.png"
    transparent.save(transparent_path)

    webp_path, gif_path = save_animation(transparent)

    ARCHIVE_OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(front_source, ARCHIVE_OUT / "tracking-avatar-source-magenta.png")
    shutil.copy2(transparent_path, ARCHIVE_OUT / "tracking-avatar-transparent.png")
    shutil.copy2(transparent_path, ARCHIVE_OUT / "tracking-avatar-transparent-original.png")
    shutil.copy2(webp_path, ARCHIVE_OUT / "tracking-avatar-animated-transparent.webp")
    shutil.copy2(gif_path, ARCHIVE_OUT / "tracking-avatar-animated-transparent.gif")

    print(front_source)
    print(transparent_path)
    print(webp_path)
    print(gif_path)
    print(ARCHIVE_OUT / "tracking-avatar-source-magenta.png")
    print(ARCHIVE_OUT / "tracking-avatar-transparent.png")
    print(ARCHIVE_OUT / "tracking-avatar-animated-transparent.webp")
    print(ARCHIVE_OUT / "tracking-avatar-animated-transparent.gif")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selected", default=DEFAULT_SELECTED_SLUG)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--skip-generate", action="store_true")
    args = parser.parse_args()

    generated = None if args.skip_generate else generate_candidates()
    finalize(args.selected, generated, args.source)


if __name__ == "__main__":
    main()
