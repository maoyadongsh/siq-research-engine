from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PATCH_ROOT = ROOT / "infra" / "openshell" / "patches" / "hermes-0.13.0"
PREPARE = ROOT / "scripts" / "openshell" / "prepare_siq_analysis_context.sh"
BUILD = ROOT / "scripts" / "openshell" / "build_siq_analysis_image.sh"

PATCH_NAMES = (
    "0001-runtime-auth-file-override.patch",
    "0002-runtime-state-home-override.patch",
    "0003-api-run-stop-quiescence.patch",
)
PATCH_THREE_SHA256 = "84555a500afd0c7cacb37acbafab55a1cc06867c21aa30a97c78b93420f8a17c"
PATCH_BUNDLE_SHA256 = "aabc1d6fdd252acc4131bf6b843c96c43f875267c5b08100cdc94700c762a242"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_stop_patch_waits_for_executor_quiescence_without_cancelling_wrapper() -> None:
    patch = (PATCH_ROOT / PATCH_NAMES[2]).read_text(encoding="utf-8")
    stop_hunk = patch[patch.index("+        self._run_stop_requests.add(run_id)") :]

    assert "-            task.cancel()" in stop_hunk
    assert "+            task.cancel()" not in stop_hunk
    stop_intent = stop_hunk.index("self._run_stop_requests.add(run_id)")
    approval_release = stop_hunk.index("unregister_gateway_notify(approval_session_key)")
    executor_wait = stop_hunk.index(
        "await asyncio.wait_for(asyncio.shield(task), timeout=5.0)"
    )
    assert stop_intent < approval_release < executor_wait
    assert '+        return web.json_response({"run_id": run_id, "status": "stopping"})' in stop_hunk
    assert "current_status" not in stop_hunk


def test_stop_patch_distinguishes_confirmed_stop_from_asyncio_cancellation() -> None:
    patch = (PATCH_ROOT / PATCH_NAMES[2]).read_text(encoding="utf-8")

    assert "self._run_stop_requests: set[str] = set()" in patch
    assert "if run_id in self._run_stop_requests:" in patch
    assert "_mark_cancelled(runtime, quiesced=True)" in patch
    cancelled = patch[patch.index("except asyncio.CancelledError") :]
    assert "_mark_cancelled(runtime, quiesced=False)" in cancelled
    assert patch.count('"quiesced": True') >= 3
    assert patch.count("quiesced=True") >= 4
    assert '"stopping",\n+            last_event="run.stopping",\n+            quiesced=False' in patch


def test_context_and_image_pin_all_three_patch_checksums_in_order() -> None:
    hashes = [_sha256(PATCH_ROOT / name) for name in PATCH_NAMES]
    bundle = hashlib.sha256(("\n".join(hashes) + "\n").encode("ascii")).hexdigest()
    prepare = PREPARE.read_text(encoding="utf-8")
    build = BUILD.read_text(encoding="utf-8")

    assert hashes[2] == PATCH_THREE_SHA256
    assert bundle == PATCH_BUNDLE_SHA256
    assert f'readonly HERMES_PATCH_THREE_SHA256="{PATCH_THREE_SHA256}"' in prepare
    assert f'readonly HERMES_INTEGRATION_PATCH_SHA256="{PATCH_BUNDLE_SHA256}"' in prepare
    assert f'readonly EXPECTED_INTEGRATION_PATCH="{PATCH_BUNDLE_SHA256}"' in build
    assert prepare.index(PATCH_NAMES[0]) < prepare.index(PATCH_NAMES[1]) < prepare.index(
        PATCH_NAMES[2]
    )
    assert '"hermes_run_quiescence_patch_sha256": "$HERMES_PATCH_THREE_SHA256"' in prepare
    assert "HERMES_RUN_QUIESCENCE_PATCH" in prepare
    assert "self._run_stop_requests: set[str] = set()" in prepare
