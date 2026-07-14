# ruff: noqa: B008

import base64
import json

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers.meeting_stream import router as stream_router
from routers.meetings import router
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole
from services.meeting_config import MeetingSettings, meeting_capabilities
from services.meeting_contracts import MEETING_TABLES, StableSegmentInput
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_repository import MeetingRepository
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


def _user(user_id: int) -> User:
    return User(
        id=user_id,
        username=f"analyst-{user_id}",
        email=f"analyst-{user_id}@example.test",
        hashed_password="x",
        full_name=f"Analyst {user_id}",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )


def _client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'meetings.db'}")

    async def initialize():
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
                )
            )

    anyio.run(initialize)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(stream_router, prefix="/api")
    active_user = {"value": _user(7)}

    async def current_user():
        return active_user["value"]

    async def session_dependency():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_async_session] = session_dependency
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETINGS_ASR_ENABLED", "false")
    return TestClient(app), active_user, engine


def test_meetings_router_session_lifecycle_and_cross_user_404(tmp_path, monkeypatch):
    client, active_user, engine = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/meetings/v1/sessions",
        headers={"Idempotency-Key": "router-create-1"},
        json={
            "title": "周例会",
            "language": "zh-CN",
            "audio_source": "microphone",
            "voiceprint_enabled": False,
            "ai_enabled": False,
            "model_selection": {
                "mode": "none",
                "model_ref": None,
                "fallback_policy": "disabled",
            },
        },
    )
    assert response.status_code == 201
    meeting_id = response.json()["id"]
    assert response.json()["state"] == "draft"

    replay = client.post(
        "/api/meetings/v1/sessions",
        headers={"Idempotency-Key": "router-create-1"},
        json={
            "title": "周例会",
            "language": "zh-CN",
            "audio_source": "microphone",
            "voiceprint_enabled": False,
            "ai_enabled": False,
            "model_selection": {
                "mode": "none",
                "model_ref": None,
                "fallback_policy": "disabled",
            },
        },
    )
    assert replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["id"] == meeting_id

    listed = client.get("/api/meetings/v1/sessions")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == meeting_id
    searched = client.get("/api/meetings/v1/sessions", params={"q": "周例", "sort": "updated_at_desc"})
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert client.get("/api/meetings/v1/sessions", params={"sort": "drop table"}).status_code == 422

    started = client.post(f"/api/meetings/v1/sessions/{meeting_id}/start")
    assert started.status_code == 200
    assert started.json()["session"]["state"] == "connecting"

    active_user["value"] = _user(8)
    hidden = client.get(f"/api/meetings/v1/sessions/{meeting_id}")
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "MEETING_RESOURCE_NOT_FOUND"
    assert client.get(f"/api/meetings/v1/sessions/{meeting_id}/transcript").status_code == 404
    assert client.get(f"/api/meetings/v1/sessions/{meeting_id}/audio/manifest").status_code == 404

    anyio.run(engine.dispose)


def test_capabilities_fail_closed_on_invalid_configuration(tmp_path, monkeypatch):
    client, _, engine = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("SIQ_MEETINGS_MAX_CHUNK_BYTES", "not-a-number")

    response = client.get("/api/meetings/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert "SIQ_MEETINGS_MAX_CHUNK_BYTES must be an integer" in response.json()["configuration_errors"]
    assert client.get("/api/meetings/v1/sessions").status_code == 503
    anyio.run(engine.dispose)


def test_stream_resource_limits_are_validated_and_exposed_fail_closed(monkeypatch):
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETING_MAX_ACTIVE_PER_USER", "2")
    monkeypatch.setenv("SIQ_MEETING_MAX_ACTIVE_TOTAL", "1")
    monkeypatch.setenv("SIQ_MEETING_AUDIO_MAX_FRAMES_PER_SECOND", "0")
    monkeypatch.setenv("SIQ_MEETING_AUDIO_MAX_BYTES_PER_SECOND", "not-a-number")
    invalid = MeetingSettings.from_env()
    assert invalid.operational is False
    assert "SIQ_MEETING_MAX_ACTIVE_PER_USER must not exceed SIQ_MEETING_MAX_ACTIVE_TOTAL" in invalid.errors
    assert any("AUDIO_MAX_FRAMES_PER_SECOND must be between" in error for error in invalid.errors)
    assert "SIQ_MEETING_AUDIO_MAX_BYTES_PER_SECOND must be an integer" in invalid.errors
    assert meeting_capabilities(invalid)["enabled"] is False

    monkeypatch.setenv("SIQ_MEETING_MAX_ACTIVE_PER_USER", "2")
    monkeypatch.setenv("SIQ_MEETING_MAX_ACTIVE_TOTAL", "3")
    monkeypatch.setenv("SIQ_MEETING_AUDIO_MAX_FRAMES_PER_SECOND", "25")
    monkeypatch.setenv("SIQ_MEETING_AUDIO_MAX_BYTES_PER_SECOND", "160000")
    monkeypatch.setenv("SIQ_MEETING_AUDIO_RATE_BURST_SECONDS", "3")
    valid = MeetingSettings.from_env()
    assert valid.operational is True
    limits = meeting_capabilities(valid)["limits"]
    assert limits["max_active_per_user"] == 2
    assert limits["max_active_total"] == 3
    assert limits["audio_max_frames_per_second"] == 25
    assert limits["audio_max_bytes_per_second"] == 160_000
    assert limits["audio_rate_burst_seconds"] == 3


def test_model_catalog_refresh_requires_meeting_admin(tmp_path, monkeypatch):
    client, active_user, engine = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("SIQ_MEETING_AI_ENABLED", "true")
    monkeypatch.setenv(
        "SIQ_MEETINGS_MODEL_CATALOG_JSON",
        json.dumps(
            [
                {
                    "model_ref": "meeting:test:123456789abc",
                    "label": "Test model",
                    "provider_label": "Configured runtime",
                    "locality": "local",
                    "configured": True,
                    "available": True,
                    "capabilities": ["text", "structured_json"],
                    "data_boundary": "local",
                }
            ]
        ),
    )

    denied = client.post("/api/meetings/v1/models/refresh")
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "MEETING_PERMISSION_DENIED"

    active_user["value"].role = UserRole.ADMIN
    refreshed = client.post("/api/meetings/v1/models/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["items"][0]["model_ref"] == "meeting:test:123456789abc"

    anyio.run(engine.dispose)


def test_protected_asr_configuration_requires_internal_service_token(monkeypatch):
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETINGS_ASR_ENABLED", "true")
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "production")
    monkeypatch.setenv(
        "SIQ_MEETING_ASR_WS_URL",
        "ws://meeting-speech:8901/v1/stream/{meeting_id}",
    )
    monkeypatch.delenv("SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("SIQ_MEETING_ASR_SERVICE_TOKEN", raising=False)

    missing = MeetingSettings.from_env()
    assert missing.operational is False
    assert any("INTERNAL_SERVICE_TOKEN" in error for error in missing.errors)

    monkeypatch.setenv("SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN", "test-service-token")
    configured = MeetingSettings.from_env()
    assert configured.operational is True
    assert configured.errors == ()


def test_component_flags_default_off_and_canonical_names_override_legacy(monkeypatch):
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.delenv("SIQ_MEETING_REALTIME_ASR_ENABLED", raising=False)
    monkeypatch.delenv("SIQ_MEETINGS_ASR_ENABLED", raising=False)
    monkeypatch.delenv("SIQ_MEETING_AI_ENABLED", raising=False)
    monkeypatch.delenv("SIQ_MEETINGS_AI_ENABLED", raising=False)
    monkeypatch.delenv("SIQ_MEETING_VOICEPRINT_ENABLED", raising=False)
    monkeypatch.delenv("SIQ_MEETINGS_VOICEPRINT_ENABLED", raising=False)
    monkeypatch.delenv("SIQ_MEETING_CORRECTION_LEARNING_ENABLED", raising=False)

    defaults = MeetingSettings.from_env()
    assert defaults.operational is True
    assert defaults.asr_enabled is False
    assert defaults.ai_enabled is False
    assert defaults.voiceprint_enabled is False
    assert defaults.correction_learning_enabled is False
    assert meeting_capabilities(defaults)["correction_learning"]["available"] is False

    monkeypatch.setenv("SIQ_MEETINGS_ASR_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETING_REALTIME_ASR_ENABLED", "false")
    canonical = MeetingSettings.from_env()
    assert canonical.asr_enabled is False

    monkeypatch.setenv("SIQ_MEETING_CORRECTION_LEARNING_ENABLED", "true")
    learning = MeetingSettings.from_env()
    assert learning.correction_learning_enabled is True
    assert meeting_capabilities(learning)["correction_learning"]["available"] is True


def test_correction_learning_flag_rejects_forged_contribution_but_saves_revision(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SIQ_MEETING_CORRECTION_LEARNING_ENABLED", "false")
    client, _, engine = _client(tmp_path, monkeypatch)
    created = client.post(
        "/api/meetings/v1/sessions",
        json={"title": "订正边界", "ai_enabled": False, "model_selection": {"mode": "none"}},
    )
    meeting_id = created.json()["id"]

    async def seed_segment() -> str:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            segment, _, _ = await MeetingRepository(session).append_stable_segment(
                meeting_id,
                7,
                StableSegmentInput(
                    utterance_id="flag-off-utterance",
                    provider_segment_key="flag-off-segment",
                    start_ms=0,
                    end_ms=900,
                    raw_text="海光新息",
                    asr_final_text="海光新息",
                    asr_provider="meeting-speech",
                    asr_model="test-asr",
                    asr_version="v1",
                ),
            )
            return segment.id

    segment_id = anyio.run(seed_segment)
    corrected = client.patch(
        f"/api/meetings/v1/sessions/{meeting_id}/segments/{segment_id}",
        headers={"Idempotency-Key": "flag-off-forged-contribution"},
        json={
            "text": "海光信息",
            "expected_revision": 0,
            "edit_intent": "asr_error",
            "contribute_to_accuracy": True,
            "candidate_terms": [
                {"canonical_term": "海光信息", "misrecognition": "海光新息", "promote_now": False}
            ],
        },
    )

    assert corrected.status_code == 200
    payload = corrected.json()
    assert payload["segment"]["display_text"] == "海光信息"
    assert payload["revision"]["revision_no"] == 1
    assert payload["feedback"]["status"] == "excluded"
    assert payload["feedback"]["contribute_to_accuracy"] is False
    assert payload["candidate_ids"] == []
    anyio.run(engine.dispose)


def test_voiceprint_auto_match_capability_requires_validated_complete_thresholds(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETINGS_ASR_ENABLED", "false")
    monkeypatch.setenv("SIQ_MEETINGS_VOICEPRINT_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETING_VOICEPRINT_AUTO_MATCH_ENABLED", "true")
    monkeypatch.setenv(
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY",
        base64.urlsafe_b64encode(b"t" * 32).decode("ascii"),
    )
    monkeypatch.setenv(
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH",
        str(tmp_path / "security" / "voiceprint-tombstones.jsonl"),
    )

    monkeypatch.setenv(
        "SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON",
        json.dumps({"auto_match_validated": True}),
    )
    malformed = MeetingSettings.from_env()
    assert malformed.operational is False
    assert meeting_capabilities(malformed)["voiceprint"]["auto_match"] is False

    monkeypatch.setenv(
        "SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON",
        json.dumps(
            {
                "version": "voiceprint-thresholds.v1",
                "suggestion_min_score": 0.75,
                "suggestion_min_margin": 0.08,
                "auto_min_score": 0.92,
                "auto_min_margin": 0.2,
                "min_effective_duration_ms": 6_000,
                "allowed_quality_grades": ["good"],
                "auto_match_validated": True,
            }
        ),
    )
    valid = MeetingSettings.from_env()
    assert valid.operational is True
    assert valid.voiceprint_auto_match_enabled is True
    assert meeting_capabilities(valid)["voiceprint"]["auto_match"] is True
