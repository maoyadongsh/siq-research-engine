#!/usr/bin/env python3
"""Generate independent synthetic inputs for the five PMIC golden candidates.

The generated packages are inputs, never accepted golden results. They contain
no Hermes output, real-smoke report, factcheck result, or human attestation.
Actual behavior must be produced by the real smoke runner and recomputed by the
offline golden evaluator.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
BASE_GENERATOR = ROOT / "generate_evidence_complete_fixture.py"
BASE_DEAL_ID = "DEAL-PMIC-POSITIVE-COND-2026"
CREATED_AT = "2026-07-14T00:00:00Z"
SUITE_MANIFEST_PATH = ROOT / "golden_suite_manifest.json"

SUITE_CASES: tuple[dict[str, str], ...] = (
    {
        "case_id": "GOLDEN-PMIC-CONDITIONAL-SUPPORT",
        "fixture": BASE_DEAL_ID,
        "deal_id": BASE_DEAL_ID,
        "execution": "R0-R4 real Hermes run, report quality and factcheck, then trusted human confirmation",
        "expected_outcome": "conditional_support",
    },
    {
        "case_id": "GOLDEN-PMIC-MATERIAL-RISK",
        "fixture": "DEAL-PMIC-MATERIAL-RISK-2026",
        "deal_id": "DEAL-PMIC-MATERIAL-RISK-2026",
        "execution": "R0-R4 real Hermes run",
        "expected_outcome": "review_or_reject_after_material_legal_or_financial_risk",
    },
    {
        "case_id": "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE",
        "fixture": "DEAL-PMIC-INSUFFICIENT-2026",
        "deal_id": "DEAL-PMIC-INSUFFICIENT-2026",
        "execution": "R0-R4 real Hermes run; preserve explicit missing/assumed claims",
        "expected_outcome": "insufficient_evidence",
    },
    {
        "case_id": "GOLDEN-PMIC-FULL-R3",
        "fixture": "DEAL-PMIC-FULL-R3-2026",
        "deal_id": "DEAL-PMIC-FULL-R3-2026",
        "execution": "R0-R4 real Hermes run with a preserved high-materiality tradeoff through R2",
        "expected_outcome": "full_red_blue_debate",
    },
    {
        "case_id": "GOLDEN-PMIC-SNAPSHOT-STALE",
        "fixture": "DEAL-PMIC-SNAPSHOT-STALE-2026",
        "deal_id": "DEAL-PMIC-SNAPSHOT-STALE-2026",
        "execution": "Complete and human-confirm the initial run, then apply the committed stale-update source and refresh the snapshot",
        "expected_outcome": "receipts_stale_and_decision_review_required",
    },
)


SCENARIOS: dict[str, dict[str, Any]] = {
    "GOLDEN-PMIC-MATERIAL-RISK": {
        "deal_id": "DEAL-PMIC-MATERIAL-RISK-2026",
        "document_id": "DOC-PMICMATRISK2026A1",
        "parse_run_id": "PRUN-20260714-PMICMATRISK001",
        "evidence_prefix": "EVID-PMIC-MRISK",
        "company_name": "衡界（纯合成）",
        "company_alias": "衡界",
        "fixture_id": "PMIC-GOLDEN-MATERIAL-RISK-001",
        "label": "material_legal_and_financial_risk",
        "expected_semantics": {
            "r0": "ready_or_needs_more_evidence_as_model_determines",
            "r1": "legal_or_finance_report_identifies_a_material_blocker",
            "r1_5": "material_risk_is_explicitly_adjudicated",
            "r2": "experts_preserve_the_verified_adverse_facts",
            "r3": "full_debate_required_if_the_material_blocker_remains_open",
            "r4": "review_or_reject",
        },
        "critical_fact_status": "complete",
        "critical_gaps": [],
        "overrides": {
            "LEG-003": {
                "claim": "核心生产线的排污许可已经到期，监管机关要求续证前停止相关工序。",
                "quote": (
                    "合成监管回函确认核心湿法工序排污许可已于2026-05-31到期；公司于到期后才提交续证，"
                    "监管机关书面要求续证前停止该工序。续证仍取决于新增治理设备验收，预计至少需要120天，"
                    "现有材料不存在临时许可或豁免。"
                ),
                "verification": "合成许可台账、续证受理回执和监管停工通知逐项核验",
            },
            "LEG-004": {
                "claim": "外部FTO意见识别出覆盖核心光路的有效阻断性专利，当前没有可验证设计绕开。",
                "quote": (
                    "外部合成知识产权律师将竞争对手A的有效专利权利要求逐项映射至QH-5000核心双光路，"
                    "确认全部必要技术特征均被覆盖，并将禁令风险评为高。公司未提交无效检索意见、许可要约"
                    "或完成验证的设计绕开方案，书面结论为量产和销售存在实质阻断风险。"
                ),
                "verification": "合成权利要求对照表、律师FTO意见与研发设计评审记录交叉验证",
            },
            "LEG-010": {
                "claim": "交易文件把许可恢复和专利风险关闭列为不可豁免交割条件，当前尚未满足。",
                "quote": (
                    "合成条款清单明确将排污许可恢复、监管停工解除以及核心专利许可或无侵权法律意见列为"
                    "不可由投资人单方豁免的交割条件；截至评测基准日三项均未满足。其他估值、治理和信息权"
                    "条款已成文，但不能替代上述法定和知识产权条件。"
                ),
                "verification": "合成条款清单、董事会决议和交割条件清单逐项勾稽",
            },
            "RSK-010": {
                "claim": "许可停工和专利禁令的组合情景构成投决否决项，现有缓释不足以支持交割。",
                "quote": (
                    "组合压力测试假设核心工序停工120天并发生专利临时禁令，2026年收入降至520百万元、"
                    "净亏损38百万元、期末现金低于最低运营现金80百万元；已确认订单无法全部外协履行。"
                    "风险委员会阈值要求在许可恢复且专利风险关闭前维持否决，估值下调不能消除该阻断项。"
                ),
                "verification": "合成停工情景、订单交付能力、现金压力模型和风险阈值重算",
            },
        },
    },
    "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE": {
        "deal_id": "DEAL-PMIC-INSUFFICIENT-2026",
        "document_id": "DOC-PMICINSUFF2026A1",
        "parse_run_id": "PRUN-20260714-PMICINSUFF001",
        "evidence_prefix": "EVID-PMIC-INSUF",
        "company_name": "未衡（纯合成）",
        "company_alias": "未衡",
        "fixture_id": "PMIC-GOLDEN-INSUFFICIENT-001",
        "label": "verified_material_omissions",
        "expected_semantics": {
            "r0": "ready_or_needs_more_evidence_as_model_determines",
            "r1": "material_claims_are_restricted_instead_of_invented",
            "r1_5": "missing_facts_remain_explicit",
            "r2": "no_missing_fact_is_silently_upgraded",
            "r3": "debate_cannot_substitute_for_missing_project_evidence",
            "r4": "insufficient_evidence_if_the_workflow_reaches_a_decision",
        },
        "critical_fact_status": "incomplete",
        "critical_gaps": [
            "customer_and_order_confirmations_missing",
            "audited_financial_statements_missing",
            "freedom_to_operate_opinion_missing",
        ],
        "overrides": {
            "BUS-007": {
                "claim": "客户集中度和复购率仅来自管理层清单，未取得客户级外部确认。",
                "quote": (
                    "数据室仅提供管理层编制的18家客户名称和汇总收入，未提供客户合同、验收单、回款记录"
                    "或第三方函证；所谓92%复购率无法还原到客户和订单。该缺口已经数据室管理员书面确认。"
                ),
                "verification": "合成数据室目录、缺件清单与管理员书面确认交叉核对",
            },
            "BUS-008": {
                "claim": "在手订单金额没有合同、定金或客户确认支持，不能作为已验证订单。",
                "quote": (
                    "管理层演示稿声称在手订单620百万元，但数据室未提供订单合同、客户确认、交付排期或"
                    "定金流水；180百万元框架意向与所谓不可撤销订单无法区分。材料管理员确认原始订单包未上传。"
                ),
                "verification": "合成数据室订单目录、银行流水目录和缺件回函核验",
            },
            "FIN-001": {
                "claim": "数据室没有审计报告和完整总账，三年报表主体与合并范围无法验证。",
                "quote": (
                    "仅有管理层导出的三张汇总报表截图，未提供审计报告、总账、科目余额表、合并抵销或"
                    "子公司明细；截图中的主体名称不一致。会计师未授权引用，数据室管理员确认审计底稿缺失。"
                ),
                "verification": "合成财务目录、文件元数据和会计师授权缺失记录核验",
            },
            "LEG-004": {
                "claim": "核心产品没有可引用的FTO检索或法律意见，侵权风险无法判断。",
                "quote": (
                    "数据室知识产权目录只有公司自行制作的专利数量表，未提供权利要求检索、竞品专利映射、"
                    "外部律师意见或设计绕开验证；管理员确认不存在可供本轮尽调引用的FTO文件。"
                ),
                "verification": "合成知识产权目录、文件哈希清单和管理员缺件确认核验",
            },
            "RSK-010": {
                "claim": "订单、审计财务和FTO三项关键输入缺失，无法形成可信综合下行情景。",
                "quote": (
                    "风险模型所需的客户级订单、经审计现金流和核心专利可实施性均无项目证据；任何收入、"
                    "现金底线、估值回报或禁令损失数字都将依赖未经验证的管理层假设。缺口关闭前只能给出"
                    "材料不足结论，不能用行业经验补值。"
                ),
                "verification": "合成风险输入清单与三类缺件记录逐项映射",
            },
        },
    },
    "GOLDEN-PMIC-FULL-R3": {
        "deal_id": "DEAL-PMIC-FULL-R3-2026",
        "document_id": "DOC-PMICFULLR32026A1",
        "parse_run_id": "PRUN-20260714-PMICFULLR3001",
        "evidence_prefix": "EVID-PMIC-FULLR3",
        "company_name": "辩衡（纯合成）",
        "company_alias": "辩衡",
        "fixture_id": "PMIC-GOLDEN-FULL-R3-001",
        "label": "evidence_complete_high_material_conflict",
        "expected_semantics": {
            "r0": "ready",
            "r1": "opposing_recommendations_on_capacity_and_valuation",
            "r1_5": "facts_are_closed_but_the_decision_tradeoff_is_preserved",
            "r2": "material_opposition_or_red_flag_remains_explicit",
            "r3": "full_four_turn_red_blue_debate_and_chairman_verdict",
            "r4": "decision_binds_the_debate_verdict",
        },
        "critical_fact_status": "complete",
        "critical_gaps": [],
        "overrides": {
            "BUS-008": {
                "claim": "已确认订单支持扩产，但订单期限和客户集中度使释放节奏存在实质争议。",
                "quote": (
                    "客户函证确认不可撤销订单620百万元，其中420百万元来自两家客户并要求九个月内交付；"
                    "延期超过60天可触发15%违约金。现有产能无法按期完成全部交付，但一次性扩产会在订单"
                    "峰值后形成闲置，事实完整且两种决策后果均可量化。"
                ),
                "verification": "合成客户函证、交付排程、违约条款和产能模型联合核验",
            },
            "FIN-010": {
                "claim": "基准回报支持投资，而订单峰值后的下行回报不足，估值与扩产必须共同裁定。",
                "quote": (
                    "拟投240百万元、投前估值3,600百万元时，基准退出MOIC为2.11倍；若两家集中客户在"
                    "订单峰值后不续约，2029退出估值4,200百万元，对应MOIC仅0.98倍。将投前估值下调至"
                    "3,100百万元可把该下行MOIC提高至1.13倍，但仍不能单独解决产能闲置。"
                ),
                "verification": "合成资本结构、退出估值和客户续约敏感性逐项重算",
            },
            "RSK-004": {
                "claim": "扩产是履约所需但一次性投入造成高闲置风险，支持与反对立场均由同一证据集约束。",
                "quote": (
                    "不扩产时九个月交付缺口为74台并触发最高63百万元违约金；一次性新增180台年产能后，"
                    "若两家集中客户不续约，2028利用率降至54%。分两笔扩产可把履约缺口降至12台，并把"
                    "下行利用率维持在68%，但第二笔释放时点会影响客户交付承诺。"
                ),
                "verification": "合成逐月交付、违约金、产能爬坡和客户续约情景联合重算",
            },
        },
    },
    "GOLDEN-PMIC-SNAPSHOT-STALE": {
        "deal_id": "DEAL-PMIC-SNAPSHOT-STALE-2026",
        "document_id": "DOC-PMICSTALE2026A1",
        "parse_run_id": "PRUN-20260714-PMICSTALE001",
        "evidence_prefix": "EVID-PMIC-STALE",
        "company_name": "更衡（纯合成）",
        "company_alias": "更衡",
        "fixture_id": "PMIC-GOLDEN-SNAPSHOT-STALE-001",
        "label": "confirmed_decision_invalidated_by_new_source",
        "expected_semantics": {
            "initial_run": "complete_R0_R4_and_obtain_a_trusted_human_confirmation",
            "source_activation": "activate_the_committed_synthetic_update_source",
            "snapshot_change": "current_snapshot_differs_from_the_confirmed_decision_snapshot",
            "receipt_state": "prior_startup_receipts_are_stale",
            "workflow_state": "decision_review_required",
        },
        "critical_fact_status": "complete",
        "critical_gaps": [],
        "overrides": {},
        "stale_update": True,
    },
}


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _sha256(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _input_identity(files: Mapping[str, str]) -> dict[str, Any]:
    aggregate = hashlib.sha256()
    for relative in sorted(files):
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(_sha256(files[relative]).encode("ascii"))
        aggregate.update(b"\n")
    snapshot = _json_file(files, "evidence/evidence_snapshot.json")
    return {
        "input_bundle_sha256": aggregate.hexdigest(),
        "fixture_contract_sha256": _sha256(files["fixture_contract.json"]),
        "evidence_snapshot_hash": snapshot.get("snapshot_hash"),
        "file_count": len(files),
    }


def build_suite_manifest(
    scenario_files: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    rendered = dict(scenario_files or {})
    for case_id in SCENARIOS:
        if case_id not in rendered:
            _, rendered[case_id] = build_scenario(case_id)
    base = _load_base_generator()
    base_files = base.build_files()
    base._validate_rendered_files(base_files)
    identities = {
        "GOLDEN-PMIC-CONDITIONAL-SUPPORT": _input_identity(base_files),
        **{case_id: _input_identity(files) for case_id, files in rendered.items()},
    }
    return {
        "schema_version": "siq_primary_market_ic_golden_input_suite_v1",
        "suite_id": "PMIC-GOLDEN-INPUTS-2026-07-14",
        "synthetic_evaluation_only": True,
        "input_only": True,
        "quality_accepted": False,
        "rules": [
            "Fixtures are independent input packages, not expected model outputs.",
            "No fixture contains a real-smoke report, factcheck result, human confirmation, or golden evaluation result.",
            "A scenario passes only when its isolated real run satisfies the repository golden evaluator.",
            "Trusted human confirmation and methodology approval must be performed outside fixture generation.",
        ],
        "regression_lanes": [
            {
                "lane_id": "PMIC-R3-SAFE-SKIP",
                "kind": "deterministic_contract_regression",
                "expected": "skip only when every persisted safety check is true",
                "release_binding": False,
            },
            {
                "lane_id": "PMIC-FACTCHECK-REPAIR",
                "kind": "model_assisted_regression",
                "expected": "blocked original report plus a new audited revision that passes revalidation",
                "release_binding": False,
            },
            {
                "lane_id": "PMIC-TAMPER-NEGATIVE",
                "kind": "fail_expected_adversarial_regression",
                "expected": "raw-output, handoff, task, or golden-result mutation is rejected by digest recomputation",
                "release_binding": False,
            },
        ],
        "cases": [
            {
                **row,
                "input_only": True,
                "quality_accepted": False,
                "input_status": "ready",
                "result_status": "not_run",
                "input_identity": identities[row["case_id"]],
            }
            for row in SUITE_CASES
        ],
    }


def _write_or_check_suite_manifest(
    payload: Mapping[str, Any],
    *,
    check: bool,
) -> None:
    expected = _json_text(payload)
    if check:
        try:
            actual = SUITE_MANIFEST_PATH.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise SystemExit("suite manifest check failed: missing") from exc
        if actual != expected:
            raise SystemExit("suite manifest check failed: changed")
        return
    SUITE_MANIFEST_PATH.write_text(expected, encoding="utf-8")


def _load_base_generator():
    spec = importlib.util.spec_from_file_location("pmic_positive_fixture_base", BASE_GENERATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base generator: {BASE_GENERATOR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _replace_company(value: Any, alias: str) -> Any:
    if isinstance(value, str):
        return value.replace("启衡", alias)
    if isinstance(value, list):
        return [_replace_company(item, alias) for item in value]
    if isinstance(value, dict):
        return {key: _replace_company(item, alias) for key, item in value.items()}
    return value


def _json_file(files: Mapping[str, str], relative: str) -> dict[str, Any]:
    payload = json.loads(files[relative])
    if not isinstance(payload, dict):
        raise AssertionError(f"expected object at {relative}")
    return payload


def _add_stale_update(files: dict[str, str], scenario: Mapping[str, Any]) -> None:
    deal_id = str(scenario["deal_id"])
    document_id = "DOC-PMICSTALE2026B1"
    parse_run_id = "PRUN-20260714-PMICSTALE002"
    source_id = f"PM:{deal_id}:{document_id}:{parse_run_id}"
    content_path = "scenario_inputs/stale_update/content_list_enhanced.json"
    archive_path = "scenario_inputs/stale_update/archive_manifest.json"
    content = _json_text(
        {
            "schema_version": "siq_synthetic_primary_market_source_v1",
            "deal_id": deal_id,
            "document_id": document_id,
            "parse_run_id": parse_run_id,
            "fixture_notice": "SYNTHETIC EVALUATION ONLY",
            "blocks": [
                {
                    "id": "pmic-stale-update-001",
                    "type": "text",
                    "page": 1,
                    "section": "新版申报材料",
                    "text": "新版合成申报材料已启用；此前报告和回执必须重新绑定新的 Evidence snapshot。",
                    "synthetic_evaluation_only": True,
                }
            ],
        }
    )
    archive = _json_text(
        {
            "schema_version": "siq_primary_market_parse_archive_v1",
            "deal_id": deal_id,
            "document_id": document_id,
            "parse_run_id": parse_run_id,
            "build_mode": "deterministic_synthetic_stale_update_v1",
            "created_at": CREATED_AT,
            "bundle_sha256": _sha256(content),
            "artifacts": [
                {
                    "path": "content_list_enhanced.json",
                    "sha256": _sha256(content),
                    "block_count": 1,
                }
            ],
        }
    )
    descriptor = {
        "schema_version": "siq_primary_market_ic_stale_update_v1",
        "deal_id": deal_id,
        "synthetic_evaluation_only": True,
        "requires_existing_human_confirmation": True,
        "source": {
            "schema_version": "siq_primary_market_analysis_source_v1",
            "source_id": source_id,
            "domain": "primary_market",
            "source_type": "primary_market_prospectus",
            "deal_id": deal_id,
            "market": "CN",
            "company_id": f"PRIMARY:{deal_id}",
            "filing_id": f"SYNTHETIC-PROSPECTUS-UPDATE:{document_id}",
            "document_id": document_id,
            "parse_run_id": parse_run_id,
            "artifact_manifest_path": archive_path,
            "archive_manifest_sha256": _sha256(archive),
            "status": "ready",
            "capabilities": {
                "text_evidence": "ready",
                "source_page_trace": "ready",
                "financial_facts": "ready",
                "semantic_index": "ready",
            },
            "quality_status": "pass",
            "synthetic_evaluation_only": True,
            "activated_at": CREATED_AT,
        },
    }
    files[content_path] = content
    files[archive_path] = archive
    files["scenario_inputs/stale_update.json"] = _json_text(descriptor)


def build_scenario(case_id: str) -> tuple[Any, dict[str, str]]:
    scenario = SCENARIOS[case_id]
    base = _load_base_generator()
    specs = _replace_company(copy.deepcopy(base.EVIDENCE_SPECS), str(scenario["company_alias"]))
    by_code = {item["code"]: item for item in specs}
    for code, updates in scenario["overrides"].items():
        by_code[code].update(updates)
    base.EVIDENCE_SPECS = specs
    base.DEAL_ID = scenario["deal_id"]
    base.TARGET = ROOT / scenario["deal_id"]
    base.DOCUMENT_ID = scenario["document_id"]
    base.PARSE_RUN_ID = scenario["parse_run_id"]
    base.SOURCE_ID = f"PM:{base.DEAL_ID}:{base.DOCUMENT_ID}:{base.PARSE_RUN_ID}"
    base.COMPANY_NAME = scenario["company_name"]
    base.SOURCE_PATH = f"parsed_documents/{base.DOCUMENT_ID}/runs/{base.PARSE_RUN_ID}/content_list_enhanced.json"
    base.ARCHIVE_PATH = f"parsed_documents/{base.DOCUMENT_ID}/runs/{base.PARSE_RUN_ID}/archive_manifest.json"
    evidence_prefix = str(scenario["evidence_prefix"])
    base._evidence_id = lambda code: f"{evidence_prefix}-{code}"
    files = base.build_files()

    contract = _json_file(files, "fixture_contract.json")
    contract.update(
        {
            "fixture_id": scenario["fixture_id"],
            "deal_id": scenario["deal_id"],
            "label": scenario["label"],
            "golden_case_id": case_id,
            "expected_semantics": scenario["expected_semantics"],
            "quality_accepted": False,
            "input_only": True,
        }
    )
    contract["critical_fact_completeness"].update(
        {
            "status": scenario["critical_fact_status"],
            "missing_critical_facts": scenario["critical_gaps"],
            "open_questions": scenario["critical_gaps"],
        }
    )
    files["fixture_contract.json"] = _json_text(contract)

    manifest = _json_file(files, "manifest.json")
    manifest.update(
        {
            "fixture_label": scenario["label"],
            "golden_case_id": case_id,
            "quality_accepted": False,
            "input_only": True,
        }
    )
    files["manifest.json"] = _json_text(manifest)

    quality = _json_file(files, "evidence/evidence_quality_report.json")
    quality["critical_fact_status"] = scenario["critical_fact_status"]
    quality["known_critical_fact_gaps"] = scenario["critical_gaps"]
    if scenario["critical_gaps"]:
        quality["warnings"] = [f"verified_material_omission:{item}" for item in scenario["critical_gaps"]]
        quality["limitations"] = [
            *quality.get("limitations", []),
            "A verified record of an omitted source does not verify the missing underlying fact.",
        ]
    files["evidence/evidence_quality_report.json"] = _json_text(quality)

    files["README.md"] = (
        f"# {scenario['deal_id']}\n\n"
        "SYNTHETIC EVALUATION ONLY. This is an input candidate, not a golden result.\n"
        "It contains no Hermes output, factcheck result, human confirmation, or quality approval.\n\n"
        f"- Golden case: `{case_id}`\n"
        f"- Expected behavior: `{scenario['label']}`\n\n"
        "Regenerate or verify all independent candidate inputs with:\n\n"
        "```bash\n"
        "python eval_datasets/primary_market_ic_real_smoke/generate_golden_suite_fixtures.py\n"
        "python eval_datasets/primary_market_ic_real_smoke/generate_golden_suite_fixtures.py --check\n"
        "```\n"
    )
    if scenario.get("stale_update"):
        _add_stale_update(files, scenario)
    base._validate_rendered_files(files)
    _validate_candidate_input(files, scenario, case_id)
    return base, files


def _contains_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return bool(set(value) & forbidden) or any(_contains_key(item, forbidden) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _validate_candidate_input(
    files: Mapping[str, str],
    scenario: Mapping[str, Any],
    case_id: str,
) -> None:
    if any(path.startswith(("release/", "decision/", "audit/")) for path in files):
        raise AssertionError(f"{case_id} input contains generated behavior artifacts")
    for relative, text in files.items():
        if relative.endswith(".json"):
            payload = json.loads(text)
            if _contains_key(payload, {"human_confirmation", "confirmed_by", "quality_accepted"}):
                if relative not in {"manifest.json", "fixture_contract.json"}:
                    raise AssertionError(f"{case_id} input contains approval-like field: {relative}")
    manifest = _json_file(files, "manifest.json")
    contract = _json_file(files, "fixture_contract.json")
    if manifest.get("deal_id") != scenario["deal_id"] or contract.get("golden_case_id") != case_id:
        raise AssertionError(f"{case_id} input identity mismatch")
    if manifest.get("quality_accepted") is not False or contract.get("quality_accepted") is not False:
        raise AssertionError(f"{case_id} input must remain unaccepted")
    if scenario.get("stale_update"):
        descriptor = _json_file(files, "scenario_inputs/stale_update.json")
        source = descriptor["source"]
        archive = files[source["artifact_manifest_path"]]
        if source["archive_manifest_sha256"] != _sha256(archive):
            raise AssertionError("stale update archive digest mismatch")


def _expected_files(target: Path) -> set[str]:
    return {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()}


def _write_or_check(base: Any, files: Mapping[str, str], *, check: bool) -> None:
    target = Path(base.TARGET)
    if check:
        base.check_fixture(dict(files))
        extras = sorted(_expected_files(target) - set(files))
        if extras:
            raise SystemExit(f"fixture check failed: unexpected:{','.join(extras)}")
        return
    target.mkdir(parents=True, exist_ok=True)
    extras = sorted(_expected_files(target) - set(files))
    if extras:
        raise SystemExit(f"refusing to overwrite fixture with unexpected files: {','.join(extras)}")
    for relative, text in files.items():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed files without writing")
    parser.add_argument("--case-id", choices=sorted(SCENARIOS), action="append")
    args = parser.parse_args(argv)
    selected = args.case_id or sorted(SCENARIOS)
    generated: list[dict[str, Any]] = []
    scenario_files: dict[str, Mapping[str, str]] = {}
    for case_id in selected:
        base, files = build_scenario(case_id)
        _write_or_check(base, files, check=args.check)
        scenario_files[case_id] = files
        generated.append(
            {
                "case_id": case_id,
                "deal_id": base.DEAL_ID,
                "path": str(base.TARGET),
                "file_count": len(files),
            }
        )
    suite_manifest = build_suite_manifest(scenario_files)
    _write_or_check_suite_manifest(suite_manifest, check=args.check)
    print(json.dumps({"action": "checked" if args.check else "generated", "cases": generated}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
