#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path("/home/maoyd/douge_ai_agent/finall_all_front_0516/front")
OUT = ROOT / "public/pet/agent-drafts/finsight-legal-version-comparison.png"

ITEMS = [
    ("A candidate", ROOT / "public/pet/agent-drafts/beauty-candidates/finsight-legal-a-source-magenta.png"),
    ("B candidate", ROOT / "public/pet/agent-drafts/beauty-candidates/finsight-legal-b-source-magenta.png"),
    ("C candidate", ROOT / "public/pet/agent-drafts/beauty-candidates/finsight-legal-c-source-magenta.png"),
    ("D candidate", ROOT / "public/pet/agent-drafts/beauty-candidates/finsight-legal-d-source-magenta.png"),
    ("current transparent", ROOT / "public/pet/agent-drafts/finsight-legal-avatar-transparent.png"),
    ("current webp frame", ROOT / "public/pet/agent-drafts/finsight-legal-avatar-animated-transparent.webp"),
    ("native checker source", ROOT / "public/pet/agent-drafts/finsight-legal-avatar-selected-native-checker-source.png"),
    (
        "latest raw generated",
        Path(
            "/home/maoyd/.codex/generated_images/019e443b-be04-7fe0-9134-5fb5bdc53731/"
            "ig_01264d19e9fab05c016a0d6b0ab55c8197a9f40691f5c26531.png"
        ),
    ),
]


def first_frame(path: Path) -> Image.Image:
    image = Image.open(path)
    try:
        image.seek(0)
    except EOFError:
        pass
    return image.convert("RGBA")


def tile(label: str, path: Path) -> Image.Image:
    image = first_frame(path)
    bg = Image.new("RGBA", image.size, (245, 247, 250, 255))
    bg.alpha_composite(image)
    thumb = bg.convert("RGB")
    thumb.thumbnail((300, 300), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (340, 380), (255, 255, 255))
    canvas.paste(thumb, ((340 - thumb.width) // 2, 18))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 20)
        small = ImageFont.truetype("DejaVuSans.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
        small = font
    draw.text((18, 325), label, fill=(15, 23, 42), font=font if len(label) <= 18 else small)
    draw.text((18, 350), path.name[:34], fill=(100, 116, 139), font=small)
    return canvas


def main() -> None:
    cols = 4
    rows = (len(ITEMS) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 340, rows * 380), (238, 242, 247))
    for idx, (label, path) in enumerate(ITEMS):
        sheet.paste(tile(label, path), ((idx % cols) * 340, (idx // cols) * 380))
    sheet.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
