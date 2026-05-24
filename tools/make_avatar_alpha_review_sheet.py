#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ASSET_DIR = Path("/home/maoyd/douge_ai_agent/finall_all_front_0516/front/public/pet/agent-drafts")
OUT = ASSET_DIR / "finsight-selected-a-alpha-review-sheet.png"
FILES = [
    ("analysis", ASSET_DIR / "finsight-analysis-avatar-transparent.png"),
    ("factchecker", ASSET_DIR / "finsight-factchecker-avatar-transparent.png"),
    ("tracking", ASSET_DIR / "finsight-tracking-avatar-transparent.png"),
]


def checker(size: tuple[int, int], cell: int = 24) -> Image.Image:
    image = Image.new("RGB", size, (245, 247, 250))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(212, 219, 230))
    return image


def main() -> None:
    cell_w = 360
    cell_h = 420
    sheet = Image.new("RGB", (cell_w * len(FILES), cell_h), (248, 250, 252))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        font = ImageFont.load_default()

    for idx, (label, path) in enumerate(FILES):
        image = Image.open(path).convert("RGBA")
        image.thumbnail((320, 320), Image.Resampling.LANCZOS)
        bg = checker((320, 320))
        bg.paste(image, ((320 - image.width) // 2, (320 - image.height) // 2), image)
        x = idx * cell_w + 20
        sheet.paste(bg, (x, 20))
        draw.text((x, 356), label, fill=(15, 23, 42), font=font)

    sheet.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
