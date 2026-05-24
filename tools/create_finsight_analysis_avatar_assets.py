#!/usr/bin/env python3
from __future__ import annotations

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
ARCHIVE_OUT = Path("/home/maoyd/douge_ai_agent/agent-avatar-archive-20260520/analysis")
CANDIDATE_DIR = PROJECT_OUT / "beauty-candidates" / "analysis-redesign-20260520"

SIZE = 768
FRAME_COUNT = 44
DURATION_MS = 68
ACCENT = (64, 132, 255)
SECONDARY = (255, 190, 72)
INK = (37, 48, 67)

NEGATIVE = (
    "low quality, blurry, ugly, unattractive, generic face, uncanny face, deformed face, distorted eyes, "
    "asymmetric eyes, bad anatomy, childlike, baby face, old, harsh wrinkles, feminine man, western face, "
    "caucasian, plastic toy, doll, mascot costume, flat 2d icon, overdone influencer makeup, heavy makeup, "
    "vulgar, text, logo, watermark, cropped head, full body, hands, extra people, messy background, scenery, "
    "charts, HUD, floating panels, props, white hair, silver hair, gray hair, grey hair, platinum hair, "
    "sunglasses, opaque glasses, distorted glasses, broken glasses, banker stereotype, trader yelling"
)

STYLE = (
    "FinSight AI agent avatar, transparent-background source candidate, mature professional premium cartoon character, "
    "semi-realistic 3D portrait, refined Chinese aesthetic, exceptionally handsome adult East Asian man, high attractiveness, "
    "exquisite facial features, harmonious natural proportions, clear intelligent eyes, elegant straight nose bridge, "
    "refined clean jawline, smooth luminous skin, high-end cinematic beauty lighting, shoulder-up close-up portrait, "
    "centered, clean silhouette, crisp edges, business appropriate, polished but not plastic, suitable for chatbot avatar, "
    "no text, no logo, isolated on a perfectly flat solid #ff00ff chroma-key background, no shadows on background, no gradients"
)

JOBS = [
    {
        "slug": "analysis-redesign-a",
        "seed": 202605200701,
        "prompt": (
            f"{STYLE}. A-share listed company financial diagnosis analyst, extremely handsome young mature Chinese man, "
            "calm penetrating gaze that suggests deep analytical intelligence, neat black side-parted hair with natural volume, "
            "dark navy tailored suit, charcoal shirt, subtle gold lapel detail shaped like a rising insight line, "
            "restrained confident half-smile, elite research director temperament, trustworthy, sharp, elegant"
        ),
    },
    {
        "slug": "analysis-redesign-b",
        "seed": 202605200702,
        "prompt": (
            f"{STYLE}. Operating-diagnosis financial analyst and investment committee research strategist, very handsome Chinese man, "
            "clean defined eyebrows, bright wise eyes, refined oval face, short black hair swept back softly, "
            "midnight blue suit with black turtleneck, tiny gold ratio-symbol lapel pin, composed premium executive analyst presence, "
            "intellectual charisma, serious but approachable"
        ),
    },
    {
        "slug": "analysis-redesign-c",
        "seed": 202605200703,
        "prompt": (
            f"{STYLE}. Senior financial statement analysis expert, exceptionally handsome mature Chinese man, "
            "sharp warm eyes, elegant face, precise calm expression, dark hair with clean side part, "
            "graphite navy blazer, crisp white shirt, muted gold tie bar, subtle blue-and-gold analyst badge, "
            "wise evidence-driven strategist energy, refined high-end finance portrait"
        ),
    },
    {
        "slug": "analysis-redesign-d",
        "seed": 202605200704,
        "prompt": (
            f"{STYLE}. FinSight financial diagnosis chief analyst, stunningly handsome Chinese man, "
            "clear thoughtful eyes, strong clean jawline, neat thick black hair, elegant navy-black tailored suit, "
            "minimal gold cuff and lapel accent, calm authority of someone who reads risks through financial statements, "
            "premium intelligent aura, polished research expert, charismatic but restrained"
        ),
    },
]

# Picked to emphasize: strong analyst identity, highest-face-polish prompt,
# and a clean silhouette that reads well at small UI sizes.
SELECTED_SLUG = "analysis-redesign-d"


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
    deadline = time.time() + 60
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
    prompt["8"]["inputs"]["steps"] = 30
    prompt["5"]["inputs"]["guidance"] = 4.0
    prompt["11"]["inputs"]["filename_prefix"] = f"finsight_agents_analysis_redesign/{job['slug']}"
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


def analysis_effect_layer(size: int, phase: float) -> Image.Image:
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
    inner = round(size * (0.268 + pulse * 0.004))
    halo_draw.ellipse(
        (inner, inner, size - inner, size - inner),
        outline=(*SECONDARY, round(14 + pulse * 14)),
        width=round(size * 0.004),
    )
    halo = halo.filter(ImageFilter.GaussianBlur(round(size * 0.02)))
    layer = Image.alpha_composite(layer, halo)

    # Subtle diagnostic-finance motifs: trend line plus risk pulse, behind the portrait.
    pts = []
    for i in range(-1, 8):
        x = round(i * size / 6 + pulse * size * 0.026)
        y = round(size * (0.60 - 0.11 * math.sin(i * 1.12 + phase)))
        pts.append((x, y))
    draw.line(pts, fill=(*SECONDARY, round(18 + pulse * 24)), width=round(size * 0.005))

    cx = size // 2
    cy = round(size * 0.52)
    for idx, radius in enumerate((0.31, 0.39)):
        r = round(size * radius)
        draw.arc(
            (cx - r, cy - r, cx + r, cy + r),
            start=205 + idx * 14,
            end=325 + idx * 18,
            fill=(*INK, round(12 + pulse * 12 - idx * 3)),
            width=2,
        )

    for idx in range(3):
        angle = phase * (0.58 + idx * 0.09) + idx * 2.18
        radius = size * (0.32 + idx * 0.04)
        x = size / 2 + math.cos(angle) * radius
        y = size / 2 + math.sin(angle) * radius * 0.78
        dot = round(size * (0.0065 + idx * 0.0018))
        color = SECONDARY if idx != 1 else ACCENT
        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=(*color, round(48 + pulse * 38)))

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
        frame = Image.alpha_composite(analysis_effect_layer(SIZE, phase), frame)
        frames.append(frame.convert("RGBA"))
    return frames


def save_animation(base: Image.Image) -> tuple[Path, Path]:
    frames = make_frames(base)
    webp = PROJECT_OUT / "finsight-analysis-avatar-animated-transparent.webp"
    gif = PROJECT_OUT / "finsight-analysis-avatar-animated-transparent.gif"
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
        draw.text((x0 + 22, y0 + 325), f"analysis / {job['slug']}", fill=(28, 38, 54), font=font)

    out = CANDIDATE_DIR / "finsight-analysis-redesign-contact-sheet.jpg"
    sheet.save(out, quality=94)
    return out


def copy_to_archive(source_path: Path, transparent_path: Path, webp_path: Path, gif_path: Path) -> None:
    ARCHIVE_OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, ARCHIVE_OUT / "analysis-avatar-source-magenta.png")
    shutil.copy2(transparent_path, ARCHIVE_OUT / "analysis-avatar-transparent.png")
    shutil.copy2(transparent_path, ARCHIVE_OUT / "analysis-avatar-transparent-original.png")
    shutil.copy2(webp_path, ARCHIVE_OUT / "analysis-avatar-animated-transparent.webp")
    shutil.copy2(gif_path, ARCHIVE_OUT / "analysis-avatar-animated-transparent.gif")


def main() -> None:
    wait_ready()
    PROJECT_OUT.mkdir(parents=True, exist_ok=True)
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)

    final_paths: list[tuple[dict, Path]] = []
    selected_source: Path | None = None
    for job in JOBS:
        prompt_id = submit(job)
        source = wait_output(prompt_id)
        target = CANDIDATE_DIR / f"finsight-{job['slug']}-source-magenta.png"
        shutil.copy2(source, target)
        final_paths.append((job, target))
        if job["slug"] == SELECTED_SLUG:
            selected_source = target
        print(f"candidate {job['slug']}: {target}")

    print(f"contact_sheet: {make_contact_sheet(final_paths)}")

    if selected_source is None:
        raise RuntimeError(f"Selected candidate not generated: {SELECTED_SLUG}")

    front_source = PROJECT_OUT / "finsight-analysis-avatar-selected-redesign-source-magenta.png"
    source_alias = PROJECT_OUT / "finsight-analysis-avatar-source-magenta.png"
    selected_alias = PROJECT_OUT / "finsight-analysis-avatar-selected-a-source-magenta.png"
    shutil.copy2(selected_source, front_source)
    shutil.copy2(selected_source, source_alias)
    shutil.copy2(selected_source, selected_alias)

    transparent = remove_magenta(Image.open(selected_source))
    transparent_path = PROJECT_OUT / "finsight-analysis-avatar-transparent.png"
    transparent.save(transparent_path)

    webp_path, gif_path = save_animation(transparent)
    copy_to_archive(front_source, transparent_path, webp_path, gif_path)

    print(front_source)
    print(transparent_path)
    print(webp_path)
    print(gif_path)


if __name__ == "__main__":
    main()
