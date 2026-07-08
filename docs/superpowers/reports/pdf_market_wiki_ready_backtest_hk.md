# PDF Parser Wiki-Ready Backtest

- Generated at: `2026-07-08T02:15:19Z`
- Markets: `HK`
- Total: `50`
- Wiki ready: `49`
- Not ready: `1`

## By Market

| Market | Total | Wiki ready | Not ready | Warnings |
| --- | ---: | ---: | ---: | ---: |
| HK | 50 | 49 | 1 | 41 |

## Blockers

| Code | Count |
| --- | ---: |
| `core_statement_canonical_missing` | 1 |

## Warnings

| Code | Count |
| --- | ---: |
| `financial_check_warning` | 41 |

## Market Profiles

| Market | Profile | Sprawl limits | Unclassified ratio limit | Notes |
| --- | --- | --- | ---: | --- |
| HK | `hkex_pdf_wiki_ready_v1` | balance_sheet=680, income_statement=380, cash_flow_statement=140 | None | HK annual reports include industrial, bank, insurance, and US-style issuers; core readiness accepts broader HKFRS/IFRS balance-sheet and cash-flow anchors.<br>A statement with no mapped cash-flow facts remains a blocker. |

## Statement Quality Profile

| Market | Statement | Items p50/p90/max | Source tables p50/p90/max | Unclassified source ratio p50/p90/max |
| --- | --- | ---: | ---: | ---: |
| HK | `balance_sheet` | 31/153/619 | 2/16/49 | 0.0/0.0/0.0 |
| HK | `cash_flow_statement` | 14/20/48 | 2/4/7 | 0.0/0.0/0.0 |
| HK | `income_statement` | 26/97/350 | 4/16/29 | 0.0/0.0/0.0 |

## Not Ready Items

| Market | Task | Company | Blockers |
| --- | --- | --- | --- |
| HK | `f877c0f9-f2a7-4b13-99fa-8b2d507b1d70` | JD SW | `core_statement_canonical_missing` |

## Warning Samples

| Market | Task | Company | Warning samples |
| --- | --- | --- | --- |
| HK | `0194d711-8173-46cb-ba91-192ea3c13746` | HAIDILAO | `financial_check_warning`: {"total": 359, "pass": 307, "fail": 0, "warning": 7, "skipped": 45} |
| HK | `0361fdc1-43a3-4178-9167-35bd6340f8d2` | HUA HONG GRACE | `financial_check_warning`: {"total": 183, "pass": 149, "fail": 0, "warning": 5, "skipped": 29} |
| HK | `09b433d8-1ffb-43a6-82c3-0955bde241aa` | CHINA UNICOM | `financial_check_warning`: {"total": 89, "pass": 73, "fail": 0, "warning": 2, "skipped": 14} |
| HK | `0b8d4d2e-32f0-4ce7-909b-4c74456a1cbb` | NTES S | `financial_check_warning`: {"total": 126, "pass": 100, "fail": 0, "warning": 4, "skipped": 22} |
| HK | `0cbb79fa-0701-40c3-9178-f42490fc2ddf` | BABA W | `financial_check_warning`: {"total": 128, "pass": 86, "fail": 0, "warning": 12, "skipped": 30} |
| HK | `0fc7b34e-da9d-4d6d-803a-d94475a5d264` | CHINA SHENHUA | `financial_check_warning`: {"total": 85, "pass": 67, "fail": 0, "warning": 8, "skipped": 10} |
| HK | `11155940-55ec-4654-9327-4f3e5ad0300b` | TECHTRONIC IND | `financial_check_warning`: {"total": 85, "pass": 66, "fail": 0, "warning": 9, "skipped": 10} |
| HK | `24039b93-d3e3-4a29-a39f-7bea0b5b7d3a` | HSBC HOLDINGS | `financial_check_warning`: {"total": 562, "pass": 499, "fail": 0, "warning": 7, "skipped": 56} |
| HK | `2682411f-b78e-4181-b8ed-f934e0313af1` | HAIER SMARTHOME | `financial_check_warning`: {"total": 115, "pass": 107, "fail": 0, "warning": 6, "skipped": 2} |
| HK | `270b4195-8b66-4b9c-a059-64d17389c086` | WUXI APPTEC | `financial_check_warning`: {"total": 140, "pass": 124, "fail": 0, "warning": 10, "skipped": 6} |
| HK | `274cf782-b04e-4c27-a549-d8bde87cff7b` | TSINGTAO BREW | `financial_check_warning`: {"total": 515, "pass": 462, "fail": 0, "warning": 16, "skipped": 37} |
| HK | `3086b12e-24d3-44da-ade6-43e0c04ec76e` | SINOPEC CORP | `financial_check_warning`: {"total": 1011, "pass": 996, "fail": 0, "warning": 2, "skipped": 13} |
| HK | `362176b2-5a57-441d-9191-e060618a3a70` | LI AUTO W | `financial_check_warning`: {"total": 86, "pass": 68, "fail": 0, "warning": 8, "skipped": 10} |
| HK | `437602aa-82b7-4d8f-a181-4b4f2e8ad0ac` | POWER ASSETS | `financial_check_warning`: {"total": 69, "pass": 45, "fail": 0, "warning": 8, "skipped": 16} |
| HK | `4c4f0281-34a2-4e0e-9ee2-e4b6bb6b2163` | BANK OF CHINA | `financial_check_warning`: {"total": 76, "pass": 61, "fail": 0, "warning": 3, "skipped": 12} |
| HK | `50090c9f-a424-4d73-b28c-96fa60dd99ff` | LINK REIT | `financial_check_warning`: {"total": 542, "pass": 387, "fail": 0, "warning": 36, "skipped": 119} |
| HK | `51529553-a60a-46db-96e9-e0b9182e4d35` | PETROCHINA | `financial_check_warning`: {"total": 401, "pass": 392, "fail": 0, "warning": 5, "skipped": 4} |
| HK | `65ecbdab-e0e1-4ac2-b6d1-62230a20f002` | HKEX | `financial_check_warning`: {"total": 209, "pass": 189, "fail": 0, "warning": 6, "skipped": 14} |
| HK | `6d186b13-c8fd-4aa8-a7d7-a81592def6a9` | SBP GROUP | `financial_check_warning`: {"total": 114, "pass": 92, "fail": 0, "warning": 16, "skipped": 6} |
| HK | `6e60e03f-997c-4ab9-9aaa-92553b8fa2bc` | KUAISHOU W | `financial_check_warning`: {"total": 91, "pass": 81, "fail": 0, "warning": 2, "skipped": 8} |
| HK | `722dc491-c9a8-4764-92de-6b62cae028b3` | BEONE MEDICINES | `financial_check_warning`: {"total": 89, "pass": 75, "fail": 0, "warning": 4, "skipped": 10} |
| HK | `75793460-a52e-46ef-ae3f-3925e5b4d6af` | SHK PPT | `financial_check_warning`: {"total": 515, "pass": 447, "fail": 0, "warning": 14, "skipped": 54} |
| HK | `7d6039b8-5868-4953-bf48-1ddb6b9bdfc2` | CNOOC | `financial_check_warning`: {"total": 200, "pass": 166, "fail": 0, "warning": 7, "skipped": 27} |
| HK | `83e1c9b7-4c18-4e7a-8c1f-f4bdca660f8b` | CHINA TELECOM | `financial_check_warning`: {"total": 111, "pass": 95, "fail": 0, "warning": 4, "skipped": 12} |
| HK | `8dc6a8c9-f92e-4621-bc9c-3b4d48f06a1c` | CSPC PHARMA | `financial_check_warning`: {"total": 397, "pass": 362, "fail": 0, "warning": 7, "skipped": 28} |
| HK | `8f2e1192-5261-4ae2-805b-cb5969535d48` | ABC | `financial_check_warning`: {"total": 73, "pass": 54, "fail": 0, "warning": 5, "skipped": 14} |
| HK | `a397cb1f-be46-4921-a750-795cadc99fa3` | NONGFU SPRING | `financial_check_warning`: {"total": 166, "pass": 125, "fail": 0, "warning": 9, "skipped": 32} |
| HK | `a958b888-ac94-478f-95f0-1f2a5ac71af3` | CHINA TOWER | `financial_check_warning`: {"total": 97, "pass": 89, "fail": 0, "warning": 2, "skipped": 6} |
| HK | `aaba3271-6f9b-44b5-be92-ed926a6cb43d` | CRRC | `financial_check_warning`: {"total": 132, "pass": 119, "fail": 0, "warning": 5, "skipped": 8} |
| HK | `ae02a926-930e-4e50-b753-8e54578b8798` | INNOVENT BIO | `financial_check_warning`: {"total": 78, "pass": 58, "fail": 0, "warning": 6, "skipped": 14} |
| HK | `affcc063-56bc-4242-a06d-1252781fa1d0` | BYD COMPANY | `financial_check_warning`: {"total": 210, "pass": 166, "fail": 0, "warning": 6, "skipped": 38} |
| HK | `b78fbbbe-d542-4820-b7f6-fbe9e372f645` | ICBC | `financial_check_warning`: {"total": 97, "pass": 82, "fail": 0, "warning": 3, "skipped": 12} |
| HK | `c777ee77-b2a6-459d-b366-668a3a7b755a` | JIANGXI COPPER | `financial_check_warning`: {"total": 205, "pass": 168, "fail": 0, "warning": 5, "skipped": 32} |
| HK | `d25426c7-bc1e-4f39-a0ec-a8d7a5d43d6a` | SUNNY OPTICAL | `financial_check_warning`: {"total": 101, "pass": 85, "fail": 0, "warning": 6, "skipped": 10} |
| HK | `d3c23ef3-d2e1-4be0-a362-541f0d8827f8` | CHINA RES BEER | `financial_check_warning`: {"total": 94, "pass": 78, "fail": 0, "warning": 8, "skipped": 8} |
| HK | `dab19462-22ce-45eb-99bf-cbfb0a879210` | BIDU SW | `financial_check_warning`: {"total": 122, "pass": 96, "fail": 0, "warning": 6, "skipped": 20} |
| HK | `dd73d6f3-1a2d-4ce5-aebd-117111e50fd5` | GEELY AUTO | `financial_check_warning`: {"total": 119, "pass": 75, "fail": 0, "warning": 7, "skipped": 37} |
| HK | `e89555a9-359e-4d96-9737-198694c5a402` | BOC HONG KONG | `financial_check_warning`: {"total": 81, "pass": 62, "fail": 0, "warning": 3, "skipped": 16} |
| HK | `f877c0f9-f2a7-4b13-99fa-8b2d507b1d70` | JD SW | `financial_check_warning`: {"total": 87, "pass": 57, "fail": 0, "warning": 5, "skipped": 25} |
| HK | `faead375-c944-4e2d-9ab9-c3dd61573410` | PING AN | `financial_check_warning`: {"total": 97, "pass": 81, "fail": 0, "warning": 2, "skipped": 14} |
| HK | `ff6651f7-18be-4cea-aaf5-6d3318c4798a` | CM BANK | `financial_check_warning`: {"total": 74, "pass": 52, "fail": 0, "warning": 6, "skipped": 16} |
