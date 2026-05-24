#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import create_finsight_tracking_office_lady_avatar_assets as base


base.CANDIDATE_DIR = (
    base.PROJECT_OUT / "beauty-candidates" / "tracking-office-lady-fullbody-20260520"
)

FULLBODY_STYLE = (
    "FinSight_tracking AI agent full-body avatar candidate, mature professional premium cartoon character, "
    "semi-realistic 3D full-length character portrait, refined Chinese aesthetic, breathtakingly beautiful adult East Asian woman, "
    "ultra high attractiveness, stunningly gorgeous face, slim graceful build, slender waist, long elegant legs, narrow shoulders, "
    "long flowing hair only, exquisite facial features, elegant almond eyes, refined nose bridge, soft refined lips, "
    "smooth luminous skin, high-end cinematic beauty lighting, full figure visible from head to shoes with generous padding, "
    "centered, clean slender silhouette, crisp edges, business appropriate, polished but not plastic, suitable for chatbot avatar, "
    "no text, no logo, isolated on a perfectly flat solid #ff00ff chroma-key background, no shadows on background, no gradients"
)

base.NEGATIVE = (
    "low quality, blurry, ugly, unattractive, generic face, uncanny face, deformed face, distorted eyes, "
    "asymmetric eyes, bad anatomy, childlike, baby face, old, harsh wrinkles, masculine woman, western face, "
    "caucasian, plastic toy, doll, mascot costume, flat 2d icon, overdone influencer makeup, heavy makeup, "
    "vulgar, nude, lingerie, swimsuit, explicit, fetish, text, logo, watermark, cropped head, cropped feet, cropped legs, "
    "half body, bust portrait, shoulder-up portrait, close-up portrait, hands dominating frame, extra people, messy background, "
    "office room background, scenery, charts, HUD, floating panels, props, white hair, silver hair, gray hair, grey hair, "
    "platinum hair, short hair, bob haircut, ponytail, average face, plain face, heavy build, stocky body, bulky shoulders"
)

base.JOBS = [
    {
        "slug": "tracking-fullbody-a",
        "seed": 202605201201,
        "prompt": (
            f"{FULLBODY_STYLE}. A-share secondary-market financial tracking and early-warning chief officer, "
            "long glossy black hair falling over one shoulder, slim delicate rim glasses, poised and alert eyes, "
            "charcoal tailored blazer with narrow waistline, crisp white silk blouse, fitted pencil skirt, black sheer tights, "
            "elegant heels, subtle teal lapel accent, refined office lady aura, slightly alluring through professional tailoring, "
            "calm evidence-chain risk controller, structured warning-level temperament"
        ),
    },
    {
        "slug": "tracking-fullbody-b",
        "seed": 202605201202,
        "prompt": (
            f"{FULLBODY_STYLE}. Continuous financial tracking director for A-share listed companies, "
            "waist-length deep chestnut brown hair with soft side part, no glasses, confident composed gaze, "
            "graphite fitted blazer, ivory blouse, slender teal neck scarf, high-waisted tailored skirt, elegant black heels, "
            "tiny orange alert-pin detail, absolute office lady elegance, beautiful and mildly seductive but still formal, "
            "expert in tracking items, metric panels, alert reports, update records, and audit-ready source evidence"
        ),
    },
    {
        "slug": "tracking-fullbody-c",
        "seed": 202605201203,
        "prompt": (
            f"{FULLBODY_STYLE}. Premium finance surveillance office lady and A-share risk monitoring specialist, "
            "long straight black hair, delicate transparent glasses, intelligent sharp eyes, refined red-brown lips, "
            "fitted black blazer, white blouse, slim tailored trousers, teal cuff accent, restrained orange risk-warning brooch, "
            "serene but formidable, very slim elegant posture, evidence-first analyst who detects unit, scale, and period inconsistencies"
        ),
    },
    {
        "slug": "tracking-fullbody-d",
        "seed": 202605201204,
        "prompt": (
            f"{FULLBODY_STYLE}. FinSight_tracking financial early-warning office lady, "
            "long wavy black hair with glossy volume, captivating calm eyes, subtle natural makeup, no glasses, "
            "dark charcoal tailored blazer with narrow waistline, elegant satin white blouse with tasteful neckline, "
            "slim high-waisted office skirt, black stockings, refined heels, slim teal jewelry accent, "
            "refined orange warning-detail on lapel, stunning mature beauty with restrained sensual confidence, "
            "professional risk-observation commander for tracking lists, sentiment monitoring, metrics panels, and four-level alerts"
        ),
    },
]


def main() -> None:
    base.generate_candidates()


if __name__ == "__main__":
    main()
