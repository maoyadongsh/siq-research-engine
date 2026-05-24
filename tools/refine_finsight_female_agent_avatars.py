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
    "/home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts"
)

NEGATIVE = (
    "low quality, blurry, ugly, deformed face, distorted eyes, bad anatomy, extra fingers, "
    "childlike, baby face, old, harsh wrinkles, text, logo, watermark, cropped head, full body, "
    "flat 2d icon, plastic toy, overly cute mascot, scary, messy background, white hair, silver hair, "
    "gray hair, grey hair, platinum hair, pale hair, elderly"
)

STYLE = (
    "FinSight AI agent avatar, mature professional premium cartoon character, semi-realistic 3D, "
    "beautiful adult East Asian woman, high-fashion but business appropriate, refined attractive face, "
    "black hair or deep chestnut brown hair only, no white hair, no silver hair, polished cinematic lighting, "
    "shoulder-up close-up portrait, centered, clean silhouette, fintech interface glow, soft depth of field, "
    "detailed eyes, refined skin, elegant facial features, high-end character design, suitable for chatbot avatar, no text"
)

JOBS = [
    {
        "slug": "factchecker-v2a",
        "seed": 2026051812,
        "prefix": "finsight_agents_drafts/finsight-factchecker-avatar-static-v2a",
        "prompt": (
            f"{STYLE}. Fact verification officer, deep chestnut bob haircut, graceful confident gaze, "
            "subtle slim rim glasses, black tailored blazer and white blouse, emerald green audit glow, "
            "floating evidence cards and precise verification check marks, elegant studio beauty lighting, "
            "rigorous, sharp, premium financial compliance temperament"
        ),
    },
    {
        "slug": "factchecker-v2b",
        "seed": 2026051813,
        "prefix": "finsight_agents_drafts/finsight-factchecker-avatar-static-v2b",
        "prompt": (
            f"{STYLE}. Fact verification officer, long glossy black hair tucked behind one ear, no glasses, "
            "calm intelligent smile, tailored charcoal suit with emerald accents, translucent document panels, "
            "soft scanning light, tasteful luxury editorial beauty, precise and trustworthy audit expert energy"
        ),
    },
    {
        "slug": "tracking-v2a",
        "seed": 2026051814,
        "prefix": "finsight_agents_drafts/finsight-tracking-avatar-static-v2a",
        "prompt": (
            f"{STYLE}. Continuous risk tracking officer, sleek high ponytail with black hair, alert elegant eyes, "
            "teal fitted tech jacket with fine orange alert accents, radar rings and timeline signal lights behind her, "
            "glamorous but professional fintech monitoring commander, agile, composed, visually striking"
        ),
    },
    {
        "slug": "tracking-v2b",
        "seed": 2026051815,
        "prefix": "finsight_agents_drafts/finsight-tracking-avatar-static-v2b",
        "prompt": (
            f"{STYLE}. Continuous risk tracking officer, stylish deep brown shoulder-length hair, polished side part, "
            "confident warm expression, teal and graphite cyber blazer, orange-red risk alert points and circular radar HUD, "
            "premium cinematic beauty portrait, mature professional market surveillance energy"
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


def make_contact_sheet(paths: list[Path]) -> Path:
    thumbs = []
    for path in paths:
        image = Image.open(path).convert("RGB")
        image.thumbnail((360, 360), Image.Resampling.LANCZOS)
        thumbs.append((path, image.copy()))

    width = 380 * len(thumbs)
    height = 440
    sheet = Image.new("RGB", (width, height), (246, 248, 251))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        font = ImageFont.load_default()

    for idx, (path, image) in enumerate(thumbs):
        x0 = idx * 380
        x = x0 + (380 - image.width) // 2
        sheet.paste(image, (x, 18))
        label = path.stem.replace("finsight-", "").replace("-avatar-static-", "")
        draw.text((x0 + 22, 392), label, fill=(28, 38, 54), font=font)

    out = PROJECT_OUT / "finsight-female-avatar-refinements-contact-sheet.jpg"
    sheet.save(out, quality=92)
    return out


def main() -> None:
    wait_ready()
    PROJECT_OUT.mkdir(parents=True, exist_ok=True)
    final_paths: list[Path] = []

    for job in JOBS:
        prompt_id = submit(job)
        source = wait_output(prompt_id)
        target = PROJECT_OUT / f"finsight-{job['slug']}-avatar-static.png"
        shutil.copy2(source, target)
        final_paths.append(target)
        print(f"{job['slug']}: {target}")

    print(f"contact_sheet: {make_contact_sheet(final_paths)}")


if __name__ == "__main__":
    main()
