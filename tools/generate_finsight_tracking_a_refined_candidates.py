#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import generate_finsight_tracking_idphoto_candidates as base


base.OUT_DIR = (
    Path("/home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts")
    / "beauty-candidates"
    / "tracking-a-refined-20260520"
)

base.STYLE = (
    "FinSight_tracking AI agent professional ID-photo style avatar candidate, mature professional premium cartoon character, "
    "semi-realistic 3D portrait, refined Chinese aesthetic, breathtakingly beautiful adult East Asian woman, ultra high attractiveness, "
    "stunning gorgeous face, excellent auspicious facial impression, elegant trustworthy face, intelligent kind eyes, "
    "soft confident smile, slender elegant office lady, slim neck, delicate narrow shoulders, long flowing black hair only, "
    "exquisite facial features, harmonious natural proportions, elegant almond eyes, refined nose bridge, soft refined lips, "
    "smooth luminous skin, high-end cinematic beauty lighting, formal professional portrait composition, centered, "
    "head-and-shoulders to upper-chest visible like a premium corporate ID portrait, not a close-up, not full body, "
    "clean slender silhouette, crisp edges, business appropriate, polished but not plastic, suitable for chatbot avatar, no text, no logo, "
    "isolated on a perfectly flat solid #ff00ff chroma-key background, no shadows on background, no gradients"
)

base.NEGATIVE = (
    "low quality, blurry, ugly, unattractive, average face, plain face, generic face, stern face, mean face, tired face, "
    "uncanny face, deformed face, distorted eyes, asymmetric eyes, bad anatomy, childlike, baby face, old, harsh wrinkles, "
    "masculine woman, western face, caucasian, plastic toy, doll, mascot costume, flat 2d icon, overdone influencer makeup, "
    "heavy makeup, vulgar, nude, lingerie, swimsuit, explicit, fetish, text, logo, watermark, full body, tiny face, cropped head, "
    "cropped hair, cropped shoulders, hands, extra people, messy background, office room background, scenery, charts, HUD, "
    "floating panels, props, white hair, silver hair, gray hair, grey hair, platinum hair, short hair, bob haircut, ponytail, "
    "heavy build, stocky body, bulky shoulders"
)

base.JOBS = [
    {
        "slug": "tracking-a-refined-1",
        "seed": 202605201401,
        "prompt": (
            f"{base.STYLE}. A-share secondary-market financial tracking and early-warning chief officer, "
            "long glossy black hair draped over one shoulder, delicate slim rim glasses, bright gentle intelligent eyes, "
            "more beautiful balanced face, refined oval face, graceful professional expression, "
            "charcoal tailored blazer, crisp white silk blouse with tasteful open collar, subtle teal lapel accent, "
            "elegant office lady aura, slightly alluring through refined tailoring, calm evidence-chain risk controller"
        ),
    },
    {
        "slug": "tracking-a-refined-2",
        "seed": 202605201402,
        "prompt": (
            f"{base.STYLE}. FinSight_tracking evidence-chain risk controller, "
            "long smooth black hair over shoulders, thin transparent glasses, exquisitely beautiful face, "
            "soft almond eyes with composed warmth, gentle confident smile, refined slim face and graceful jawline, "
            "dark charcoal blazer, white blouse, teal accent strip, mature high-end office lady temperament, "
            "trustworthy metric tracking and early-warning specialist"
        ),
    },
    {
        "slug": "tracking-a-refined-3",
        "seed": 202605201403,
        "prompt": (
            f"{base.STYLE}. A-share listed-company financial monitoring director, "
            "long black side-parted hair, elegant delicate glasses, stunning face with soft noble features, "
            "clear clever eyes, natural refined makeup, kind but sharp expression, slim neck and narrow shoulders, "
            "fitted black blazer, white satin blouse, teal lapel detail, slightly seductive only through polished office tailoring, "
            "professional citation-discipline and warning-level temperament"
        ),
    },
    {
        "slug": "tracking-a-refined-4",
        "seed": 202605201404,
        "prompt": (
            f"{base.STYLE}. Premium financial tracking office lady and risk alert chief officer, "
            "long glossy black hair framing the face, elegant slim rim glasses, ultra beautiful face, "
            "warm trustworthy smile, luminous skin, graceful eyes, excellent face harmony and approachable authority, "
            "charcoal blazer with slender waist impression, white blouse with tasteful neckline, subtle teal accent, "
            "calm precise evidence-first analyst, refined mature beauty"
        ),
    },
]


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
