#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from sec_evidence_lib import extract_contexts, extract_ixbrl_facts, extract_units, soup_from_html, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract inline XBRL facts, contexts and units from a SEC HTML file.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    soup = soup_from_html(args.source.resolve())
    contexts = extract_contexts(soup)
    units = extract_units(soup)
    facts = extract_ixbrl_facts(soup, contexts, units)
    write_json(args.output_dir / "facts_raw.json", {"schema_version": "sec_xbrl_facts_raw_v1", "facts": facts})
    write_json(args.output_dir / "contexts.json", {"schema_version": "sec_xbrl_contexts_v1", "contexts": contexts})
    write_json(args.output_dir / "units.json", {"schema_version": "sec_xbrl_units_v1", "units": units})
    print(f"facts={len(facts)} contexts={len(contexts)} units={len(units)}")


if __name__ == "__main__":
    main()
