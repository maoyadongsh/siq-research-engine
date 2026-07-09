# PDF Parser Wiki-Ready Backtest

- Generated at: `2026-07-08T02:37:25Z`
- Markets: `HK`
- Total: `50`
- Wiki ready: `50`
- Not ready: `0`

## By Market

| Market | Total | Wiki ready | Not ready | Warnings |
| --- | ---: | ---: | ---: | ---: |
| HK | 50 | 50 | 0 | 45 |

## Blockers

| Code | Count |
| --- | ---: |
| _none_ | 0 |

## Warnings

| Code | Count |
| --- | ---: |
| `financial_check_warning` | 45 |

## Market Profiles

| Market | Profile | Sprawl limits | Unclassified ratio limit | Notes |
| --- | --- | --- | ---: | --- |
| HK | `hkex_pdf_wiki_ready_v1` | balance_sheet=680, income_statement=380, cash_flow_statement=140 | None | HK annual reports include industrial, bank, insurance, and US-style issuers; core readiness accepts broader HKFRS/IFRS balance-sheet and cash-flow anchors.<br>A statement with no mapped cash-flow facts remains a blocker. |

## Statement Quality Profile

| Market | Statement | Items p50/p90/max | Source tables p50/p90/max | Unclassified source ratio p50/p90/max |
| --- | --- | ---: | ---: | ---: |
| HK | `balance_sheet` | 34/170/677 | 2/18/49 | 0.0/0.0/0.0 |
| HK | `cash_flow_statement` | 14/22/48 | 2/4/6 | 0.0/0.0/0.0 |
| HK | `income_statement` | 68/167/350 | 5/17/29 | 0.0/0.0/0.0 |

## Warning Samples

| Market | Task | Company | Warning samples |
| --- | --- | --- | --- |
| HK | `0194d711-8173-46cb-ba91-192ea3c13746` | HAIDILAO | `financial_check_warning`: {"total": 377, "pass": 325, "fail": 0, "warning": 7, "skipped": 45} |
| HK | `0361fdc1-43a3-4178-9167-35bd6340f8d2` | HUA HONG GRACE | `financial_check_warning`: {"total": 223, "pass": 191, "fail": 0, "warning": 5, "skipped": 27} |
| HK | `09b433d8-1ffb-43a6-82c3-0955bde241aa` | CHINA UNICOM | `financial_check_warning`: {"total": 91, "pass": 75, "fail": 0, "warning": 2, "skipped": 14} |
| HK | `0b8d4d2e-32f0-4ce7-909b-4c74456a1cbb` | NTES S | `financial_check_warning`: {"total": 196, "pass": 172, "fail": 0, "warning": 4, "skipped": 20} |
| HK | `0cbb79fa-0701-40c3-9178-f42490fc2ddf` | BABA W | `financial_check_warning`: {"total": 360, "pass": 278, "fail": 0, "warning": 17, "skipped": 65} |
| HK | `0fc7b34e-da9d-4d6d-803a-d94475a5d264` | CHINA SHENHUA | `financial_check_warning`: {"total": 99, "pass": 82, "fail": 0, "warning": 7, "skipped": 10} |
| HK | `11155940-55ec-4654-9327-4f3e5ad0300b` | TECHTRONIC IND | `financial_check_warning`: {"total": 143, "pass": 96, "fail": 0, "warning": 13, "skipped": 34} |
| HK | `24039b93-d3e3-4a29-a39f-7bea0b5b7d3a` | HSBC HOLDINGS | `financial_check_warning`: {"total": 592, "pass": 530, "fail": 0, "warning": 7, "skipped": 55} |
| HK | `2682411f-b78e-4181-b8ed-f934e0313af1` | HAIER SMARTHOME | `financial_check_warning`: {"total": 188, "pass": 153, "fail": 0, "warning": 12, "skipped": 23} |
| HK | `270b4195-8b66-4b9c-a059-64d17389c086` | WUXI APPTEC | `financial_check_warning`: {"total": 245, "pass": 196, "fail": 0, "warning": 16, "skipped": 33} |
| HK | `274cf782-b04e-4c27-a549-d8bde87cff7b` | TSINGTAO BREW | `financial_check_warning`: {"total": 547, "pass": 494, "fail": 0, "warning": 16, "skipped": 37} |
| HK | `3086b12e-24d3-44da-ade6-43e0c04ec76e` | SINOPEC CORP | `financial_check_warning`: {"total": 1085, "pass": 1070, "fail": 0, "warning": 2, "skipped": 13} |
| HK | `362176b2-5a57-441d-9191-e060618a3a70` | LI AUTO W | `financial_check_warning`: {"total": 152, "pass": 140, "fail": 0, "warning": 4, "skipped": 8} |
| HK | `437602aa-82b7-4d8f-a181-4b4f2e8ad0ac` | POWER ASSETS | `financial_check_warning`: {"total": 69, "pass": 45, "fail": 0, "warning": 8, "skipped": 16} |
| HK | `4c4f0281-34a2-4e0e-9ee2-e4b6bb6b2163` | BANK OF CHINA | `financial_check_warning`: {"total": 76, "pass": 61, "fail": 0, "warning": 3, "skipped": 12} |
| HK | `50090c9f-a424-4d73-b28c-96fa60dd99ff` | LINK REIT | `financial_check_warning`: {"total": 588, "pass": 433, "fail": 0, "warning": 36, "skipped": 119} |
| HK | `51529553-a60a-46db-96e9-e0b9182e4d35` | PETROCHINA | `financial_check_warning`: {"total": 486, "pass": 451, "fail": 0, "warning": 7, "skipped": 28} |
| HK | `65ecbdab-e0e1-4ac2-b6d1-62230a20f002` | HKEX | `financial_check_warning`: {"total": 237, "pass": 223, "fail": 0, "warning": 2, "skipped": 12} |
| HK | `6c7621f4-ad04-4643-b6fd-5ad189bffeb0` | SMIC | `financial_check_warning`: {"total": 186, "pass": 153, "fail": 0, "warning": 6, "skipped": 27} |
| HK | `6d186b13-c8fd-4aa8-a7d7-a81592def6a9` | SBP GROUP | `financial_check_warning`: {"total": 232, "pass": 187, "fail": 0, "warning": 20, "skipped": 25} |
| HK | `6e60e03f-997c-4ab9-9aaa-92553b8fa2bc` | KUAISHOU W | `financial_check_warning`: {"total": 165, "pass": 155, "fail": 0, "warning": 2, "skipped": 8} |
| HK | `722dc491-c9a8-4764-92de-6b62cae028b3` | BEONE MEDICINES | `financial_check_warning`: {"total": 96, "pass": 82, "fail": 0, "warning": 4, "skipped": 10} |
| HK | `75793460-a52e-46ef-ae3f-3925e5b4d6af` | SHK PPT | `financial_check_warning`: {"total": 566, "pass": 498, "fail": 0, "warning": 14, "skipped": 54} |
| HK | `7d6039b8-5868-4953-bf48-1ddb6b9bdfc2` | CNOOC | `financial_check_warning`: {"total": 214, "pass": 180, "fail": 0, "warning": 7, "skipped": 27} |
| HK | `83e1c9b7-4c18-4e7a-8c1f-f4bdca660f8b` | CHINA TELECOM | `financial_check_warning`: {"total": 199, "pass": 153, "fail": 0, "warning": 7, "skipped": 39} |
| HK | `8cc50e40-074f-4a3c-b032-c020f0efb5cc` | CHINA MOBILE | `financial_check_warning`: {"total": 158, "pass": 113, "fail": 0, "warning": 7, "skipped": 38} |
| HK | `8dc6a8c9-f92e-4621-bc9c-3b4d48f06a1c` | CSPC PHARMA | `financial_check_warning`: {"total": 469, "pass": 435, "fail": 0, "warning": 7, "skipped": 27} |
| HK | `8f2e1192-5261-4ae2-805b-cb5969535d48` | ABC | `financial_check_warning`: {"total": 77, "pass": 60, "fail": 0, "warning": 3, "skipped": 14} |
| HK | `9aecfb55-5069-47b1-8383-47cb118b0b16` | TENCENT | `financial_check_warning`: {"total": 210, "pass": 169, "fail": 0, "warning": 6, "skipped": 35} |
| HK | `a397cb1f-be46-4921-a750-795cadc99fa3` | NONGFU SPRING | `financial_check_warning`: {"total": 186, "pass": 145, "fail": 0, "warning": 9, "skipped": 32} |
| HK | `a958b888-ac94-478f-95f0-1f2a5ac71af3` | CHINA TOWER | `financial_check_warning`: {"total": 160, "pass": 121, "fail": 0, "warning": 9, "skipped": 30} |
| HK | `aaba3271-6f9b-44b5-be92-ed926a6cb43d` | CRRC | `financial_check_warning`: {"total": 227, "pass": 190, "fail": 0, "warning": 5, "skipped": 32} |
| HK | `ae02a926-930e-4e50-b753-8e54578b8798` | INNOVENT BIO | `financial_check_warning`: {"total": 78, "pass": 58, "fail": 0, "warning": 6, "skipped": 14} |
| HK | `affcc063-56bc-4242-a06d-1252781fa1d0` | BYD COMPANY | `financial_check_warning`: {"total": 231, "pass": 183, "fail": 0, "warning": 12, "skipped": 36} |
| HK | `b78fbbbe-d542-4820-b7f6-fbe9e372f645` | ICBC | `financial_check_warning`: {"total": 97, "pass": 82, "fail": 0, "warning": 3, "skipped": 12} |
| HK | `c777ee77-b2a6-459d-b366-668a3a7b755a` | JIANGXI COPPER | `financial_check_warning`: {"total": 270, "pass": 233, "fail": 0, "warning": 5, "skipped": 32} |
| HK | `d25426c7-bc1e-4f39-a0ec-a8d7a5d43d6a` | SUNNY OPTICAL | `financial_check_warning`: {"total": 179, "pass": 133, "fail": 0, "warning": 12, "skipped": 34} |
| HK | `d3c23ef3-d2e1-4be0-a362-541f0d8827f8` | CHINA RES BEER | `financial_check_warning`: {"total": 103, "pass": 87, "fail": 0, "warning": 8, "skipped": 8} |
| HK | `d872c0b8-30d4-4999-9765-0235e870f2ca` | PICC P&C | `financial_check_warning`: {"total": 94, "pass": 83, "fail": 0, "warning": 1, "skipped": 10} |
| HK | `dab19462-22ce-45eb-99bf-cbfb0a879210` | BIDU SW | `financial_check_warning`: {"total": 309, "pass": 263, "fail": 0, "warning": 14, "skipped": 32} |
| HK | `dd73d6f3-1a2d-4ce5-aebd-117111e50fd5` | GEELY AUTO | `financial_check_warning`: {"total": 173, "pass": 129, "fail": 0, "warning": 7, "skipped": 37} |
| HK | `e89555a9-359e-4d96-9737-198694c5a402` | BOC HONG KONG | `financial_check_warning`: {"total": 81, "pass": 62, "fail": 0, "warning": 3, "skipped": 16} |
| HK | `f877c0f9-f2a7-4b13-99fa-8b2d507b1d70` | JD SW | `financial_check_warning`: {"total": 119, "pass": 90, "fail": 0, "warning": 4, "skipped": 25} |
| HK | `faead375-c944-4e2d-9ab9-c3dd61573410` | PING AN | `financial_check_warning`: {"total": 157, "pass": 141, "fail": 0, "warning": 2, "skipped": 14} |
| HK | `ff6651f7-18be-4cea-aaf5-6d3318c4798a` | CM BANK | `financial_check_warning`: {"total": 74, "pass": 52, "fail": 0, "warning": 6, "skipped": 16} |
