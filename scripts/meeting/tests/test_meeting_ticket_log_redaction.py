from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_public_api_and_stream_gateway_disable_raw_uvicorn_access_logs():
    sources = {
        "apps/api/start.sh": (ROOT / "apps/api/start.sh").read_text(encoding="utf-8"),
        "start_all.sh": (ROOT / "start_all.sh").read_text(encoding="utf-8"),
        "scripts/meeting/run_meeting_services.sh": (
            ROOT / "scripts/meeting/run_meeting_services.sh"
        ).read_text(encoding="utf-8"),
    }
    assert "main:app --host \"$UVICORN_HOST\" --port \"$SIQ_BACKEND_PORT\" --no-access-log" in sources[
        "apps/api/start.sh"
    ]
    assert "main:app --host \"$BACKEND_HOST\" --port \"$BACKEND_PORT\" --no-access-log" in sources[
        "start_all.sh"
    ]
    assert "meeting_stream_gateway:app" in sources["scripts/meeting/run_meeting_services.sh"]
    assert "--no-access-log" in sources["scripts/meeting/run_meeting_services.sh"]


def test_edge_access_log_uses_query_free_uri_only():
    source = (ROOT / "apps/web/nginx.conf.template").read_text(encoding="utf-8")
    start = source.index("log_format siq_safe")
    end = source.index(";", start)
    declaration = source[start : end + 1]
    assert "$request_method $uri $server_protocol" in declaration
    assert "$request_uri" not in declaration
    assert '"$request "' not in declaration
    assert "access_log /dev/stdout siq_safe;" in source
    audio_location = source[
        source.index("location ~ ^/api/meetings/v1/sessions/[^/]+/audio$") :
    ]
    audio_location = audio_location[: audio_location.index("\n    }")]
    assert "error_log /dev/null crit;" in audio_location
