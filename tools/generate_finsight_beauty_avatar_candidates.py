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
OUT_DIR = Path(
    "/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts/beauty-candidates"
)

NEGATIVE = (
    "low quality, blurry, ugly, unattractive, generic face, uncanny face, deformed face, distorted eyes, "
    "asymmetric eyes, bad anatomy, childlike, baby face, old, harsh wrinkles, masculine woman, feminine man, "
    "western face, caucasian, plastic toy, doll, mascot costume, flat 2d icon, overdone influencer makeup, "
    "heavy makeup, vulgar, text, logo, watermark, cropped head, full body, hands, extra people, messy background, "
    "charts, HUD, floating panels, props, white hair, silver hair, gray hair, grey hair, platinum hair"
)

STYLE = (
    "FinSight AI agent avatar, transparent-background source candidate, mature professional premium cartoon character, "
    "semi-realistic 3D portrait, refined Chinese aesthetic, beautiful adult East Asian face, exquisite facial features, "
    "harmonious natural proportions, elegant almond eyes, delicate nose bridge, soft refined lips, smooth luminous skin, "
    "high-end cinematic beauty lighting, shoulder-up close-up portrait, centered, clean silhouette, crisp edges, "
    "business appropriate, polished but not plastic, suitable for chatbot avatar, no text, no logo, "
    "isolated on a perfectly flat solid #ff00ff chroma-key background, no shadows on background, no gradients"
)

JOBS = [
    {
        "slug": "analysis-a",
        "agent": "analysis",
        "seed": 202605190101,
        "prompt": (
            f"{STYLE}. Male deep financial analysis strategist, handsome mature Chinese man, refined oval face, "
            "clear intelligent eyes, neat dark hair with natural volume, calm confident gentle smile, dark navy tailored suit, "
            "subtle gold trim, executive analyst temperament, trustworthy and thoughtful, no glasses"
        ),
    },
    {
        "slug": "analysis-b",
        "agent": "analysis",
        "seed": 202605190102,
        "prompt": (
            f"{STYLE}. Male deep financial analysis strategist, handsome mature Chinese man, elegant clean jawline, "
            "warm sharp eyes, short black side-parted hair, calm premium business expression, charcoal navy suit and black shirt, "
            "minimal gold detail, high-end finance advisor temperament, refined and authoritative"
        ),
    },
    {
        "slug": "factchecker-a",
        "agent": "factchecker",
        "seed": 202605190201,
        "prompt": (
            f"{STYLE}. Female fact verification officer, beautiful mature Chinese woman, refined oval face, "
            "deep chestnut bob haircut, slim rim glasses, clear bright eyes, natural elegant makeup, black tailored blazer, "
            "white blouse, subtle emerald accent, precise graceful audit expert temperament"
        ),
    },
    {
        "slug": "factchecker-b",
        "agent": "factchecker",
        "seed": 202605190202,
        "prompt": (
            f"{STYLE}. Female fact verification officer, beautiful mature Chinese woman, graceful small oval face, "
            "soft black shoulder-length hair tucked behind one ear, optional delicate glasses, refined eyes, calm confident smile, "
            "charcoal blazer with white silk blouse and emerald pin, intelligent compliance expert temperament"
        ),
    },
    {
        "slug": "tracking-a",
        "agent": "tracking",
        "seed": 202605190301,
        "prompt": (
            f"{STYLE}. Female continuous risk tracking officer, beautiful mature Chinese woman, sleek black high ponytail, "
            "delicate oval face, alert expressive eyes, clean natural makeup, teal cyan fitted tech jacket with subtle orange accents, "
            "agile composed monitoring commander temperament, elegant and modern"
        ),
    },
    {
        "slug": "tracking-b",
        "agent": "tracking",
        "seed": 202605190302,
        "prompt": (
            f"{STYLE}. Female continuous risk tracking officer, beautiful mature Chinese woman, refined face and elegant eyes, "
            "glossy dark brown low ponytail with soft side bangs, composed confident smile, teal graphite cyber blazer, "
            "small orange alert detail, premium market surveillance expert temperament"
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
    prompt["8"]["inputs"]["steps"] = 30
    prompt["5"]["inputs"]["guidance"] = 4.0
    prompt["11"]["inputs"]["filename_prefix"] = f"finsight_agents_beauty/{job['slug']}"
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


def make_contact_sheet(paths: list[tuple[dict, Path]]) -> Path:
    thumbs = []
    for job, path in paths:
        image = Image.open(path).convert("RGB")
        image.thumbnail((300, 300), Image.Resampling.LANCZOS)
        thumbs.append((job, image.copy()))

    cols = 3
    cell_w = 340
    cell_h = 370
    sheet = Image.new("RGB", (cols * cell_w, 2 * cell_h), (246, 248, 251))
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
        draw.text((x0 + 22, y0 + 325), f"{job['agent']} / {job['slug']}", fill=(28, 38, 54), font=font)

    out = OUT_DIR / "finsight-beauty-candidates-contact-sheet.jpg"
    sheet.save(out, quality=94)
    return out


def main() -> None:
    wait_ready()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    final_paths: list[tuple[dict, Path]] = []

    for job in JOBS:
        prompt_id = submit(job)
        source = wait_output(prompt_id)
        target = OUT_DIR / f"finsight-{job['slug']}-source-magenta.png"
        shutil.copy2(source, target)
        final_paths.append((job, target))
        print(f"{job['agent']} {job['slug']}: {target}")

    print(f"contact_sheet: {make_contact_sheet(final_paths)}")


if __name__ == "__main__":
    main()
