from __future__ import annotations

from enum import StrEnum
from typing import Any
from urllib.parse import urlparse


GATE_CONTRACT_VERSION = "risk_calibrated_gate_v1"


class GateSeverity(StrEnum):
    HARD = "hard"
    SOFT = "soft"
    OBSERVE = "observe"


class GateMode(StrEnum):
    OBSERVE = "observe"
    WARN = "warn"
    ENFORCE = "enforce"


class GateDecision(StrEnum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class PromotionTarget(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    CANONICAL = "canonical"
    RETRIEVAL = "retrieval"
    PRODUCTION = "production"


PROMOTION_TARGETS = tuple(target.value for target in PromotionTarget)
_DECISION_RANK = {
    GateDecision.ALLOW.value: 0,
    GateDecision.REVIEW.value: 1,
    GateDecision.BLOCK.value: 2,
}
_SEVERITY_RANK = {
    GateSeverity.OBSERVE.value: 0,
    GateSeverity.SOFT.value: 1,
    GateSeverity.HARD.value: 2,
}
SOURCE_MANIFEST_VERSION = "siq_source_manifest_v1"
OFFICIAL_REGULATOR_TIER = "official_regulator"
OFFICIAL_ISSUER_TIER = "official_issuer"
RECOGNIZED_VENDOR_TIER = "recognized_vendor"
UNVERIFIED_WEB_TIER = "unverified_web"
LOCAL_UPLOADED_TIER = "local_uploaded"
OFFICIAL_EVIDENCE_TIERS = frozenset({OFFICIAL_REGULATOR_TIER, OFFICIAL_ISSUER_TIER})
REVIEW_SOURCE_TIERS = frozenset({RECOGNIZED_VENDOR_TIER, UNVERIFIED_WEB_TIER, LOCAL_UPLOADED_TIER})

OFFICIAL_REGULATOR_HOST_SUFFIXES_BY_MARKET = {
    "CN": ("cninfo.com.cn",),
    "HK": ("hkexnews.hk", "hkex.com.hk"),
    "US": ("sec.gov",),
    "EU": (
        "filings.xbrl.org",
        "sec.gov",
        "fca.org.uk",
        "amf-france.org",
        "info-financiere.fr",
        "unternehmensregister.de",
        "bundesanzeiger.de",
        "afm.nl",
        "six-group.com",
        "ser-ag.com",
        "londonstockexchange.com",
        "investegate.co.uk",
        "lseg.com",
    ),
    "JP": ("edinet-fsa.go.jp", "release.tdnet.info", "jpx.co.jp", "www2.jpx.co.jp"),
    "KR": ("dart.fss.or.kr", "opendart.fss.or.kr", "englishdart.fss.or.kr", "kind.krx.co.kr"),
}


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _first_value(*values: Any) -> Any:
    for value in values:
        if _has_value(value):
            return value
    return None


def _host_matches(host: str, suffix: str) -> bool:
    normalized_host = str(host or "").rstrip(".").lower()
    normalized_suffix = str(suffix or "").rstrip(".").lower()
    return normalized_host == normalized_suffix or normalized_host.endswith(f".{normalized_suffix}")


def _url_host(value: Any) -> str | None:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").rstrip(".").lower()
    return host or None


def _url_matches_any(value: Any, suffixes: tuple[str, ...]) -> bool:
    host = _url_host(value)
    return bool(host and any(_host_matches(host, suffix) for suffix in suffixes))


def _official_regulator_suffixes(market: Any) -> tuple[str, ...]:
    return OFFICIAL_REGULATOR_HOST_SUFFIXES_BY_MARKET.get(str(market or "").strip().upper(), ())


def _normalize_source_tier(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {
        OFFICIAL_REGULATOR_TIER,
        "official_regulator_source",
        "official_mirror",
        "official_exchange",
        "regulator",
        "exchange",
        "statutory_public_html",
        "statutory_public_pdf",
    }:
        return OFFICIAL_REGULATOR_TIER
    if text in {OFFICIAL_ISSUER_TIER, "official_direct", "issuer", "issuer_official_direct", "official_issuer_direct"}:
        return OFFICIAL_ISSUER_TIER
    if text in {RECOGNIZED_VENDOR_TIER, "vendor", "mainstream_repository"}:
        return RECOGNIZED_VENDOR_TIER
    if text in {UNVERIFIED_WEB_TIER, "manual_unverified", "manual", "unverified", "unknown"}:
        return UNVERIFIED_WEB_TIER
    if text in {LOCAL_UPLOADED_TIER, "local", "upload", "uploaded"}:
        return LOCAL_UPLOADED_TIER
    if text == "official":
        return OFFICIAL_REGULATOR_TIER
    return None


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "verified", "official_verified"}


def _source_manifest_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    payload = manifest.get("source_manifest") if isinstance(manifest.get("source_manifest"), dict) else {}
    return payload if isinstance(payload, dict) else {}


def _content_hash_digest(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1]
    return text or None


def source_manifest_summary(*, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = manifest if isinstance(manifest, dict) else {}
    source_manifest = _source_manifest_payload(manifest)
    market = str(manifest.get("market") or "").strip().upper()
    suffixes = _official_regulator_suffixes(market)
    source_url = _first_value(
        source_manifest.get("final_url"),
        source_manifest.get("source_url"),
        manifest.get("final_url"),
        manifest.get("effective_url"),
        manifest.get("source_url"),
        source_manifest.get("initial_url"),
        manifest.get("initial_url"),
    )
    initial_url = _first_value(source_manifest.get("initial_url"), manifest.get("initial_url"), manifest.get("source_url"))
    final_url = _first_value(source_manifest.get("final_url"), manifest.get("final_url"), manifest.get("effective_url"), manifest.get("source_url"))
    raw_tier = _first_value(manifest.get("source_tier"), source_manifest.get("source_tier"))
    source_tier = _normalize_source_tier(raw_tier)
    regulator_url_verified = _url_matches_any(final_url or source_url, suffixes)
    if source_tier is None:
        if regulator_url_verified:
            source_tier = OFFICIAL_REGULATOR_TIER
        elif manifest.get("local_source_path") and not _has_value(source_url):
            source_tier = LOCAL_UPLOADED_TIER
        else:
            source_tier = UNVERIFIED_WEB_TIER

    source_verification_status = _first_value(
        manifest.get("source_verification_status"),
        source_manifest.get("source_verification_status"),
    )
    issuer_domain_verified = _boolish(
        _first_value(
            manifest.get("issuer_domain_verified"),
            source_manifest.get("issuer_domain_verified"),
            source_manifest.get("issuer_domain_verification_status"),
        )
    ) or (source_tier == OFFICIAL_ISSUER_TIER and str(source_verification_status or "").lower() == "official_verified")
    regulator_host_verified = _boolish(
        _first_value(source_manifest.get("regulator_host_verified"), manifest.get("regulator_host_verified"))
    ) or regulator_url_verified

    redirect_chain = _first_value(source_manifest.get("redirect_chain"), manifest.get("redirect_chain"), [])
    redirect_chain_valid = isinstance(redirect_chain, list)
    content_sha256 = _content_hash_digest(_first_value(source_manifest.get("content_sha256"), manifest.get("content_sha256")))
    content_hash = _content_hash_digest(_first_value(source_manifest.get("content_hash"), manifest.get("content_hash")))
    hash_digest = content_sha256 or content_hash
    hash_consistent = not (content_sha256 and content_hash) or content_sha256 == content_hash
    retrieved_at = _first_value(source_manifest.get("retrieved_at"), manifest.get("retrieved_at"))
    missing_fields: list[str] = []
    if not _has_value(initial_url):
        missing_fields.append("initial_url")
    if not _has_value(final_url):
        missing_fields.append("final_url")
    if "redirect_chain" not in source_manifest and "redirect_chain" not in manifest:
        missing_fields.append("redirect_chain")
    elif not redirect_chain_valid:
        missing_fields.append("redirect_chain:list")
    if not hash_digest:
        missing_fields.append("content_hash")
    if not _has_value(retrieved_at):
        missing_fields.append("retrieved_at")

    issues: list[dict[str, Any]] = []
    evidence_refs = ["manifest.json:source_manifest", "manifest.json:source_url"]
    if source_tier == OFFICIAL_REGULATOR_TIER and not regulator_host_verified:
        issues.append(
            {
                "rule_id": "package.source.official_regulator_unverified",
                "severity": GateSeverity.HARD.value,
                "reason": "official regulator source URL is outside the market allowlist",
                "evidence_refs": evidence_refs,
            }
        )
    if source_tier == OFFICIAL_ISSUER_TIER and not issuer_domain_verified:
        issues.append(
            {
                "rule_id": "package.source.official_issuer_unverified",
                "severity": GateSeverity.HARD.value,
                "reason": "official issuer source lacks issuer domain verification",
                "evidence_refs": evidence_refs,
            }
        )
    if source_tier in REVIEW_SOURCE_TIERS:
        issues.append(
            {
                "rule_id": "package.source.unverified_for_official_evidence",
                "severity": GateSeverity.SOFT.value,
                "reason": f"{source_tier} source cannot directly support official evidence",
                "evidence_refs": evidence_refs,
            }
        )
    if missing_fields:
        issues.append(
            {
                "rule_id": "package.source_manifest.missing_fields",
                "severity": GateSeverity.SOFT.value,
                "reason": f"source manifest missing fields: {', '.join(missing_fields)}",
                "evidence_refs": evidence_refs,
            }
        )
    if not hash_consistent:
        issues.append(
            {
                "rule_id": "package.source_manifest.hash_inconsistent",
                "severity": GateSeverity.HARD.value,
                "reason": "source manifest content_hash and content_sha256 disagree",
                "evidence_refs": evidence_refs,
            }
        )

    hard_issue_count = sum(1 for issue in issues if issue["severity"] == GateSeverity.HARD.value)
    review_issue_count = sum(1 for issue in issues if issue["severity"] == GateSeverity.SOFT.value)
    official_evidence_allowed = source_tier in OFFICIAL_EVIDENCE_TIERS and hard_issue_count == 0 and review_issue_count == 0
    return {
        "schema_version": "siq_source_summary_v1",
        "source_manifest_schema_version": source_manifest.get("schema_version"),
        "market": market,
        "source_tier": source_tier,
        "raw_source_tier": raw_tier,
        "source_verification_status": source_verification_status,
        "official_evidence_allowed": official_evidence_allowed,
        "regulator_host_verified": regulator_host_verified,
        "issuer_domain_verified": issuer_domain_verified,
        "initial_url": initial_url,
        "final_url": final_url,
        "redirect_chain": redirect_chain if redirect_chain_valid else None,
        "content_hash": f"sha256:{hash_digest}" if hash_digest else None,
        "retrieved_at": retrieved_at,
        "missing_fields": missing_fields,
        "hash_consistent": hash_consistent,
        "issues": issues,
        "hard_issue_count": hard_issue_count,
        "review_issue_count": review_issue_count,
    }


def gate_mode_for_severity(severity: str) -> str:
    if severity == GateSeverity.HARD.value:
        return GateMode.ENFORCE.value
    if severity == GateSeverity.SOFT.value:
        return GateMode.WARN.value
    return GateMode.OBSERVE.value


def gate_decisions_for_severity(severity: str) -> dict[str, str]:
    if severity == GateSeverity.HARD.value:
        return {
            PromotionTarget.DRAFT.value: GateDecision.ALLOW.value,
            PromotionTarget.REVIEW.value: GateDecision.REVIEW.value,
            PromotionTarget.CANONICAL.value: GateDecision.BLOCK.value,
            PromotionTarget.RETRIEVAL.value: GateDecision.BLOCK.value,
            PromotionTarget.PRODUCTION.value: GateDecision.BLOCK.value,
        }
    if severity == GateSeverity.SOFT.value:
        return {
            PromotionTarget.DRAFT.value: GateDecision.ALLOW.value,
            PromotionTarget.REVIEW.value: GateDecision.REVIEW.value,
            PromotionTarget.CANONICAL.value: GateDecision.REVIEW.value,
            PromotionTarget.RETRIEVAL.value: GateDecision.REVIEW.value,
            PromotionTarget.PRODUCTION.value: GateDecision.REVIEW.value,
        }
    return {target: GateDecision.ALLOW.value for target in PROMOTION_TARGETS}


def gate_results_for_issue(
    *,
    rule_id: str,
    severity: str,
    reason: str,
    evidence_refs: list[str] | None = None,
    mode: str | None = None,
    decisions_by_target: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    decisions = {**gate_decisions_for_severity(severity), **(decisions_by_target or {})}
    gate_mode = mode or gate_mode_for_severity(severity)
    refs = [str(ref) for ref in (evidence_refs or []) if str(ref or "").strip()]
    return [
        {
            "rule_id": rule_id,
            "severity": severity,
            "mode": gate_mode,
            "decision": decisions.get(target, GateDecision.ALLOW.value),
            "target": target,
            "promotion_target": target,
            "reason": reason,
            "evidence_refs": refs,
        }
        for target in PROMOTION_TARGETS
    ]


def aggregate_gate_decisions(gate_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {
        target: {
            "target": target,
            "promotion_target": target,
            "decision": GateDecision.ALLOW.value,
            "severity": GateSeverity.OBSERVE.value,
            "rule_ids": [],
            "review_rule_ids": [],
            "blocking_rule_ids": [],
            "reasons": [],
        }
        for target in PROMOTION_TARGETS
    }
    for gate in gate_results:
        target = str(gate.get("target") or "")
        if target not in decisions:
            continue
        current = decisions[target]
        decision = str(gate.get("decision") or GateDecision.ALLOW.value)
        severity = str(gate.get("severity") or GateSeverity.OBSERVE.value)
        if _DECISION_RANK.get(decision, 0) > _DECISION_RANK.get(str(current["decision"]), 0):
            current["decision"] = decision
        if _SEVERITY_RANK.get(severity, 0) > _SEVERITY_RANK.get(str(current["severity"]), 0):
            current["severity"] = severity
        rule_id = str(gate.get("rule_id") or "")
        if rule_id:
            current["rule_ids"].append(rule_id)
            if decision == GateDecision.BLOCK.value:
                current["blocking_rule_ids"].append(rule_id)
            elif decision == GateDecision.REVIEW.value:
                current["review_rule_ids"].append(rule_id)
        reason = str(gate.get("reason") or "")
        if reason:
            current["reasons"].append(reason)

    for payload in decisions.values():
        for key in ("rule_ids", "review_rule_ids", "blocking_rule_ids", "reasons"):
            seen: set[str] = set()
            payload[key] = [item for item in payload[key] if not (item in seen or seen.add(item))]
    return decisions


def gate_rule_ids(gate_results: list[dict[str, Any]], severity: str) -> set[str]:
    return {
        str(gate.get("rule_id"))
        for gate in gate_results
        if gate.get("severity") == severity and gate.get("rule_id")
    }


_gate_mode_for_severity = gate_mode_for_severity
_gate_decisions_for_severity = gate_decisions_for_severity
_gate_results_for_issue = gate_results_for_issue
_aggregate_gate_decisions = aggregate_gate_decisions
_gate_rule_ids = gate_rule_ids
