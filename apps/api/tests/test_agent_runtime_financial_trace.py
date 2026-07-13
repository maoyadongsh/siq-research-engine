import json
import os
from pathlib import Path

import anyio
from services.agent_runtime_financial_trace import extract_runtime_financial_receipts

from services import agent_chat_runtime as runtime


def _write_session(profile_dir: Path, session_id: str, *, command: str, output: dict, tool_call_id: str = "call-1") -> None:
    session_path = profile_dir / "sessions" / f"session_{session_id}.json"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "calculate"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": tool_call_id,
                                "function": {
                                    "name": "terminal",
                                    "arguments": json.dumps({"command": command}),
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            {"output": json.dumps(output), "exit_code": 0, "error": None}
                        ),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def _ratio_payload() -> dict:
    return {
        "status": "ok",
        "operation": "ratio",
        "input": {
            "numerator": "40",
            "numerator_unit": "HKD million",
            "denominator": "100",
            "denominator_unit": "HKD million",
        },
        "result": {"ratio": "0.4", "percent": "40"},
    }


def _normalize_payload() -> dict:
    return {
        "status": "ok",
        "operation": "normalize_amount",
        "input": {"value": "4427571", "unit": "千元", "currency": "CNY"},
        "result": {
            "native_base_value": "4427571000",
            "native_100m_value": "44.27571",
            "cny_base_value": "4427571000",
            "cny_100m_value": "44.27571",
        },
    }


def test_extracts_single_allowlisted_current_turn_receipt(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:test-session"
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
        output=_ratio_payload(),
    )

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert len(receipts) == 1
    assert receipts[0]["operation"] == "ratio"
    assert receipts[0]["receipt_tool_call_id"] == "call-1"
    assert receipts[0]["receipt_source"] == "hermes_session_tool"


def test_extracts_allowlisted_amount_normalization_receipt(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:normalize-session"
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json normalize --value 4427571 --unit 千元 --currency CNY",
        output=_normalize_payload(),
    )

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert len(receipts) == 1
    assert receipts[0]["operation"] == "normalize_amount"
    assert receipts[0]["result"]["native_100m_value"] == "44.27571"


def test_extracts_only_newest_exact_session_across_profile_roots(tmp_path):
    logical_profile = tmp_path / "siq_assistant"
    live_profile = tmp_path / "finsight_assistant"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:test-session"
    old_payload = _ratio_payload()
    new_payload = _ratio_payload()
    new_payload["result"] = {"ratio": "0.3", "percent": "30"}
    _write_session(
        logical_profile,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
        output=old_payload,
        tool_call_id="old-call",
    )
    _write_session(
        live_profile,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 30 --denominator 100",
        output=new_payload,
        tool_call_id="new-call",
    )
    old_path = logical_profile / "sessions" / f"session_{session_id}.json"
    new_path = live_profile / "sessions" / f"session_{session_id}.json"
    os.utime(old_path, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new_path, ns=(2_000_000_000, 2_000_000_000))

    receipts = extract_runtime_financial_receipts(
        profile_dir=logical_profile,
        profile_dirs=(live_profile,),
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert len(receipts) == 1
    assert receipts[0]["receipt_tool_call_id"] == "new-call"
    assert receipts[0]["result"]["percent"] == "30"


def test_runtime_includes_live_finsight_profile_as_receipt_candidate(tmp_path, monkeypatch):
    logical_profile = tmp_path / "siq_assistant"
    captured = {}

    def fake_extract(**kwargs):
        captured.update(kwargs)
        return ()

    monkeypatch.setitem(runtime.HERMES_PROFILE_ROOTS, "siq_assistant", logical_profile)
    monkeypatch.setattr(runtime, "HERMES_PROFILES_ROOT", tmp_path)
    monkeypatch.setattr(runtime.agent_runtime_financial_trace, "extract_runtime_financial_receipts", fake_extract)

    assert runtime._trusted_financial_receipts("siq_assistant", "chat-session") == ()
    assert captured["profile_dir"] == logical_profile
    assert captured["profile_dirs"] == (tmp_path / "finsight_assistant",)


def test_rejects_piped_or_chained_financial_command(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:test-session"
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 40 --denominator 100 | head",
        output=_ratio_payload(),
    )

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert receipts == ()


def test_rejects_unpaired_or_mixed_stdout_receipt(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:test-session"
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
        output=_ratio_payload(),
        tool_call_id="call-command",
    )
    path = profile_dir / "sessions" / f"session_{session_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["messages"][-1]["tool_call_id"] = "call-other"
    path.write_text(json.dumps(payload), encoding="utf-8")

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert receipts == ()


def test_rejects_operation_that_does_not_belong_to_invoked_script(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:test-session"
    payload = _ratio_payload()
    payload["operation"] = "goodwill_reconciliation"
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
        output=payload,
    )

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert receipts == ()


def test_runtime_retries_financial_receipt_after_terminal_event(monkeypatch):
    calls = 0
    delays: list[float] = []
    receipt = {"operation": "ratio", "receipt_source": "hermes_session_tool"}

    def fake_receipts(_profile, _session_id):
        nonlocal calls
        calls += 1
        return () if calls == 1 else (receipt,)

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(runtime, "_trusted_financial_receipts", fake_receipts)
    monkeypatch.setattr(runtime.asyncio, "sleep", fake_sleep)

    async def run_case():
        return await runtime._trusted_financial_receipts_after_run(
            "siq_assistant",
            "session-id",
            message="前两大商誉占比是多少？",
            reply="前两大商誉占比为 86.93%。",
        )

    result = anyio.run(run_case)

    assert result == (receipt,)
    assert calls == 2
    assert delays == [0.02]


def test_runtime_does_not_wait_for_non_financial_receipt(monkeypatch):
    calls = 0

    def fake_receipts(_profile, _session_id):
        nonlocal calls
        calls += 1
        return ()

    async def unexpected_sleep(_delay):
        raise AssertionError("non-financial replies must not wait for a financial receipt")

    monkeypatch.setattr(runtime, "_trusted_financial_receipts", fake_receipts)
    monkeypatch.setattr(runtime.asyncio, "sleep", unexpected_sleep)

    async def run_case():
        return await runtime._trusted_financial_receipts_after_run(
            "siq_assistant",
            "session-id",
            message="请概述公司战略。",
            reply="公司战略聚焦产品升级。",
        )

    result = anyio.run(run_case)

    assert result == ()
    assert calls == 1
