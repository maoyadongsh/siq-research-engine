from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
START = ROOT / "scripts/openshell/start_gateway.sh"
IDENTITY = ROOT / "scripts/openshell/gateway_runtime_identity.py"
RECOVERY = ROOT / "scripts/openshell/gateway_start_recovery.py"
ACTIVATION = ROOT / "scripts/openshell/configure_bind_mount_contract.sh"
STOP = ROOT / "scripts/openshell/stop_gateway.sh"


def _shell_function(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(name)}\(\) \{{\n(?P<body>.*?)^\}}$",
        source,
    )
    assert match is not None, f"missing shell function: {name}"
    return match.group("body")


def _subcommand_names(parser) -> set[str]:
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            return set(choices)
    raise AssertionError("gateway runtime identity parser has no subcommands")


def test_failed_start_cleanup_never_sends_a_signal_with_shell_kill() -> None:
    start = START.read_text(encoding="utf-8")
    cleanup = _shell_function(start, "cleanup_failed_start")

    # `kill -0` is only an existence probe. Every actual signal must go through
    # the process-bound pidfd recovery path, including startup error cleanup.
    naked_signal = re.search(r"(?m)^\s*kill\s+(?!-0(?:\s|$))", cleanup)
    assert naked_signal is None
    assert "gateway_start_recovery.py" in cleanup
    assert "recover --reap" in cleanup


def test_stop_persists_reap_evidence_before_pidfd_only_termination() -> None:
    stop = STOP.read_text(encoding="utf-8")

    resume = stop.index("recover >/dev/null")
    sandbox_gate = stop.index("sandbox list")
    reap = stop.index("recover --reap")
    assert resume < sandbox_gate < reap
    assert "gateway_start_recovery.py" in stop
    assert "gateway_runtime_identity.py" not in stop
    assert re.search(r"(?m)^\s*kill(?:\s|$)", stop) is None


def test_gateway_start_persists_intent_provisional_and_runtime_in_order() -> None:
    start = START.read_text(encoding="utf-8")
    identity = IDENTITY.read_text(encoding="utf-8")
    recovery = RECOVERY.read_text(encoding="utf-8")

    for declaration in (
        'START_INTENT="$GATEWAY_ROOT/gateway.start.intent.json"',
        'STARTING_FILE="$GATEWAY_ROOT/gateway.starting.json"',
        'RUNTIME_FILE="$GATEWAY_ROOT/gateway.runtime.json"',
    ):
        assert declaration in start
    assert "INTENT_SCHEMA" in recovery
    assert "STARTING_SCHEMA" in identity + recovery
    assert 'SCHEMA = "siq.openshell.gateway_process.v1"' in identity

    intent = start.index("prepare >/dev/null")
    spawn = start.index('exec nohup setsid "$GATEWAY_BIN"')
    provisional = start.index(" attach --pid ")
    committed = start.index(" commit --pid ")
    assert intent < spawn < provisional < committed


def test_start_recovery_contract_can_adopt_or_reap_provisional_process() -> None:
    start = START.read_text(encoding="utf-8")
    recovery = RECOVERY.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("siq_gateway_start_recovery_under_test", RECOVERY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert "recover" in _subcommand_names(module._parser())
    assert 'return "adopted"' in recovery
    assert 'return "reaped"' in recovery

    # Recovery must run before start_gateway treats partial records or occupied
    # listeners as an unrecoverable conflict, and failed cleanup uses the same
    # identity-bound operation instead of reconstructing process identity.
    recover = start.index("gateway_start_recovery.py")
    evidence_gate = start.index('if [[ -e "$PID_FILE"')
    port_gate = start.index("sport = :$GATEWAY_PORT")
    assert recover < evidence_gate < port_gate
    assert "recover --reap" in _shell_function(start, "cleanup_failed_start")


def test_transaction_cleanup_durably_removes_journal_before_backups() -> None:
    activation = ACTIVATION.read_text(encoding="utf-8")
    cleanup = _shell_function(activation, "clear_transaction_files")

    combined_remove = 'rm -f -- "$JOURNAL" "$PREVIOUS_ACTIVATION" "$PREVIOUS_CONFIG" "$TARGET_ACTIVATION"'
    backup_remove_command = 'rm -f -- "$PREVIOUS_ACTIVATION" "$PREVIOUS_CONFIG" "$TARGET_ACTIVATION"'
    assert combined_remove not in cleanup
    assert backup_remove_command in cleanup

    journal_remove = cleanup.index('rm -f -- "$JOURNAL"')
    journal_fsync = cleanup.index('fsync_directory "$GATEWAY_ROOT"', journal_remove)
    backup_remove = cleanup.index(backup_remove_command, journal_fsync)
    backup_fsync = cleanup.index('fsync_directory "$GATEWAY_ROOT"', backup_remove)

    assert journal_remove < journal_fsync < backup_remove < backup_fsync
