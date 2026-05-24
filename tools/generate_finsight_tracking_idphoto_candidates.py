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
    "/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts/beauty-candidates/tracking-idphoto-20260520"
)

NEGATIVE = (
    "low quality, blurry, ugly, unattractive, average face, plain face, generic face, uncanny face, deformed face, "
    "distorted eyes, asymmetric eyes, bad anatomy, childlike, baby face, old, harsh wrinkles, masculine woman, western face, "
    "caucasian, plastic toy, doll, mascot costume, flat 2d icon, overdone influencer makeup, heavy makeup, vulgar, nude, "
    "lingerie, swimsuit, explicit, fetish, text, logo, watermark, full body, tiny face, cropped head, cropped hair, cropped shoulders, "
    "hands, extra people, messy background, office room background, scenery, charts, HUD, floating panels, props, white hair, "
    "silver hair, gray hair, grey hair, platinum hair, short hair, bob haircut, ponytail, heavy build, stocky body, bulky shoulders"
)

STYLE = (
    "FinSight_tracking AI agent professional ID-photo style avatar candidate, mature professional premium cartoon character, "
    "semi-realistic 3D portrait, refined Chinese aesthetic, breathtakingly beautiful adult East Asian woman, ultra high attractiveness, "
    "stunning gorgeous face, slender elegant office lady, slim neck, delicate narrow shoulders, long flowing hair only, "
    "exquisite facial features, harmonious natural proportions, elegant almond eyes, refined nose bridge, soft refined lips, "
    "smooth luminous skin, high-end cinematic beauty lighting, formal professional portrait composition, centered, "
    "head-and-shoulders to upper-chest visible like a premium corporate ID portrait, not a close-up, not full body, "
    "clean slender silhouette, crisp edges, business appropriate, polished but not plastic, suitable for chatbot avatar, no text, no logo, "
    "isolated on a perfectly flat solid #ff00ff chroma-key background, no shadows on background, no gradients"
)

JOBS = [
    {
        "slug": "tracking-idphoto-a",
        "seed": 202605201301,
        "prompt": (
            f"{STYLE}. A-share secondary-market financial tracking and early-warning chief officer, "
            "long glossy black hair draped over one shoulder, poised alert eyes, slim delicate rim glasses, "
            "charcoal tailored blazer, crisp white silk blouse with tasteful open collar, subtle teal lapel accent, "
            "elegant office lady aura, slightly alluring through refined tailoring, calm evidence-chain risk controller, "
            "financial metrics consistency, citation discipline, structured warning-level temperament"
        ),
    },
    {
        "slug": "tracking-idphoto-b",
        "seed": 202605201302,
        "prompt": (
            f"{STYLE}. Continuous financial tracking director for A-share listed companies, "
            "waist-length deep chestnut brown hair with soft side part visible around shoulders, confident composed gaze, no glasses, "
            "graphite fitted blazer, ivory blouse, slender teal neck scarf, tiny orange alert-pin detail, "
            "absolute office lady elegance, beautiful and mildly seductive but still formal, "
            "expert in tracking items, metric panels, alert reports, update records, and audit-ready source evidence"
        ),
    },
    {
        "slug": "tracking-idphoto-c",
        "seed": 202605201303,
        "prompt": (
            f"{STYLE}. Premium finance surveillance office lady and A-share risk monitoring specialist, "
            "long straight black hair falling past shoulders, intelligent sharp eyes, refined red-brown lips, delicate transparent glasses, "
            "fitted black blazer over white blouse, teal cuff accent, restrained orange risk-warning brooch, "
            "serene but formidable, mature beauty, evidence-first analyst who detects unit, scale, and period inconsistencies"
        ),
    },
    {
        "slug": "tracking-idphoto-d",
        "seed": 202605201304,
        "prompt": (
            f"{STYLE}. FinSight_tracking financial early-warning office lady, "
            "long wavy black hair with glossy volume around shoulders, captivating calm eyes, subtle natural makeup, no glasses, "
            "dark charcoal tailored blazer with narrow waist impression, elegant satin white blouse with tasteful neckline, slim teal jewelry accent, "
            "refined orange warning-detail on lapel, stunning mature beauty with restrained sensual confidence, "
            "professional risk-observation commander for tracking lists, sentiment monitoring, metrics panels, and four-level alerts"
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
    prompt["11"]["inputs"]["filename_prefix"] = f"finsight_agents_tracking_idphoto/{job['slug']}"
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
        image.thumbnail((320, 320), Image.Resampling.LANCZOS)
        thumbs.append((job, image.copy()))

    cols = 2
    rows = 2
    cell_w = 400
    cell_h = 395
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
        label = job["slug"].replace("tracking-idphoto-", "").upper()
        draw.text((x0 + 28, y0 + 346), f"tracking ID-photo {label}", fill=(28, 38, 54), font=font)

    out = OUT_DIR / "finsight-tracking-idphoto-contact-sheet.jpg"
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
        print(f"candidate {job['slug']}: {target}")

    print(f"contact_sheet: {make_contact_sheet(final_paths)}")


if __name__ == "__main__":
    main()
