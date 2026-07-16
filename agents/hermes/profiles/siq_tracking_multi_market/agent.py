"""Target-only wrapper around the non-CN production tracking scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(
    os.environ.get("SIQ_PROJECT_ROOT") or Path(__file__).resolve().parents[4]
).expanduser().resolve()
DEFAULT_WIKI_BASE = Path(
    os.environ.get("SIQ_WIKI_ROOT")
    or os.environ.get("WIKI_ROOT")
    or PROJECT_ROOT / "data" / "wiki"
).expanduser().resolve()
SCRIPT_DIR = PROJECT_ROOT / "data" / "wiki" / "tracking" / "scripts_multi_market"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_all import _read_target_bundle, run_all


class TrackingAgent:
    """Run tracking only from a server-resolved non-CN target bundle."""

    def __init__(self, wiki_base_path: str | Path = DEFAULT_WIKI_BASE):
        self.wiki_base = str(Path(wiki_base_path).expanduser().resolve())

    def run(
        self,
        target_json: str | Path,
        *,
        skip_sentiment: bool = False,
        use_search: bool = True,
        allow_simulated_sentiment: bool = False,
        strict: bool = False,
        update_analysis: bool = False,
    ) -> dict[str, Any]:
        """Validate the exact ResearchIdentity, then run the tracking chain."""
        target_bundle = _read_target_bundle(str(target_json), self.wiki_base)
        target = target_bundle["research_target"]
        identity = target["research_identity"]
        stock_code = str(target.get("display_code") or identity.get("company_id") or "company")
        company_name = str(
            target.get("display_name") or target.get("company_wiki_id") or stock_code
        )
        return run_all(
            stock_code,
            company_name,
            self.wiki_base,
            skip_sentiment=skip_sentiment,
            use_search=use_search,
            allow_simulated_sentiment=allow_simulated_sentiment,
            strict=strict,
            update_analysis=update_analysis,
            target_bundle=target_bundle,
        )
