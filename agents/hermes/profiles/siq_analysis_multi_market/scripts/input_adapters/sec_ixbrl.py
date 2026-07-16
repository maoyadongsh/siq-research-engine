"""Adapter for SEC HTML/iXBRL report packages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .base import (
    ADAPTER_CONTRACT_VERSION,
    AdapterContext,
    SourceAdapterError,
    artifact_path,
    list_payload,
    normalize_evidence_ref,
    normalize_fact,
    read_json,
    read_text,
)


class SecIxbrlAdapter:
    source_family = "sec_ixbrl"
    adapter_name = "sec_ixbrl"
    adapter_version = "1.0.0"

    def build(self, context: AdapterContext) -> dict[str, Any]:
        target = context.research_target
        identity = target["research_identity"]
        report = target["source_report"]
        form_type = str(report.get("form_type") or context.manifest.get("form") or "").upper()
        if form_type not in {"10-K", "10-Q", "10-K/A", "10-Q/A"}:
            raise SourceAdapterError(
                "source_adapter_unavailable",
                "SEC adapter currently supports 10-K and 10-Q report packages",
                details={"form_type": form_type},
            )

        document_path = artifact_path(
            context,
            manifest_keys=("document_full",),
            fallbacks=("parser/document_full.json",),
            required=True,
        )
        report_markdown_path = artifact_path(
            context,
            manifest_keys=("wiki_report_complete", "report_complete"),
            fallbacks=("sections/report_complete.md", "parser/report_complete.md"),
            required=True,
        )
        financial_data_path = artifact_path(
            context,
            manifest_keys=("financial_data",),
            fallbacks=("metrics/financial_data.json",),
            required=True,
        )
        normalized_metrics_path = artifact_path(
            context,
            manifest_keys=("normalized_metrics",),
            fallbacks=("metrics/normalized_metrics.json",),
            required=True,
        )
        financial_checks_path = artifact_path(
            context,
            manifest_keys=("financial_checks",),
            fallbacks=("metrics/financial_checks.json",),
            required=True,
        )
        source_map_path = artifact_path(
            context,
            manifest_keys=("source_map",),
            fallbacks=("qa/source_map.json",),
            required=True,
        )
        sections_path = artifact_path(context, manifest_keys=("sections",), fallbacks=("sections.json",))
        table_index_path = artifact_path(
            context,
            manifest_keys=("table_index",),
            fallbacks=("tables/table_index.json",),
        )
        xbrl_facts_path = artifact_path(
            context,
            manifest_keys=("xbrl_facts_raw",),
            fallbacks=("xbrl/facts_raw.json",),
            required=True,
        )
        contexts_path = artifact_path(
            context,
            manifest_keys=("xbrl_contexts",),
            fallbacks=("xbrl/contexts.json",),
            required=True,
        )
        units_path = artifact_path(
            context,
            manifest_keys=("xbrl_units",),
            fallbacks=("xbrl/units.json",),
            required=True,
        )
        labels_path = artifact_path(
            context,
            manifest_keys=("xbrl_labels",),
            fallbacks=("xbrl/labels.json",),
        )

        document_payload = read_json(document_path, required=True)
        report_markdown = read_text(report_markdown_path, required=True)
        metrics_payload = read_json(normalized_metrics_path, required=True)
        raw_metrics = list_payload(metrics_payload, "metrics", "facts", "data")
        source_map = read_json(source_map_path, required=True)
        raw_evidence = [
            {**item, "section_role": _evidence_section_role(item)}
            for item in list_payload(source_map, "entries", "evidence", "items")
        ]
        xbrl_payload = read_json(xbrl_facts_path, required=True)
        raw_xbrl_facts = list_payload(xbrl_payload, "facts", "items")
        contexts_payload = read_json(contexts_path, required=True)
        contexts_by_id = _mapping_payload(contexts_payload, "contexts")
        units_payload = read_json(units_path, required=True)
        units_by_id = _mapping_payload(units_payload, "units")
        xbrl_by_id = {
            str(item.get("fact_id") or ""): item
            for item in raw_xbrl_facts
            if str(item.get("fact_id") or "")
        }

        evidence_refs = [
            normalize_evidence_ref(
                item,
                identity=identity,
                report_id=str(report["report_id"]),
                source_family=self.source_family,
                default_kind="sec_html_section",
            )
            for item in raw_evidence
        ]
        evidence_ids = {item["evidence_id"] for item in evidence_refs}
        evidence_by_raw_fact: dict[str, list[dict[str, Any]]] = {}
        for raw, normalized in zip(raw_evidence, evidence_refs):
            nested = raw.get("raw") if isinstance(raw.get("raw"), Mapping) else {}
            fact_id = str(raw.get("fact_id") or nested.get("fact_id") or "")
            if fact_id:
                evidence_by_raw_fact.setdefault(fact_id, []).append(normalized)

        facts = []
        for raw_metric in raw_metrics:
            raw = dict(raw_metric)
            raw_details = raw.get("raw") if isinstance(raw.get("raw"), Mapping) else {}
            raw_fact_id = str(raw.get("raw_fact_id") or raw_details.get("fact_id") or "")
            xbrl_fact = xbrl_by_id.get(raw_fact_id, {})
            concept = str(raw.get("concept") or xbrl_fact.get("concept") or "")
            raw["concept"] = concept
            context_ref = str(
                raw.get("context_ref")
                or raw_details.get("context_id")
                or xbrl_fact.get("context_ref")
                or ""
            )
            xbrl_context = contexts_by_id.get(context_ref, {})
            if context_ref and not xbrl_context:
                raise SourceAdapterError(
                    "xbrl_context_missing",
                    "normalized SEC metric references an unavailable XBRL context",
                    details={"metric_id": raw.get("metric_id"), "context_ref": context_ref},
                )
            _enrich_metric_context(
                raw,
                xbrl_fact=xbrl_fact,
                context=xbrl_context,
                context_ref=context_ref,
                units_by_id=units_by_id,
                form_type=form_type,
            )
            raw["accounting_basis"] = _accounting_basis(raw, concept, xbrl_fact)
            refs = list(evidence_by_raw_fact.get(raw_fact_id, ()))
            if xbrl_fact and not refs:
                xbrl_ref = normalize_evidence_ref(
                    {
                        **xbrl_fact,
                        "source_type": "sec_xbrl_fact",
                        "xbrl_tag": concept,
                        "source_url": context.manifest.get("source_url"),
                        "quote_text": xbrl_fact.get("value_text"),
                        "unit": raw.get("unit") or xbrl_fact.get("unit"),
                    },
                    identity=identity,
                    report_id=str(report["report_id"]),
                    source_family=self.source_family,
                    default_kind="sec_xbrl_fact",
                )
                if xbrl_ref["evidence_id"] not in evidence_ids:
                    evidence_refs.append(xbrl_ref)
                    evidence_ids.add(xbrl_ref["evidence_id"])
                refs.append(xbrl_ref)
            facts.append(
                normalize_fact(
                    raw,
                    identity=identity,
                    report=report,
                    evidence_refs=refs,
                    source_family=self.source_family,
                )
            )
        _validate_context_disambiguation(facts)

        section_files = sorted(
            str(path)
            for path in (context.report_dir / "sections").glob("*.md")
            if path.is_file() and not path.is_symlink()
        )
        section_catalog = _section_catalog([Path(item) for item in section_files])
        existing_section_sources = {
            (str(item.get("section_role") or ""), str(item.get("local_source_id") or ""))
            for item in evidence_refs
        }
        for section in section_catalog:
            role = str(section.get("role") or "")
            if role == "other":
                continue
            local_source_id = f"sections/{section['file']}"
            if (role, local_source_id) in existing_section_sources:
                continue
            evidence_refs.append(
                normalize_evidence_ref(
                    {
                        "source_type": "sec_html_section",
                        "section_id": role,
                        "section_role": role,
                        "local_path": local_source_id,
                        "source_url": context.manifest.get("source_url"),
                        "quote_text": section.get("excerpt"),
                    },
                    identity=identity,
                    report_id=str(report["report_id"]),
                    source_family=self.source_family,
                    default_kind="sec_html_section",
                )
            )
        quality_status = str(report.get("quality_status") or context.manifest.get("quality_status") or "unknown")
        return {
            "contract_version": ADAPTER_CONTRACT_VERSION,
            "adapter": {
                "name": self.adapter_name,
                "version": self.adapter_version,
                "source_family": self.source_family,
            },
            "inputs": _paths(
                report_markdown=report_markdown_path,
                document_full=document_path,
                financial_data=financial_data_path,
                normalized_metrics=normalized_metrics_path,
                financial_checks=financial_checks_path,
                source_map=source_map_path,
                sections=sections_path,
                table_index=table_index_path,
                xbrl_facts=xbrl_facts_path,
                xbrl_contexts=contexts_path,
                xbrl_units=units_path,
                xbrl_labels=labels_path,
            ),
            "section_files": section_files,
            "section_catalog": section_catalog,
            "document_summary": {
                "top_level_keys": sorted(str(key) for key in document_payload) if isinstance(document_payload, Mapping) else [],
                "markdown_char_count": len(report_markdown),
                "markdown_headings": _markdown_headings(report_markdown),
                "table_count": _payload_count(read_json(table_index_path), "tables") if table_index_path else 0,
            },
            "normalized_facts": facts,
            "evidence_refs": evidence_refs,
            "financial_data": read_json(financial_data_path, required=True),
            "financial_checks": read_json(financial_checks_path, required=True),
            "xbrl_summary": {
                "fact_count": len(raw_xbrl_facts),
                "context_count": len(contexts_by_id),
                "unit_count": len(units_by_id),
                "label_count": _payload_count(read_json(labels_path), "labels") if labels_path else 0,
                "period_basis_counts": _period_basis_counts(facts),
                "disambiguated_context_count": len(
                    {str(item.get("context_signature") or "") for item in facts if item.get("context_signature")}
                ),
            },
            "filing": {
                "form_type": form_type,
                "accession_number": context.manifest.get("accession_number"),
                "filing_date": context.manifest.get("filing_date"),
                "accepted_at": context.manifest.get("accepted_at"),
                "source_url": context.manifest.get("source_url"),
                "accounting_standard": context.manifest.get("accounting_standard") or "US_GAAP",
                "fiscal_year": report.get("fiscal_year") or context.manifest.get("fiscal_year"),
                "fiscal_period": report.get("fiscal_period") or context.manifest.get("fiscal_period"),
                "period_end": report.get("period_end") or context.manifest.get("period_end"),
            },
            "capabilities": {
                "fulltext": True,
                "structured_metrics": bool(facts),
                "evidence": bool(evidence_refs),
                "sec_sections": bool(section_files or sections_path),
                "xbrl": bool(raw_xbrl_facts),
                "peer_metrics": False,
                "market_snapshot": False,
            },
            "quality_status": quality_status,
            "degraded_reasons": ["source_quality_warning"] if quality_status == "warning" else [],
        }


def _accounting_basis(raw_metric: Mapping[str, Any], concept: str, raw_fact: Mapping[str, Any]) -> str:
    explicit = str(raw_metric.get("accounting_basis") or "").strip().lower().replace("-", "_")
    if explicit in {"gaap", "non_gaap", "regulatory", "company_extension"}:
        return explicit
    taxonomy = str(raw_fact.get("taxonomy") or concept.partition(":")[0]).lower()
    if taxonomy == "us-gaap" and not bool(raw_fact.get("is_extension")):
        return "gaap"
    if taxonomy == "dei":
        return "regulatory"
    non_gaap_flag = raw_metric.get("is_non_gaap") is True or raw_fact.get("is_non_gaap") is True
    descriptive_text = " ".join(
        str(value or "").lower()
        for value in (
            raw_metric.get("label"),
            raw_metric.get("metric_name"),
            raw_metric.get("canonical_name"),
            concept,
        )
    )
    if non_gaap_flag or any(token in descriptive_text for token in ("non-gaap", "non gaap", "adjusted ebitda", "adjusted earnings")):
        return "non_gaap"
    return "company_extension"


def _mapping_payload(payload: Any, key: str) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        values = payload.get(key)
        if isinstance(values, Mapping):
            return {str(item_key): item for item_key, item in values.items()}
        if isinstance(values, list):
            output: dict[str, Any] = {}
            for item in values:
                if not isinstance(item, Mapping):
                    continue
                identifier = str(item.get("context_ref") or item.get("unit_ref") or item.get("id") or "")
                if identifier:
                    output[identifier] = dict(item)
            return output
    return {}


def _enrich_metric_context(
    raw: dict[str, Any],
    *,
    xbrl_fact: Mapping[str, Any],
    context: Mapping[str, Any],
    context_ref: str,
    units_by_id: Mapping[str, Any],
    form_type: str,
) -> None:
    fact_context_ref = str(xbrl_fact.get("context_ref") or "")
    if fact_context_ref and context_ref and fact_context_ref != context_ref:
        raise SourceAdapterError(
            "xbrl_context_mismatch",
            "normalized SEC metric and raw XBRL fact use different contexts",
            details={"metric_id": raw.get("metric_id")},
        )
    expected_start = str(context.get("period_start") or xbrl_fact.get("period_start") or "")
    expected_end = str(
        context.get("instant")
        or context.get("period_end")
        or xbrl_fact.get("instant")
        or xbrl_fact.get("period_end")
        or ""
    )
    for field, expected in (("period_start", expected_start), ("period_end", expected_end)):
        actual = str(raw.get(field) or "")
        if actual and expected and actual != expected:
            raise SourceAdapterError(
                "xbrl_context_mismatch",
                f"normalized SEC metric {field} conflicts with its XBRL context",
                details={"metric_id": raw.get("metric_id"), "context_ref": context_ref},
            )
        if not actual and expected:
            raw[field] = expected
    context_dimensions = context.get("dimensions") if isinstance(context.get("dimensions"), Mapping) else {}
    fact_dimensions = xbrl_fact.get("dimensions") if isinstance(xbrl_fact.get("dimensions"), Mapping) else {}
    metric_dimensions = raw.get("dimensions") if isinstance(raw.get("dimensions"), Mapping) else {}
    if metric_dimensions and context_dimensions and dict(metric_dimensions) != dict(context_dimensions):
        raise SourceAdapterError(
            "xbrl_context_mismatch",
            "normalized SEC metric dimensions conflict with its XBRL context",
            details={"metric_id": raw.get("metric_id"), "context_ref": context_ref},
        )
    dimensions = dict(metric_dimensions or fact_dimensions or context_dimensions)
    duration_days = raw.get("duration_days")
    if duration_days is None:
        duration_days = context.get("duration_days") or xbrl_fact.get("duration_days")
    period_basis = _classify_period_basis(
        form_type=form_type,
        explicit=raw.get("qtd_ytd_type"),
        period_start=raw.get("period_start"),
        period_end=raw.get("period_end"),
        duration_days=duration_days,
        instant=context.get("instant") or xbrl_fact.get("instant"),
    )
    raw_details = raw.get("raw") if isinstance(raw.get("raw"), Mapping) else {}
    fact_details = xbrl_fact.get("raw") if isinstance(xbrl_fact.get("raw"), Mapping) else {}
    unit_ref = str(
        raw.get("unit_ref")
        or raw_details.get("unit_ref")
        or fact_details.get("unitRef")
        or xbrl_fact.get("unit_ref")
        or ""
    )
    raw["context_ref"] = context_ref
    raw["dimensions"] = dimensions
    raw["duration_days"] = duration_days
    raw["qtd_ytd_type"] = period_basis
    raw["unit_ref"] = unit_ref or None
    raw["xbrl_unit_definition"] = units_by_id.get(unit_ref) if unit_ref else None
    signature_payload = {
        "concept": raw.get("concept"),
        "context_ref": context_ref,
        "period_start": raw.get("period_start"),
        "period_end": raw.get("period_end"),
        "period_basis": period_basis,
        "unit_ref": unit_ref,
        "dimensions": dimensions,
    }
    raw["context_signature"] = hashlib.sha256(
        json.dumps(signature_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _classify_period_basis(
    *,
    form_type: str,
    explicit: Any,
    period_start: Any,
    period_end: Any,
    duration_days: Any,
    instant: Any,
) -> str:
    explicit_value = str(explicit or "").strip().lower().replace("-", "_")
    aliases = {"quarter": "qtd", "quarter_to_date": "qtd", "year_to_date": "ytd", "annual": "fy"}
    explicit_value = aliases.get(explicit_value, explicit_value)
    if explicit_value in {"instant", "qtd", "ytd", "fy"}:
        return explicit_value
    if instant or not period_start:
        return "instant"
    try:
        days = int(duration_days)
    except (TypeError, ValueError):
        days = 0
    if form_type.startswith("10-Q"):
        return "qtd" if 0 < days <= 120 else "ytd"
    if form_type.startswith("10-K") and days >= 300:
        return "fy"
    if 0 < days <= 120:
        return "qtd"
    return "duration"


def _validate_context_disambiguation(facts: list[dict[str, Any]]) -> None:
    values_by_signature: dict[tuple[str, str], Any] = {}
    for fact in facts:
        concept = str(fact.get("concept") or "")
        if not concept:
            continue
        context_ref = str(fact.get("context_ref") or "")
        signature = str(fact.get("context_signature") or "")
        if not context_ref or not signature:
            raise SourceAdapterError(
                "xbrl_context_ambiguous",
                "SEC normalized metric lacks an unambiguous XBRL context signature",
                details={"metric_key": fact.get("metric_key"), "concept": concept},
            )
        key = (concept, signature)
        value = fact.get("normalized_value")
        if key in values_by_signature and values_by_signature[key] != value:
            raise SourceAdapterError(
                "xbrl_context_ambiguous",
                "the same SEC concept/context resolves to conflicting values",
                details={"concept": concept, "context_ref": context_ref},
            )
        values_by_signature[key] = value


def _period_basis_counts(facts: list[dict[str, Any]]) -> dict[str, int]:
    output: dict[str, int] = {}
    for fact in facts:
        key = str(fact.get("qtd_ytd_type") or "unknown")
        output[key] = output.get(key, 0) + 1
    return output


def _evidence_section_role(item: Mapping[str, Any]) -> str:
    nested = item.get("raw") if isinstance(item.get("raw"), Mapping) else {}
    text = " ".join(
        str(value or "").lower()
        for value in (
            item.get("section_id"),
            item.get("local_path"),
            item.get("target"),
            nested.get("file"),
            nested.get("section_title"),
        )
    )
    for role, tokens in (
        ("market_risk", ("item_7a", "item 7a", "market_risk", "market risk")),
        ("risk_factors", ("item_1a", "item 1a", "risk_factors", "risk factors")),
        ("controls", ("item_9a", "item 9a", "controls", "control")),
        ("mda", ("item_7", "item 7", "/mda", "mda.md", "management's discussion", "management’s discussion")),
        ("financial_statements", ("item_8", "item 8", "financial_statements")),
        ("notes", ("/notes", "notes.md", "section_id notes")),
        ("segments", ("segment",)),
        ("business", ("item_1", "item 1", "/business", "business.md")),
    ):
        if any(token in text for token in tokens):
            return role
    return ""


def _payload_count(payload: Any, key: str) -> int:
    if isinstance(payload, Mapping):
        value = payload.get(key)
        if isinstance(value, (list, Mapping)):
            return len(value)
    if isinstance(payload, list):
        return len(payload)
    return 0


def _paths(**values: Path | None) -> dict[str, str]:
    return {key: str(value) for key, value in values.items() if value is not None}


def _section_catalog(paths: list[Path]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for path in paths:
        text = read_text(path)
        body = _strip_front_matter(text)
        compact = " ".join(line.strip("# ") for line in body.splitlines() if line.strip())
        output.append(
            {
                "role": _section_role(path.name, compact[:500]),
                "file": path.name,
                "heading": next((line.lstrip("# ").strip() for line in body.splitlines() if line.startswith("#")), path.stem),
                "excerpt": compact[:1200],
                "char_count": len(text),
            }
        )
    return output


def _strip_front_matter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :])
    return text


def _section_role(filename: str, preview: str) -> str:
    text = f"{filename} {preview}".lower()
    for role, terms in (
        ("market_risk", ("market_risk", "item 7a", "quantitative and qualitative disclosures about market risk")),
        ("risk_factors", ("risk", "item 1a")),
        ("mda", ("mda", "management's discussion", "management’s discussion", "item 7")),
        ("controls", ("control", "item 9a")),
        ("business", ("business", "item 1")),
        ("notes", ("notes", "financial statements")),
        ("segments", ("segment",)),
    ):
        if any(term in text for term in terms):
            return role
    return "other"


def _markdown_headings(text: str, *, limit: int = 60) -> list[str]:
    headings = [line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")]
    return [item for item in headings if item][:limit]
