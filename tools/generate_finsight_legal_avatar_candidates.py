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
    "asymmetric eyes, bad anatomy, childlike, baby face, old, harsh wrinkles, feminine man, western face, "
    "caucasian, plastic toy, doll, mascot costume, flat 2d icon, overdone influencer makeup, heavy makeup, "
    "vulgar, text, logo, watermark, cropped head, full body, hands, extra people, messy background, scenery, "
    "charts, HUD, floating panels, props, court room, judge robe, wig, gavel in hand, white hair, silver hair, "
    "gray hair, grey hair, platinum hair, sunglasses, opaque glasses, distorted glasses, broken glasses"
)

STYLE = (
    "FinSight AI agent avatar, transparent-background source candidate, mature professional premium cartoon character, "
    "semi-realistic 3D portrait, refined Chinese aesthetic, extremely handsome adult East Asian man, high attractiveness, "
    "exquisite facial features, harmonious natural proportions, clear intelligent eyes, elegant straight nose bridge, "
    "refined clean jawline, smooth luminous skin, high-end cinematic beauty lighting, shoulder-up close-up portrait, "
    "centered, clean silhouette, crisp edges, business appropriate, polished but not plastic, suitable for chatbot avatar, "
    "no text, no logo, isolated on a perfectly flat solid #ff00ff chroma-key background, no shadows on background, no gradients"
)

JOBS = [
    {
        "slug": "legal-a",
        "seed": 202605200401,
        "prompt": (
            f"{STYLE}. Capital markets lawyer and legal compliance counsel, very handsome young mature Chinese man, "
            "slim silver metal rim glasses, sharp warm eyes visible clearly through the lenses, neat black side-parted hair, "
            "dark charcoal tailored suit, crisp white shirt, deep burgundy tie, subtle silver scales-of-justice lapel pin, "
            "calm authoritative expression, premium securities lawyer temperament, trustworthy, elegant, refined"
        ),
    },
    {
        "slug": "legal-b",
        "seed": 202605200402,
        "prompt": (
            f"{STYLE}. Capital markets lawyer and legal opinion specialist, extremely handsome Chinese man, "
            "thin dark titanium rectangular glasses, bright intelligent eyes, clean defined eyebrows, short black hair with natural volume, "
            "midnight navy tailored suit, white shirt, muted gold tie bar, tiny embossed legal seal lapel detail, "
            "confident restrained smile, polished elite counsel temperament, beautiful face, high charisma"
        ),
    },
    {
        "slug": "legal-c",
        "seed": 202605200403,
        "prompt": (
            f"{STYLE}. Securities compliance attorney, handsome mature Chinese man, delicate rimless glasses, "
            "precise calm gaze, elegant face, neat dark hair swept back softly, black tailored suit with subtle blue undertone, "
            "white shirt, dark wine tie, small silver shield and scales lapel pin, composed legal strategist energy, "
            "clean premium executive portrait, highly attractive but professional"
        ),
    },
    {
        "slug": "legal-d",
        "seed": 202605200404,
        "prompt": (
            f"{STYLE}. Senior legal counsel for listed companies, very handsome Chinese man, fine gold half-rim glasses, "
            "gentle sharp eyes, sophisticated clean face, thick dark hair neatly styled, deep graphite suit, white shirt, "
            "navy tie with tiny burgundy accent, miniature legal seal pin, calm powerful presence, elegant capital-market lawyer, "
            "luxury professional avatar, high-end charisma"
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
    prompt["11"]["inputs"]["filename_prefix"] = f"finsight_agents_legal/{job['slug']}"
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
        draw.text((x0 + 22, y0 + 325), f"legal / {job['slug']}", fill=(28, 38, 54), font=font)

    out = OUT_DIR / "finsight-legal-avatar-candidates-contact-sheet.jpg"
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
        print(f"legal {job['slug']}: {target}")

    print(f"contact_sheet: {make_contact_sheet(final_paths)}")


if __name__ == "__main__":
    main()
