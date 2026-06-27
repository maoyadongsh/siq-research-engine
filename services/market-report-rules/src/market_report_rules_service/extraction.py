from __future__ import annotations

from .models import ExtractionResult, Market, ParsedArtifact
from .markets.cn.extractor import extract_artifact as extract_cn_artifact
from .markets.eu.extractor import extract_artifact as extract_eu_artifact
from .markets.hk.extractor import extract_artifact as extract_hk_artifact
from .markets.jp.extractor import extract_artifact as extract_jp_artifact
from .markets.kr.extractor import extract_artifact as extract_kr_artifact
from .markets.us.extractor import extract_artifact as extract_us_artifact


EXTRACTORS = {
    Market.CN: extract_cn_artifact,
    Market.HK: extract_hk_artifact,
    Market.US: extract_us_artifact,
    Market.JP: extract_jp_artifact,
    Market.KR: extract_kr_artifact,
    Market.EU: extract_eu_artifact,
}


def extract_artifact(artifact: ParsedArtifact) -> ExtractionResult:
    try:
        extractor = EXTRACTORS[artifact.market]
    except KeyError as exc:
        raise ValueError(f"Unsupported market: {artifact.market}") from exc
    return extractor(artifact)
