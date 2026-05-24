#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


COMFY = "http://127.0.0.1:8188"
WORKFLOW = Path("/home/maoyd/ComfyUI/user/default/workflows/01_flux2_text_to_image_gb10.api.json")
COMFY_OUTPUT = Path("/home/maoyd/ComfyUI/output")
PROJECT_OUT = Path("/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts")
CHROMA_KEY = (255, 0, 255)
CHROMA_HELPER = Path.home() / ".codex/skills/.system/imagegen/scripts/remove_chroma_key.py"

SIZE = 768
FRAME_COUNT = 40
DURATION_MS = 70

NEGATIVE = (
    "low quality, blurry, ugly, deformed face, distorted eyes, bad anatomy, extra fingers, "
    "childlike, baby face, old, harsh wrinkles, text, logo, watermark, cropped head, full body, "
    "flat 2d icon, toy-like, mascot costume, ugly hands, messy background, scenery, charts, HUD, "
    "floating panels, extra people, white hair, silver hair, gray hair, grey hair, platinum hair"
)

STYLE = (
    "FinSight AI agent avatar, mature professional premium cartoon character, semi-realistic 3D, "
    "highly attractive face, polished cinematic lighting, shoulder-up close-up portrait, centered, "
    "clean silhouette, detailed eyes, refined skin, elegant facial features, high-end character design, "
    "suitable for chatbot avatar, no text, no logo"
)

JOBS = [
    {
        "slug": "analysis",
        "seed": 2026051801,
        "prefix": "finsight_agents_final/finsight-analysis-avatar-source",
        "prompt": (
            f"{STYLE}. Male deep financial analysis strategist, handsome mature East Asian man, "
            "calm intelligent expression, short dark hair, refined jawline, dark navy and black "
            "tailored suit with subtle gold accents, authoritative and thoughtful executive analyst energy, "
            "isolated on a perfectly flat solid #ff00ff chroma-key background, no props, no charts, "
            "no dashboard, no scene, no glow objects, no floating UI, crisp edges, generous padding"
        ),
    },
    {
        "slug": "factchecker",
        "seed": 2026051802,
        "prefix": "finsight_agents_final/finsight-factchecker-avatar-source",
        "prompt": (
            f"{STYLE}. Female fact verification officer, beautiful mature East Asian woman, "
            "precise and confident expression, elegant dark brown bob haircut, slim rim glasses, "
            "black blazer and white blouse with subtle emerald details, rigorous, sharp, graceful, "
            "professional risk control temperament, isolated on a perfectly flat solid #ff00ff chroma-key background, "
            "no evidence cards, no verification panels, no scene, no props, no text, crisp edges, generous padding"
        ),
    },
    {
        "slug": "tracking",
        "seed": 2026051803,
        "prefix": "finsight_agents_final/finsight-tracking-avatar-source",
        "prompt": (
            f"{STYLE}. Female continuous risk tracking officer, beautiful mature East Asian woman, "
            "alert and agile expression, sleek high ponytail, teal cyan tech jacket with subtle orange-red accents, "
            "composed and sharp market monitoring energy, isolated on a perfectly flat solid #ff00ff chroma-key background, "
            "no radar rings, no timeline boards, no scene, no props, no text, crisp edges, generous padding"
        ),
    },
]


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
    prompt["8"]["inputs"]["steps"] = 26
    prompt["11"]["inputs"]["filename_prefix"] = job["prefix"]
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


def remove_chroma_key(image: Image.Image) -> Image.Image:
    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    rgb = rgba[:, :, :3].astype(np.int16)
    alpha = rgba[:, :, 3].astype(np.uint8)
    key = np.array(CHROMA_KEY, dtype=np.int16)
    dist = np.sqrt(((rgb - key) ** 2).sum(axis=2))

    transparent_threshold = 18.0
    opaque_threshold = 75.0
    mask = np.clip((dist - transparent_threshold) / (opaque_threshold - transparent_threshold), 0.0, 1.0)
    alpha = np.minimum(alpha, (mask * 255).astype(np.uint8))

    # Simple despill: reduce magenta remnants where the key color leaks into edge pixels.
    spill = np.clip((1.0 - mask) * 0.55, 0.0, 0.55)
    rgb = rgb.astype(np.float32)
    rgb[:, :, 0] = np.clip(rgb[:, :, 0] - spill * 90, 0, 255)
    rgb[:, :, 2] = np.clip(rgb[:, :, 2] - spill * 90, 0, 255)
    rgb[:, :, 1] = np.clip(rgb[:, :, 1] + spill * 18, 0, 255)

    out = np.dstack([rgb.astype(np.uint8), alpha])
    result = Image.fromarray(out, "RGBA")
    result = result.filter(ImageFilter.GaussianBlur(0.1))
    return result


def try_remove_with_helper(src: Path, out: Path) -> bool:
    if not CHROMA_HELPER.exists():
        return False
    cmd = [
        "python",
        str(CHROMA_HELPER),
        "--input",
        str(src),
        "--out",
        str(out),
        "--auto-key",
        "border",
        "--soft-matte",
        "--transparent-threshold",
        "12",
        "--opaque-threshold",
        "220",
        "--despill",
    ]
    return subprocess.run(cmd, check=False).returncode == 0


def save_transparent(src: Path, out: Path) -> Path:
    if try_remove_with_helper(src, out):
        return out

    image = Image.open(src)
    transparent = remove_chroma_key(fit_square(image, SIZE))
    transparent.save(out)
    return out


def scale_center(image: Image.Image, scale: float, y_offset: int = 0) -> Image.Image:
    size = image.size[0]
    scaled_size = round(size * scale)
    enlarged = image.resize((scaled_size, scaled_size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - scaled_size) // 2
    y = (size - scaled_size) // 2 + y_offset
    canvas.alpha_composite(enlarged, (x, y))
    return canvas


def aura_layer(size: int, phase: float, slug: str) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    pulse = (math.sin(phase) + 1) / 2
    if slug == "analysis":
        accent = (90, 170, 255)
        secondary = (255, 194, 80)
    elif slug == "factchecker":
        accent = (34, 197, 94)
        secondary = (110, 231, 183)
    else:
        accent = (20, 184, 166)
        secondary = (249, 115, 22)

    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    halo_draw = ImageDraw.Draw(halo)
    inset = round(size * (0.18 - pulse * 0.012))
    halo_draw.ellipse(
        (inset, inset, size - inset, size - inset),
        outline=(*accent, round(32 + pulse * 28)),
        width=round(size * 0.008),
    )
    halo = halo.filter(ImageFilter.GaussianBlur(round(size * 0.02)))
    layer = Image.alpha_composite(layer, halo)

    for idx in range(3):
        angle = phase * (0.55 + idx * 0.08) + idx * 2.1
        radius = size * (0.30 + idx * 0.035)
        x = size / 2 + math.cos(angle) * radius
        y = size / 2 + math.sin(angle) * radius * 0.78
        dot = round(size * (0.007 + idx * 0.0016))
        draw.ellipse(
            (x - dot, y - dot, x + dot, y + dot),
            fill=(*secondary, round(48 + pulse * 40)),
        )

    return layer


def make_frames(base: Image.Image, slug: str) -> list[Image.Image]:
    frames: list[Image.Image] = []
    for idx in range(FRAME_COUNT):
        phase = 2 * math.pi * idx / FRAME_COUNT
        breath = 1.0 + 0.010 * ((math.sin(phase) + 1) / 2)
        bob = round(-SIZE * 0.0045 * math.sin(phase))
        frame = scale_center(base, breath, bob)
        frame = ImageEnhance.Brightness(frame).enhance(1.0 + 0.012 * math.sin(phase + 0.35))
        frame = Image.alpha_composite(aura_layer(SIZE, phase, slug), frame)
        frames.append(frame.convert("RGBA"))
    return frames


def save_animation(slug: str, base_path: Path) -> None:
    base = Image.open(base_path).convert("RGBA")
    frames = make_frames(base, slug)
    webp = PROJECT_OUT / f"finsight-{slug}-avatar-animated-transparent.webp"
    gif = PROJECT_OUT / f"finsight-{slug}-avatar-animated-transparent.gif"
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
    wait_ready()
    PROJECT_OUT.mkdir(parents=True, exist_ok=True)

    transparent_paths: dict[str, Path] = {}
    for job in JOBS:
        prompt_id = submit(job)
        source = wait_output(prompt_id)
        raw_target = PROJECT_OUT / f"finsight-{job['slug']}-avatar-source-magenta.png"
        shutil.copy2(source, raw_target)
        transparent_target = PROJECT_OUT / f"finsight-{job['slug']}-avatar-transparent.png"
        save_transparent(raw_target, transparent_target)
        transparent_paths[job["slug"]] = transparent_target
        print(raw_target)
        print(transparent_target)

    for slug, path in transparent_paths.items():
        save_animation(slug, path)


if __name__ == "__main__":
    main()
