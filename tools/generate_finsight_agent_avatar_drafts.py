#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


COMFY = "http://127.0.0.1:8188"
WORKFLOW = Path("/home/maoyd/ComfyUI/user/default/workflows/01_flux2_text_to_image_gb10.api.json")
COMFY_OUTPUT = Path("/home/maoyd/ComfyUI/output")
PROJECT_OUT = Path(
    "/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts"
)

NEGATIVE = (
    "low quality, blurry, ugly, deformed face, distorted eyes, bad anatomy, extra fingers, "
    "childlike, baby face, old, harsh wrinkles, text, logo, watermark, cropped head, full body, "
    "flat 2d icon, plastic toy, overly cute mascot, scary, messy background"
)

STYLE = (
    "FinSight AI agent avatar, mature professional premium cartoon character, semi-realistic 3D, "
    "highly attractive face, polished cinematic lighting, shoulder-up close-up portrait, centered, "
    "clean silhouette, fintech interface glow, soft depth of field, detailed eyes, refined skin, "
    "elegant facial features, high-end character design, suitable for chatbot avatar, no text"
)

JOBS = [
    {
        "slug": "analysis",
        "seed": 2026051801,
        "prefix": "finsight_agents_drafts/finsight-analysis-avatar-static-draft",
        "prompt": (
            f"{STYLE}. Male deep financial analysis strategist, handsome mature East Asian man, "
            "calm intelligent expression, short dark hair, refined jawline, dark navy and black "
            "tech-tailored suit with subtle gold accents, deep blue financial dashboard aura, "
            "gold earnings curve and report grid as abstract holographic background, trustworthy, "
            "logical, authoritative, executive analyst energy"
        ),
    },
    {
        "slug": "factchecker",
        "seed": 2026051802,
        "prefix": "finsight_agents_drafts/finsight-factchecker-avatar-static-draft",
        "prompt": (
            f"{STYLE}. Female fact verification officer, beautiful mature East Asian woman, "
            "focused precise expression, elegant silver-black or dark brown hair, optional slim "
            "rim glasses with subtle reflection, black white and emerald green palette, audit and "
            "evidence review atmosphere, floating evidence cards, verification check marks and "
            "soft scanning light in the background, rigorous, sharp, graceful, professional risk "
            "control temperament"
        ),
    },
    {
        "slug": "tracking",
        "seed": 2026051803,
        "prefix": "finsight_agents_drafts/finsight-tracking-avatar-static-draft",
        "prompt": (
            f"{STYLE}. Female continuous risk tracking officer, beautiful mature East Asian woman, "
            "alert and agile expression, sleek short hair or high ponytail, lightweight tech jacket, "
            "teal cyan monitoring glow with small orange-red alert accents, radar rings, timeline "
            "signals and risk alert points as abstract holographic background, fast response, "
            "observant, composed, market surveillance energy"
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
    deadline = time.time() + 20
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
    prompt["8"]["inputs"]["steps"] = 24
    prompt["11"]["inputs"]["filename_prefix"] = job["prefix"]
    response = http_json("POST", "/prompt", {"prompt": prompt, "client_id": str(uuid.uuid4())})
    return response["prompt_id"]


def wait_output(prompt_id: str) -> Path:
    deadline = time.time() + 900
    while time.time() < deadline:
        history = http_json("GET", f"/history/{prompt_id}")
        item = history.get(prompt_id)
        if item:
            outputs = item.get("outputs", {})
            for node_output in outputs.values():
                for image in node_output.get("images", []):
                    filename = image["filename"]
                    subfolder = image.get("subfolder") or ""
                    return COMFY_OUTPUT / subfolder / filename
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {prompt_id}")


def make_contact_sheet(paths: list[Path]) -> Path:
    thumbs = []
    for path in paths:
        im = Image.open(path).convert("RGB")
        im.thumbnail((420, 420), Image.Resampling.LANCZOS)
        thumbs.append((path, im.copy()))

    width = 440 * len(thumbs)
    height = 500
    sheet = Image.new("RGB", (width, height), (245, 247, 250))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        font = ImageFont.load_default()

    for idx, (path, im) in enumerate(thumbs):
        x = idx * 440 + (440 - im.width) // 2
        y = 20
        sheet.paste(im, (x, y))
        label = path.stem.replace("finsight-", "").replace("-avatar-static-draft_00001_", "")
        draw.text((idx * 440 + 28, 452), label, fill=(28, 38, 54), font=font)

    out = PROJECT_OUT / "finsight-agent-avatar-drafts-contact-sheet.jpg"
    sheet.save(out, quality=92)
    return out


def main() -> None:
    wait_ready()
    PROJECT_OUT.mkdir(parents=True, exist_ok=True)
    final_paths: list[Path] = []

    for job in JOBS:
        prompt_id = submit(job)
        source = wait_output(prompt_id)
        target = PROJECT_OUT / f"finsight-{job['slug']}-avatar-static-draft.png"
        shutil.copy2(source, target)
        final_paths.append(target)
        print(f"{job['slug']}: {target}")

    sheet = make_contact_sheet(final_paths)
    print(f"contact_sheet: {sheet}")


if __name__ == "__main__":
    main()
