### Task 5: Run The KR 30 Download And Parser Queue Verification

**Files:**
- Runtime output: `data/market-report-finder/kr_2025_annual_download_queue_manifest.json`
- Runtime output: `data/market-report-finder/downloads/KR/**/2025/年报/*.pdf`
- Runtime output if parser runs: `data/pdf-parser/results/<task_id>/*.json`

**Interfaces:**
- Consumes: `scripts/ops/download_kr_2025_annuals_to_parse_queue.py`.
- Produces: 30 KR annual PDFs present locally, or a manifest with explicit shortfall reasons.

- [ ] **Step 1: Run download-only first**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/ops/download_kr_2025_annuals_to_parse_queue.py --target-count 30 --report-year 2025 --download-only
```

Expected:

- Exit code `0` if all 30 are downloaded or already present.
- Exit code `2` if DART public did not expose one or more selected reports.
- Manifest exists at `data/market-report-finder/kr_2025_annual_download_queue_manifest.json`.

- [ ] **Step 2: Count downloaded KR annual PDFs**

Run:

```bash
cd /home/maoyd/siq-research-engine
find data/market-report-finder/downloads/KR -type f -name '*.pdf' | wc -l
find data/market-report-finder/downloads/KR -mindepth 1 -maxdepth 1 -type d | wc -l
```

Expected: both counts are at least `30`. If either count is below `30`, inspect the manifest `skipped` list and record the exact company tickers and reasons.

- [ ] **Step 3: Enqueue parser tasks if the parser is reachable**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/ops/download_kr_2025_annuals_to_parse_queue.py --target-count 30 --report-year 2025
```

Expected:

- Existing first 10 are reported as `already_in_queue` or `queued`.
- Newly downloaded PDFs are reported as `queued` or `already_in_queue`.
- If the parser token or service is unavailable, do not rerun destructive commands; keep the download-only manifest as the completed download evidence and report the parser enqueue blocker.

- [ ] **Step 4: Summarize manifest status**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 - <<'PY'
import json
from collections import Counter
from pathlib import Path

path = Path("data/market-report-finder/kr_2025_annual_download_queue_manifest.json")
data = json.loads(path.read_text(encoding="utf-8"))
statuses = Counter(item.get("status", "unknown") for item in data.get("items", []))
skips = Counter(item.get("status", "unknown") for item in data.get("skipped", []))
print("items", dict(statuses))
print("skipped", dict(skips))
for item in data.get("skipped", []):
    seed = item.get("seed", {})
    print(seed.get("ticker"), seed.get("name"), item.get("status"), item.get("reason"))
PY
```

Expected: `items` total is `30`. If there are skipped items, each has a clear `status` and `reason`.

- [ ] **Step 5: Final verification test suite**

Run:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv run pytest tests/test_kr_catalog.py tests/test_dart_client.py tests/test_downloader.py -q

cd /home/maoyd/siq-research-engine
python3 -m pytest scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py -q
```

Expected: all tests PASS.

- [ ] **Step 6: Commit any final code-only adjustments**

If Task 5 required code fixes, commit only code and tests:

```bash
cd /home/maoyd/siq-research-engine
git add services/market-report-finder/src/market_report_finder_service/markets/kr/catalog.py services/market-report-finder/tests/test_kr_catalog.py scripts/ops/download_kr_2025_annuals_to_parse_queue.py scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py
git commit -m "fix(kr): harden 30 company annual download flow"
```

Do not commit downloaded PDFs or parser result data unless the repository already tracks those data artifacts and the user explicitly asks for data commits.

---

