#!/usr/bin/env python3
"""Migrate safe OpenClaw IC assets into SIQ Hermes profiles.

This script is intentionally allowlist-based. It copies reusable prompt,
protocol, template, and skill assets while excluding runtime state, memories,
project artifacts, credentials, and OpenClaw-local scripts that need SIQ service
wrappers.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
OPENCLAW_ROOT = Path("/home/maoyd/.openclaw/workspace")
PROFILES_ROOT = REPO_ROOT / "agents" / "hermes" / "profiles"
IC_SHARED_ROOT = PROFILES_ROOT / "siq_ic_shared"


@dataclass(frozen=True)
class ProfileAsset:
    openclaw_workspace: str
    hermes_profile: str
    files: tuple[str, ...]


PROFILE_ASSETS: tuple[ProfileAsset, ...] = (
    ProfileAsset(
        "ic_master_coordinator_workspace",
        "siq_ic_master_coordinator",
        ("BOOTSTRAP.md", "HEARTBEAT.md", "USER.md"),
    ),
    ProfileAsset(
        "ic_chairman_workspace",
        "siq_ic_chairman",
        ("BOOTSTRAP.md", "HEARTBEAT.md", "USER.md", "KNOWLEDGE_BASE.md", "QUICK_REFERENCE.md"),
    ),
    ProfileAsset(
        "ic_strategist_workspace",
        "siq_ic_strategist",
        ("BOOTSTRAP.md", "HEARTBEAT.md", "USER.md", "WORKFLOW.md"),
    ),
    ProfileAsset(
        "ic_sector_expert_workspace",
        "siq_ic_sector_expert",
        ("BOOTSTRAP.md", "HEARTBEAT.md", "USER.md"),
    ),
    ProfileAsset(
        "ic_finance_auditor_workspace",
        "siq_ic_finance_auditor",
        ("BOOTSTRAP.md", "HEARTBEAT.md", "USER.md", "STARTUP_CHECKLIST.md"),
    ),
    ProfileAsset(
        "ic_legal_scanner_workspace",
        "siq_ic_legal_scanner",
        ("BOOTSTRAP.md", "HEARTBEAT.md", "USER.md"),
    ),
    ProfileAsset(
        "ic_risk_controller_workspace",
        "siq_ic_risk_controller",
        ("BOOTSTRAP.md", "HEARTBEAT.md", "USER.md", "STARTUP_PROTOCOL.md", "WORK_PROTOCOL.md"),
    ),
)


SHARED_TEMPLATES: tuple[tuple[str, str], ...] = (
    (
        "ic_master_coordinator_workspace/shared/templates/RETRIEVAL_MANDATORY_RULE.md",
        "templates/RETRIEVAL_MANDATORY_RULE.md",
    ),
    (
        "ic_master_coordinator_workspace/shared/templates/SIQ_DECISION_REPORT.md",
        "templates/SIQ_DECISION_REPORT.md",
    ),
    (
        "ic_master_coordinator_workspace/templates/SIQ_IC_Decision_Report_Template_v1.md",
        "templates/SIQ_IC_Decision_Report_Template_v1.md",
    ),
    (
        "ic_master_coordinator_workspace/templates/IC投决报告模板_v1.0.md",
        "templates/IC投决报告模板_v1.0.md",
    ),
)


SKILL_BATCHES: dict[str, tuple[str, ...]] = {
    "batch_1_core_ic": (
        "ic-finance-auditor",
        "ic-memo",
        "venture-capital",
        "due-diligence-analyst",
        "due-diligence-dataroom",
        "deal-screening",
        "startup-tools",
        "term-sheet-analyzer",
        "tam-sam-som",
        "cap-table-manager",
        "unit-economics",
        "dcf-model",
        "3-statement-model",
    ),
    "batch_2_research_valuation_pipeline": (
        "market-intelligence-claw",
        "competitive-analysis",
        "strategic-competitor-analysis",
        "thesis-tracker",
        "comps-analysis",
        "equity-valuation-framework",
        "financial-analyst",
        "teaser",
        "pitch-deck",
        "deal-sourcing",
        "deal-tracker",
        "risk-metrics-calculation",
        "portfolio-monitoring",
    ),
    "batch_3_diligence_research_materials": (
        "company-investment-research",
        "dd-checklist",
        "dd-meeting-prep",
        "sector-overview",
        "mckinsey-research",
        "initiating-coverage",
        "cim-builder",
        "buyer-list",
        "value-creation-plan",
        "ai-readiness",
        "financial-analysis-agent",
        "merger-model",
        "returns-analysis",
        "return-rate-impact-calculator",
    ),
}

SELECTED_SKILLS: tuple[str, ...] = tuple(
    skill_name for batch in SKILL_BATCHES.values() for skill_name in batch
)


SKIP_REFERENCES: tuple[tuple[str, str], ...] = (
    (
        "ic_strategist_workspace/milvus_query.py",
        "contains a hard-coded external API key; use SIQ startup-retrieval service instead",
    ),
    (
        "ic_risk_controller_workspace/scripts/startup_retrieval.py",
        "OpenClaw-local wrapper writes runtime evidence files; migrated behavior lives in apps/api/services/ic_startup_retrieval.py",
    ),
    (
        "ic_master_coordinator_workspace/templates/IC投研决策报告模板_v2.0_正式版.md",
        "project-specific Unitree example, not a reusable template",
    ),
    (
        "ic_master_coordinator_workspace/templates/IC投研决策报告模板_v2.0_正式版.html",
        "project-specific Unitree example, not a reusable template",
    ),
)


SKIPPED_SKILLS: tuple[tuple[str, str], ...] = (
    (
        "lbo-model",
        "source skill references examples/LBO_Model.xlsx, but that template is absent in the OpenClaw skill directory",
    ),
    (
        "investment-proposal",
        "wealth-management client proposal workflow; outside the SIQ IC deal committee execution path",
    ),
)


REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "/home/maoyd/.openclaw/workspace/ic_collaboration_shared_ws/",
        "/home/maoyd/siq-research-engine/data/wiki/deals/",
    ),
    (
        "/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/shared/projects/{项目目录}/",
        "/home/maoyd/siq-research-engine/data/wiki/deals/{deal_id}/",
    ),
    (
        "/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/shared/projects/{项目目录}",
        "/home/maoyd/siq-research-engine/data/wiki/deals/{deal_id}",
    ),
    (
        "/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/shared/projects/",
        "/home/maoyd/siq-research-engine/data/wiki/deals/",
    ),
    (
        "/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/shared/projects",
        "/home/maoyd/siq-research-engine/data/wiki/deals",
    ),
    (
        "/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/shared/",
        "/home/maoyd/siq-research-engine/data/wiki/deals/",
    ),
    (
        "/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace",
        "/home/maoyd/siq-research-engine",
    ),
    ("~/.openclaw/skills", "/home/maoyd/siq-research-engine/agents/hermes/profiles/siq_ic_shared/skills"),
    ("OpenClaw skills directory", "SIQ IC shared skills directory"),
    ("~/.openclaw/workspace/ic_risk_controller_workspace", "/home/maoyd/siq-research-engine"),
    ("shared/projects/{项目目录}/", "data/wiki/deals/{deal_id}/"),
    ("shared/projects/{项目目录}", "data/wiki/deals/{deal_id}"),
    ("shared/projects/", "data/wiki/deals/"),
    ("shared/projects", "data/wiki/deals"),
    ("ic_master_coordinator", "siq_ic_master_coordinator"),
    ("ic_chairman", "siq_ic_chairman"),
    ("ic_strategist", "siq_ic_strategist"),
    ("ic_sector_expert", "siq_ic_sector_expert"),
    ("ic_finance_auditor", "siq_ic_finance_auditor"),
    ("ic_legal_scanner", "siq_ic_legal_scanner"),
    ("ic_risk_controller", "siq_ic_risk_controller"),
    ("ic_collaboration_shared", "siq_deal_shared"),
    ("unified_hybrid_retriever.py", "SIQ startup-retrieval API"),
    ("unified_hybrid_retriever", "SIQ startup-retrieval API"),
    ("siq_workflow_policy.json", "agents/hermes/profiles/siq_ic_shared/ic_workflow_policy.json"),
)


RETRIEVAL_MANDATORY_RULE = """# SIQ 双库检索硬性规则（所有 Agent 适用）

> 不可跳过规则：任何 `siq_ic_*` Agent 在发表投资观点前，必须完成 Deal OS startup-retrieval。未执行检索即发表观点 = 无效报告。

---

## 一、标准入口

所有角色使用 SIQ Deal OS 后端入口，而不是 OpenClaw 本地脚本：

```text
POST /api/deals/{deal_id}/agents/{agent_id}/startup-retrieval
```

请求体示例：

```json
{
  "round_name": "R1",
  "query": "{company_name} {industry} {stage}",
  "limit": 20
}
```

其中：

- `deal_id`: `data/wiki/deals/{deal_id}` 下的项目包 ID。
- `agent_id`: canonical Hermes profile ID，例如 `siq_ic_finance_auditor`。
- `round_name`: `R0` / `R1` / `R1.5` / `R2` / `R3` / `R4`。

---

## 二、检索目标

| 目标 | Collection / 来源 | 最少命中 |
|------|-------------------|---------|
| 共享项目底稿 | `siq_deal_shared` / deal evidence package | 5 条 |
| 私有知识库 | `{agent_id}` | 3 条（允许 0 条但必须标注） |
| workspace 规则 | 当前 profile 的 `SOUL.md`、`AGENTS.md`、`BOOTSTRAP.md` 等 | 必读 |

---

## 三、报告强制章节

每位专家的 R1 报告必须包含：

```markdown
## 检索结果摘要

### 共享底稿证据（Top-10）
- [evidence_id] 来源 / 时间 / 关键事实 / 置信度

### 私有知识库证据（Top-10）
- [evidence_id] 方法论 / 框架 / 历史案例 / 适用边界

### 证据缺口
- 缺口：
- 对结论影响：
- 需要补充材料：
```

---

## 四、降级规则

- Startup retrieval API 失败时，必须读取 `data/wiki/deals/{deal_id}` 项目包中的本地证据文件。
- Milvus 或私有知识库不可用时，必须在报告中写明 `private_kb_unavailable` 或 `retrieval_degraded`。
- 降级后的报告不得给出 High 置信度结论，除非项目包内已有足够可审计证据。
"""


def replace_section(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = text.find(start_marker)
    if start == -1:
        return text
    end = text.find(end_marker, start + len(start_marker))
    if end == -1:
        return text[:start] + replacement.rstrip() + "\n"
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def postprocess_text(target: Path, text: str) -> str:
    if target.name == "RETRIEVAL_MANDATORY_RULE.md":
        return RETRIEVAL_MANDATORY_RULE

    normalized = text
    if "/skills/" in target.as_posix() and target.suffix.lower() in {".md", ".yaml", ".yml", ".toml"}:
        skill_replacements = (
            ("CRITICAL INSTRUCTIONS FOR CLAUDE", "CRITICAL INSTRUCTIONS FOR HERMES AGENT"),
            ("Claude", "the Hermes agent"),
            ('metadata: {"openclaw"', 'metadata: {"siq_hermes"'),
            ("  openclaw:", "  siq_hermes:"),
            ("OpenClaw skill", "SIQ Hermes skill"),
            ("OpenClaw skills", "SIQ Hermes skills"),
            ("OpenClaw Skill", "SIQ Hermes Skill"),
            ("OpenClaw 2026+", "SIQ Hermes"),
            ("via OpenClaw", "via SIQ Hermes"),
            ("OpenClaw", "SIQ Hermes"),
            ("web_search", "web search"),
        )
        for source, replacement in skill_replacements:
            normalized = normalized.replace(source, replacement)
        normalized = normalized.replace(
            "homepage: https://github.com/yourusername/openclaw-due-diligence-analyst\n",
            "",
        )
    if target.name == "BOOTSTRAP.md" and "scripts/milvus_mcp_server.py" in normalized:
        normalized = normalized.replace(
            "> ⚠️ `agent_startup_retrieval` 是 `scripts/milvus_mcp_server.py` 暴露的 MCP 工具函数，不是本地 Python 脚本。\n"
            "> 调用前需确认 MCP server 已启动（`python3 scripts/milvus_mcp_server.py`）。",
            "> `agent_startup_retrieval` 在 SIQ/Hermes 中表示 Deal OS startup-retrieval 服务调用。\n"
            "> 标准入口是 `POST /api/deals/{deal_id}/agents/{agent_id}/startup-retrieval`，或同源后端 `apps/api/services/ic_startup_retrieval.py`。",
        )
    if target.as_posix().endswith("due-diligence-analyst/README.md"):
        normalized = replace_section(
            normalized,
            "### Enable in OpenClaw",
            "### Usage",
            """### Enable in Hermes

This migrated skill is synchronized into each executable `siq_ic_*` runtime profile by:

```bash
scripts/hermes/run_gateway.sh siq_ic_master_coordinator
```

No separate OpenClaw skill enable step is required inside SIQ Hermes.""",
        )
    if target.name == "STARTUP_CHECKLIST.md":
        normalized = replace_section(
            normalized,
            "## 五、自动化脚本（可选）",
            "---\n\n**固化确认**",
            """## 五、自动化入口（SIQ/Hermes）

在 Hermes 中不再直接执行 OpenClaw 本地检索脚本。财务专家使用 Deal OS 后端：

```text
POST /api/deals/{deal_id}/agents/siq_ic_finance_auditor/startup-retrieval
```

请求体：

```json
{
  "round_name": "R1",
  "query": "{company_name} 收入 利润 现金流 估值 财务",
  "limit": 20
}
```

若 API 不可用，降级读取 `data/wiki/deals/{deal_id}` 项目包，并在报告中标注 `retrieval_degraded`。""",
        )
    if target.name == "STARTUP_PROTOCOL.md":
        normalized = replace_section(
            normalized,
            "## 六、自动化脚本（可选）",
            "## 七、检查清单（Checklist）",
            """## 六、自动化入口（SIQ/Hermes）

在 Hermes 中不再直接执行 OpenClaw 本地 `startup_retrieval.py`。风控专家使用 Deal OS 后端：

```text
POST /api/deals/{deal_id}/agents/siq_ic_risk_controller/startup-retrieval
```

请求体：

```json
{
  "round_name": "R1",
  "query": "{company_name} 风险 供应链 舆情 行业周期",
  "limit": 20
}
```

若 API 不可用，降级读取 `data/wiki/deals/{deal_id}` 项目包，并在报告中标注 `retrieval_degraded`。""",
        )
    return normalized


TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".js",
    ".ts",
    ".html",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(text: str, target: Path) -> str:
    normalized = text
    for source, replacement in REPLACEMENTS:
        normalized = normalized.replace(source, replacement)
    return postprocess_text(target, normalized)


def copy_text_asset(source: Path, target: Path) -> dict[str, str]:
    text = source.read_text(encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(normalize_text(text, target), encoding="utf-8")
    return {
        "source": str(source),
        "target": str(target),
        "sha256": sha256(target),
    }


def copy_binary_asset(source: Path, target: Path) -> dict[str, str]:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {
        "source": str(source),
        "target": str(target),
        "sha256": sha256(target),
    }


def copy_asset(source: Path, target: Path) -> dict[str, str]:
    if source.suffix.lower() in TEXT_SUFFIXES:
        return copy_text_asset(source, target)
    return copy_binary_asset(source, target)


def safe_skill_files(skill_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(skill_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(skill_root).parts):
            continue
        if path.name in {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}:
            continue
        files.append(path)
    return files


def migrate_profile_assets(inventory: dict[str, object]) -> None:
    migrated: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    for group in PROFILE_ASSETS:
        for filename in group.files:
            source = OPENCLAW_ROOT / group.openclaw_workspace / filename
            target = PROFILES_ROOT / group.hermes_profile / filename
            if not source.is_file():
                missing.append({"source": str(source), "reason": "missing"})
                continue
            migrated.append(copy_asset(source, target))
    inventory["profile_assets"] = migrated
    inventory["profile_missing"] = missing


def migrate_shared_templates(inventory: dict[str, object]) -> None:
    migrated: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    for source_relative, target_relative in SHARED_TEMPLATES:
        source = OPENCLAW_ROOT / source_relative
        target = IC_SHARED_ROOT / target_relative
        if not source.is_file():
            missing.append({"source": str(source), "reason": "missing"})
            continue
        migrated.append(copy_asset(source, target))
    inventory["shared_templates"] = migrated
    inventory["shared_templates_missing"] = missing


def migrate_skills(inventory: dict[str, object]) -> None:
    target_root = IC_SHARED_ROOT / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    migrated: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    for batch_name, skill_names in SKILL_BATCHES.items():
        for skill_name in skill_names:
            source_root = OPENCLAW_ROOT / "skills" / skill_name
            if not source_root.is_dir():
                missing.append({"batch": batch_name, "source": str(source_root), "reason": "missing"})
                continue
            target_skill_root = target_root / skill_name
            if target_skill_root.exists():
                shutil.rmtree(target_skill_root)
            for source in safe_skill_files(source_root):
                relative = source.relative_to(source_root)
                target = target_skill_root / relative
                copied = copy_asset(source, target)
                copied["batch"] = batch_name
                copied["skill"] = skill_name
                migrated.append(copied)
    inventory["skill_batches"] = {batch: list(skills) for batch, skills in SKILL_BATCHES.items()}
    inventory["skills"] = migrated
    inventory["skills_missing"] = missing


def write_skill_readme(inventory: dict[str, object]) -> None:
    target = IC_SHARED_ROOT / "skills" / "README.md"
    batch_sections = []
    for batch_name, skill_names in SKILL_BATCHES.items():
        skill_lines = "\n".join(f"- `{skill}`" for skill in skill_names)
        batch_sections.append(f"### {batch_name}\n\n{skill_lines}")
    batch_text = "\n\n".join(batch_sections)
    target.write_text(
        "# SIQ IC Shared Skills\n\n"
        "These skills are migrated from the OpenClaw workspace for the SIQ IC profiles. "
        "They are shared by all executable `siq_ic_*` Hermes profiles and synchronized "
        "into each runtime profile by `scripts/hermes/run_gateway.sh`.\n\n"
        "## Included Skills\n\n"
        f"{batch_text}\n\n"
        "## Migration Rules\n\n"
        "- Runtime state, hidden metadata folders, credentials, caches, and project outputs are not copied.\n"
        "- OpenClaw-local scripts with credentials or old workspace paths are represented by SIQ services instead.\n"
        "- Agent IDs and collection names in text assets are normalized to `siq_ic_*` and `siq_deal_shared`.\n",
        encoding="utf-8",
    )
    inventory["skills_readme"] = {"target": str(target), "sha256": sha256(target)}


def write_inventory(inventory: dict[str, object]) -> None:
    skipped = [{"source": str(OPENCLAW_ROOT / source), "reason": reason} for source, reason in SKIP_REFERENCES]
    inventory["skipped"] = skipped
    inventory["skipped_skills"] = [
        {"source": str(OPENCLAW_ROOT / "skills" / skill_name), "reason": reason}
        for skill_name, reason in SKIPPED_SKILLS
    ]
    inventory["schema_version"] = "siq_ic_openclaw_asset_migration_inventory_v1"
    inventory["source_root"] = str(OPENCLAW_ROOT)
    inventory["target_root"] = str(PROFILES_ROOT)
    inventory["normalization_rules"] = [
        "legacy ic_* agent IDs are rewritten to canonical siq_ic_* profile IDs",
        "ic_collaboration_shared is rewritten to siq_deal_shared",
        "OpenClaw shared project paths are rewritten to data/wiki/deals",
        "unified_hybrid_retriever.py references are rewritten to the SIQ startup-retrieval API",
    ]
    target = IC_SHARED_ROOT / "openclaw_asset_migration_inventory.json"
    target.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    inventory: dict[str, object] = {}
    migrate_profile_assets(inventory)
    migrate_shared_templates(inventory)
    migrate_skills(inventory)
    write_skill_readme(inventory)
    write_inventory(inventory)


if __name__ == "__main__":
    main()
