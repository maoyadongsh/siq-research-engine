# Market Modules

Each market owns its own module under `markets/<code>/`.

Required files for a new market:

- `definition.py`: market profile, storage profile, UI/page entry metadata.
- `__init__.py`: exports the market-specific public surface.

Optional files:

- `rules.py`: financial label/concept rules for that market.
- `extractor.py`: market-specific extraction implementation.
- `adapter.py`: bridge to a legacy service or external parser.

Top-level modules (`registry.py`, `storage.py`, `extraction.py`, `app.py`) should stay thin:

- `registry.py` reads market profiles from `markets`.
- `storage.py` reads market storage profiles from `markets`.
- `extraction.py` only dispatches to `markets/<code>/extractor.py`.
- `app.py` only exposes service/API metadata and endpoints.

When adding a market, avoid putting business logic into shared files. Put market differences in that market's own module and only register the module in `markets/__init__.py`.
