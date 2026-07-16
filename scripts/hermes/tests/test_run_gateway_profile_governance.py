from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


RUN_GATEWAY = Path(__file__).resolve().parents[1] / "run_gateway.sh"


@pytest.mark.skipif(shutil.which("rsync") is None, reason="run_gateway requires rsync")
def test_ic_gateway_preserves_live_provider_and_syncs_role_skill_whitelist(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    profiles_root = project_root / "agents" / "hermes" / "profiles"
    source_profile = profiles_root / "siq_ic_finance_auditor"
    shared_root = profiles_root / "siq_ic_shared"
    runtime_profiles = tmp_path / "runtime-profiles"
    runtime_profile = runtime_profiles / "siq_ic_finance_auditor"
    source_profile.mkdir(parents=True)
    runtime_profile.mkdir(parents=True)

    source_config = {
        "model": {"default": "source-model", "provider": "source-provider"},
        "providers": {"source": {}},
        "toolsets": ["terminal", "file", "web", "skills"],
        "agent": {
            "max_turns": 80,
            "tool_use_enforcement": True,
            "disabled_toolsets": ["browser", "memory"],
        },
        "skills": {"creation_nudge_interval": 0},
    }
    live_config = {
        "model": {"default": "live-model", "provider": "live-provider"},
        "providers": {"live": {"base_url": "https://live.invalid"}},
        "fallback_providers": [{"provider": "live-fallback"}],
        "custom_providers": [{"name": "Live Custom"}],
        "toolsets": ["terminal"],
        "agent": {
            "max_turns": 144,
            "tool_use_enforcement": False,
            "disabled_toolsets": ["skills"],
        },
    }
    (source_profile / "config.yaml").write_text(
        yaml.safe_dump(source_config, sort_keys=False),
        encoding="utf-8",
    )
    (runtime_profile / "config.yaml").write_text(
        yaml.safe_dump(live_config, sort_keys=False),
        encoding="utf-8",
    )

    matrix = {
        "profiles": [
            {
                "id": "siq_ic_finance_auditor",
                "skill_ids": ["allowed-finance-skill"],
            }
        ]
    }
    shared_root.mkdir(parents=True)
    (shared_root / "ic_profile_matrix.json").write_text(
        json.dumps(matrix),
        encoding="utf-8",
    )
    for skill_id in ("allowed-finance-skill", "unapproved-shared-skill"):
        skill_root = shared_root / "skills" / skill_id
        skill_root.mkdir(parents=True)
        (skill_root / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")
    stale_skill = runtime_profile / "skills" / "stale-runtime-skill"
    stale_skill.mkdir(parents=True)
    (stale_skill / "SKILL.md").write_text("# stale\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_hermes = fake_bin / "hermes"
    fake_hermes.write_text(
        "#!/usr/bin/env bash\n"
        "test -f \"$HERMES_HOME/.no-bundled-skills\" || exit 10\n"
        "test \"$HERMES_BUNDLED_SKILLS\" = \"$HERMES_HOME/.siq-empty-bundled-skills\" || exit 11\n"
        "test -d \"$HERMES_BUNDLED_SKILLS\" || exit 12\n"
        "test -z \"$(find \"$HERMES_BUNDLED_SKILLS\" -mindepth 1 -print -quit)\" || exit 13\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_hermes.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "SIQ_PROJECT_ROOT": str(project_root),
            "SIQ_HERMES_PROFILES_ROOT": str(runtime_profiles),
            "SIQ_HERMES_FORCE_PROFILE_SYNC": "1",
        }
    )

    result = subprocess.run(
        [str(RUN_GATEWAY), "siq_ic_finance_auditor"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    merged = yaml.safe_load((runtime_profile / "config.yaml").read_text(encoding="utf-8"))
    for key in ("model", "providers", "fallback_providers", "custom_providers"):
        assert merged[key] == live_config[key]
    assert merged["toolsets"] == source_config["toolsets"]
    assert merged["skills"] == source_config["skills"]
    assert merged["agent"]["disabled_toolsets"] == ["browser", "memory"]
    assert merged["agent"]["max_turns"] == 144
    assert (runtime_profile / ".no-bundled-skills").is_file()
    installed_skills = {
        path.name for path in (runtime_profile / "skills").iterdir() if path.is_dir()
    }
    assert installed_skills == {"allowed-finance-skill"}
