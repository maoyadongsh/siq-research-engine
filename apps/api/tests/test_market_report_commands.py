from pathlib import Path

from services import market_report_commands as commands


def test_us_sec_rebuild_package_args_includes_force_metadata_and_output_root():
    args = commands.us_sec_rebuild_package_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/build_sec_evidence_package.py"),
        source_path=Path("/tmp/sec-rebuild/filing.htm"),
        metadata_path=Path("/tmp/sec-rebuild/filing.metadata.json"),
        output_root=Path("/repo/data/wiki/us_sec"),
    )

    assert args == [
        "/usr/bin/python",
        "/repo/scripts/build_sec_evidence_package.py",
        "/tmp/sec-rebuild/filing.htm",
        "--force",
        "--metadata",
        "/tmp/sec-rebuild/filing.metadata.json",
        "--output-root",
        "/repo/data/wiki/us_sec",
    ]
