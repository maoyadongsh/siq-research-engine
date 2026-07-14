import json

import anyio
import httpx
import pytest
from services.meeting_hermes_runner import (
    MeetingAITask,
    MeetingHermesConfigurationError,
    MeetingHermesOutputError,
    MeetingHermesProtocolError,
    MeetingHermesRunner,
    MeetingHermesTarget,
    MeetingHermesTargetPool,
    MeetingHermesTargetUnavailable,
)


def _target(**overrides):
    values = {
        "model_ref": "model:nemotron:v1",
        "target_id": "target:nemotron:v1",
        "label": "Local model",
        "provider_label": "Hermes local",
        "provider": "custom:nemotron-local",
        "model": "nemotron_3_nano_omni",
        "locality": "local",
        "runs_url": "http://127.0.0.1:18701/v1/runs",
        "advertised_model": "siq_meeting_nemotron",
        "api_key_env": "SIQ_TEST_MEETING_HERMES_KEY",
        "context_window": 262144,
        "capabilities": ["text", "structured_json", "long_context"],
    }
    values.update(overrides)
    return MeetingHermesTarget.from_mapping(values, allowed_gateway_hosts={"127.0.0.1"})


def _correction_output():
    return {
        "schema_version": "siq.meeting.correction.v1",
        "patches": [
            {
                "segment_id": "segment-1",
                "base_revision": 0,
                "original": "耐莫创",
                "replacement": "Nemotron",
                "reason_code": "term_correction",
                "confidence": 0.96,
            }
        ],
        "review_flags": [],
    }


def _sse_response(output):
    body = (
        "data: "
        + json.dumps(
            {
                "event": "run.completed",
                "run_id": "run-1",
                "output": json.dumps(output, ensure_ascii=False),
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=body.encode(),
    )


def test_target_pool_rejects_non_allowlisted_gateway_and_duplicate_refs():
    with pytest.raises(MeetingHermesConfigurationError):
        MeetingHermesTarget.from_mapping(
            {
                "model_ref": "model:cloud:v1",
                "target_id": "target:cloud:v1",
                "provider": "cloud-provider",
                "model": "cloud-model",
                "locality": "cloud",
                "runs_url": "https://public.example/v1/runs",
            },
            allowed_gateway_hosts={"127.0.0.1"},
        )

    target = _target()
    with pytest.raises(MeetingHermesConfigurationError):
        MeetingHermesTargetPool([target, target])


def test_target_pool_reads_default_runtime_file(monkeypatch, tmp_path):
    target_file = tmp_path / "meetings" / "hermes-targets.json"
    target_file.parent.mkdir(parents=True)
    target_file.write_text(
        json.dumps(
            [
                {
                    "model_ref": "model:nemotron:v1",
                    "target_id": "target:nemotron:v1",
                    "provider": "custom:nemotron-local",
                    "model": "nemotron_3_nano_omni",
                    "locality": "local",
                    "runs_url": "http://127.0.0.1:18701/v1/runs",
                    "api_key_env": "SIQ_TEST_MEETING_HERMES_KEY",
                    "capabilities": ["text", "structured_json"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("SIQ_MEETINGS_HERMES_TARGETS_JSON", raising=False)
    monkeypatch.delenv("SIQ_MEETINGS_HERMES_TARGETS_FILE", raising=False)
    monkeypatch.setenv("SIQ_RUNTIME_ROOT", str(tmp_path))

    assert MeetingHermesTargetPool.from_env().list_targets()[0].model_ref == "model:nemotron:v1"


def test_snapshot_is_immutable_and_does_not_follow_reconfigured_target():
    runner = MeetingHermesRunner(MeetingHermesTargetPool([_target()]))
    snapshot = runner.resolve_snapshot(
        meeting_id="meeting-1",
        model_ref="model:nemotron:v1",
        selection_mode="pinned",
        settings_version=3,
        effective_after_segment_ordinal=20,
        prompt_version="correction.v2",
    )

    changed = _target(model="different-model")
    changed_runner = MeetingHermesRunner(MeetingHermesTargetPool([changed]))
    with pytest.raises(MeetingHermesTargetUnavailable):
        changed_runner.pool.require_snapshot_target(snapshot)

    assert snapshot.target_id == "target:nemotron:v1"
    assert snapshot.resolved_model == "nemotron_3_nano_omni"
    assert snapshot.settings_version == 3


def test_execute_uses_pinned_gateway_and_validates_structured_output(monkeypatch):
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"run_id": "run-1"})
        return _sse_response(_correction_output())

    async def scenario():
        monkeypatch.setenv("SIQ_TEST_MEETING_HERMES_KEY", "test-token")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runner = MeetingHermesRunner(
            MeetingHermesTargetPool([_target()]),
            client=client,
        )
        snapshot = runner.resolve_snapshot(
            meeting_id="meeting-1",
            model_ref="model:nemotron:v1",
            selection_mode="pinned",
            settings_version=1,
            effective_after_segment_ordinal=0,
            prompt_version="correction.v1",
        )
        result = await runner.execute(
            snapshot=snapshot,
            task=MeetingAITask.CORRECTION,
            job_id="job-1",
            segments=[
                {
                    "segment_id": "segment-1",
                    "revision": 0,
                    "start_ms": 0,
                    "end_ms": 900,
                    "text": "耐莫创",
                    "speaker_label": "发言人 1",
                }
            ],
            glossary=["Nemotron"],
        )
        await client.aclose()
        return result

    result = anyio.run(scenario)

    assert result.output == _correction_output()
    assert requests[0].url == httpx.URL("http://127.0.0.1:18701/v1/runs")
    assert requests[0].headers["authorization"] == "Bearer test-token"
    request_payload = json.loads(requests[0].content)
    assert request_payload["model"] == "siq_meeting_nemotron"
    prompt = json.loads(request_payload["input"])
    assert prompt["output_contract"]["json_schema"]["properties"]["patches"]["type"] == "array"
    assert "provider" not in request_payload
    assert "nemotron_3_nano_omni" not in request_payload


@pytest.mark.parametrize(
    ("task", "output"),
    [
        (
            MeetingAITask.ROLLING_MINUTES,
            {
                "schema_version": "siq.meeting.rolling_minutes.v1",
                "temporary": True,
                "overview": "会议正在讨论发布计划。",
                "agenda_topics": [
                    {"text": "发布计划", "source_segment_ids": ["segment-1"]}
                ],
            },
        ),
        (
            MeetingAITask.FINAL_MINUTES,
            {
                "schema_version": "siq.meeting.final_minutes.v1",
                "overview": "会议决定按计划发布。",
                "decisions": [
                    {"text": "按计划发布", "source_segment_ids": ["segment-1"]}
                ],
            },
        ),
    ],
)
def test_zh_cn_minutes_request_contains_exact_language_constraint(monkeypatch, task, output):
    captured_payload = {}

    def handler(request: httpx.Request):
        if request.method == "POST":
            captured_payload.update(json.loads(request.content))
            return httpx.Response(200, json={"run_id": "run-zh-cn"})
        return _sse_response(output)

    async def scenario():
        monkeypatch.setenv("SIQ_TEST_MEETING_HERMES_KEY", "test-token")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runner = MeetingHermesRunner(MeetingHermesTargetPool([_target()]), client=client)
        snapshot = runner.resolve_snapshot(
            meeting_id="meeting-zh-cn",
            model_ref="model:nemotron:v1",
            selection_mode="pinned",
            settings_version=1,
            effective_after_segment_ordinal=0,
            prompt_version="minutes.v1",
        )
        await runner.execute(
            snapshot=snapshot,
            task=task,
            job_id="job-zh-cn",
            language="zh-CN",
            segments=[
                {
                    "segment_id": "segment-1",
                    "revision": 0,
                    "text": "我们计划 next Friday 发布 SIQ。",
                }
            ],
        )
        await client.aclose()

    anyio.run(scenario)

    expected = (
        "以简体中文为主要输出语言。标题、摘要/概览、议题、章节、决定、未决问题、风险、待办、"
        "发言人观点和关键词必须使用简体中文组织，禁止英文标题和英文叙述正文。人名、公司名、"
        "产品名、技术缩写，以及说话人原本讲出的英文单词可以原样保留。逐字稿引用必须忠实保留"
        "原语言。此约束适用于会议纪要中的每个 text 字段。"
    )
    prompt = json.loads(captured_payload["input"])
    assert prompt["language_contract"] == {
        "meeting_language": "zh-CN",
        "transcript_rule": "Preserve the original language of transcript quotations.",
        "output_language": "zh-CN",
        "enforcement": "required",
        "instruction": expected,
    }
    assert captured_payload["instructions"].endswith(expected)


def test_cloud_target_pseudonymizes_speakers_and_excludes_participant_names(monkeypatch):
    captured_prompt = {}

    def handler(request: httpx.Request):
        if request.method == "POST":
            payload = json.loads(request.content)
            captured_prompt.update(json.loads(payload["input"]))
            return httpx.Response(200, json={"run_id": "run-cloud"})
        return _sse_response(_correction_output())

    async def scenario():
        monkeypatch.setenv("SIQ_TEST_MEETING_HERMES_KEY", "test-token")
        cloud = _target(
            model_ref="model:cloud:v1",
            target_id="target:cloud:v1",
            provider="cloud-provider",
            model="cloud-model",
            locality="cloud",
        )
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runner = MeetingHermesRunner(MeetingHermesTargetPool([cloud]), client=client)
        snapshot = runner.resolve_snapshot(
            meeting_id="meeting-cloud",
            model_ref="model:cloud:v1",
            selection_mode="pinned",
            settings_version=1,
            effective_after_segment_ordinal=0,
            prompt_version="correction.v1",
        )
        await runner.execute(
            snapshot=snapshot,
            task=MeetingAITask.CORRECTION,
            job_id="job-cloud",
            segments=[
                {"segment_id": "segment-1", "revision": 0, "text": "内容", "speaker_label": "张三"}
            ],
            participants=["张三", "李四"],
        )
        await client.aclose()

    anyio.run(scenario)

    assert captured_prompt["input"]["segments"][0]["speaker_label"] == "SPEAKER_01"
    assert captured_prompt["input"]["participants"] == []
    assert "张三" not in json.dumps(captured_prompt, ensure_ascii=False)


def test_execute_rejects_audio_or_voiceprint_input_before_network(monkeypatch):
    called = False

    def handler(_request: httpx.Request):
        nonlocal called
        called = True
        return httpx.Response(500)

    async def scenario():
        monkeypatch.setenv("SIQ_TEST_MEETING_HERMES_KEY", "test-token")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runner = MeetingHermesRunner(MeetingHermesTargetPool([_target()]), client=client)
        snapshot = runner.resolve_snapshot(
            meeting_id="meeting-1",
            model_ref="model:nemotron:v1",
            selection_mode="pinned",
            settings_version=1,
            effective_after_segment_ordinal=0,
            prompt_version="correction.v1",
        )
        with pytest.raises(MeetingHermesProtocolError):
            await runner.execute(
                snapshot=snapshot,
                task=MeetingAITask.CORRECTION,
                job_id="job-1",
                segments=[{"segment_id": "segment-1", "text": "内容", "voiceprint": [0.1]}],
            )
        await client.aclose()

    anyio.run(scenario)
    assert called is False


def test_invalid_model_output_is_not_accepted(monkeypatch):
    invalid_output = {
        "schema_version": "siq.meeting.final_minutes.v1",
        "overview": "结论",
        "agenda_topics": [],
        "chapters": [],
        "decisions": [{"text": "没有证据", "source_segment_ids": []}],
        "open_questions": [],
        "risks": [],
        "action_items": [],
        "speaker_viewpoints": [],
    }

    def handler(request: httpx.Request):
        if request.method == "POST":
            return httpx.Response(200, json={"run_id": "run-invalid"})
        return _sse_response(invalid_output)

    async def scenario():
        monkeypatch.setenv("SIQ_TEST_MEETING_HERMES_KEY", "test-token")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runner = MeetingHermesRunner(MeetingHermesTargetPool([_target()]), client=client)
        snapshot = runner.resolve_snapshot(
            meeting_id="meeting-1",
            model_ref="model:nemotron:v1",
            selection_mode="pinned",
            settings_version=1,
            effective_after_segment_ordinal=0,
            prompt_version="minutes.v1",
        )
        with pytest.raises(MeetingHermesOutputError):
            await runner.execute(
                snapshot=snapshot,
                task=MeetingAITask.FINAL_MINUTES,
                job_id="job-1",
                segments=[{"segment_id": "segment-1", "text": "内容"}],
            )
        await client.aclose()

    anyio.run(scenario)
