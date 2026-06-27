from __future__ import annotations

from ...models import ExtractionResult, ParsedArtifact


def extract_artifact(artifact: ParsedArtifact) -> ExtractionResult:
    raise ValueError(
        "CN/A-share extraction is registered as a legacy adapter; "
        "call the A-share pdf-parser service until migrated."
    )
