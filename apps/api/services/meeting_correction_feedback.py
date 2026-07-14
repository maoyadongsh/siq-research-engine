"""Server-side diffing and conservative ASR correction classification."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from services.meeting_contracts import (
    MEETING_DIFF_SCHEMA_VERSION,
    CandidateTermInput,
    CorrectionEditIntent,
    CorrectionErrorClass,
    DiffOperation,
)

_CHINESE_NUMERALS = frozenset("零〇一二三四五六七八九十百千万亿两")


def calculate_diff(original: str, corrected: str) -> dict:
    operations: list[dict] = []
    matcher = SequenceMatcher(a=original, b=corrected, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        operations.append(
            DiffOperation(
                op=tag,
                original_start=i1,
                original_end=i2,
                corrected_start=j1,
                corrected_end=j2,
                original=original[i1:i2],
                corrected=corrected[j1:j2],
            ).model_dump()
        )
    return {"schema_version": MEETING_DIFF_SCHEMA_VERSION, "operations": operations}


def _without_punctuation(value: str) -> str:
    return "".join(
        character
        for character in value
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


def _looks_like_itn_change(original: str, corrected: str) -> bool:
    original_has_digits = bool(re.search(r"\d", original))
    corrected_has_digits = bool(re.search(r"\d", corrected))
    original_has_chinese_number = any(value in _CHINESE_NUMERALS for value in original)
    corrected_has_chinese_number = any(value in _CHINESE_NUMERALS for value in corrected)
    return (original_has_digits and corrected_has_chinese_number) or (
        corrected_has_digits and original_has_chinese_number
    )


def classify_correction(
    original: str,
    corrected: str,
    intent: str | CorrectionEditIntent,
    *,
    has_candidate_terms: bool = False,
) -> CorrectionErrorClass:
    edit_intent = CorrectionEditIntent(intent)
    if edit_intent != CorrectionEditIntent.ASR_ERROR:
        return CorrectionErrorClass.REWRITE
    if _without_punctuation(original) == _without_punctuation(corrected):
        return CorrectionErrorClass.PUNCTUATION
    if _looks_like_itn_change(original, corrected):
        return CorrectionErrorClass.ITN
    if has_candidate_terms:
        return CorrectionErrorClass.ENTITY

    opcodes = [item[0] for item in SequenceMatcher(a=original, b=corrected, autojunk=False).get_opcodes() if item[0] != "equal"]
    if opcodes and set(opcodes) == {"delete"}:
        return CorrectionErrorClass.DELETION
    if opcodes and set(opcodes) == {"insert"}:
        return CorrectionErrorClass.INSERTION
    similarity = SequenceMatcher(a=original, b=corrected, autojunk=False).ratio()
    if similarity < 0.45:
        return CorrectionErrorClass.REWRITE
    return CorrectionErrorClass.LEXICAL


def contribution_is_eligible(
    *,
    original: str,
    corrected: str,
    intent: str | CorrectionEditIntent,
    requested: bool,
    error_class: str | CorrectionErrorClass,
) -> bool:
    if not requested or original == corrected:
        return False
    if CorrectionEditIntent(intent) != CorrectionEditIntent.ASR_ERROR:
        return False
    return CorrectionErrorClass(error_class) not in {
        CorrectionErrorClass.PUNCTUATION,
        CorrectionErrorClass.REWRITE,
    }


def validated_candidate_terms(
    original: str,
    corrected: str,
    submitted: list[CandidateTermInput],
) -> list[CandidateTermInput]:
    """Keep only minimal, evidenced pairs; never trust client ranges or labels."""

    accepted: list[CandidateTermInput] = []
    seen: set[tuple[str, str]] = set()
    for candidate in submitted:
        canonical = candidate.canonical_term.strip()
        misrecognition = candidate.misrecognition.strip()
        identity = (canonical.casefold(), misrecognition.casefold())
        if not canonical or canonical not in corrected or identity in seen:
            continue
        if misrecognition and misrecognition not in original:
            continue
        if canonical == misrecognition or len(canonical) > 80 or len(misrecognition) > 80:
            continue
        accepted.append(
            CandidateTermInput(
                canonical_term=canonical,
                misrecognition=misrecognition,
                promote_now=False,
            )
        )
        seen.add(identity)
    return accepted
