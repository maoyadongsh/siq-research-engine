# PDF Parser Wiki-Ready Backtest

- Generated at: `2026-07-08T02:07:01Z`
- Markets: `CN, EU, HK`
- Total: `181`
- Wiki ready: `150`
- Not ready: `31`

## By Market

| Market | Total | Wiki ready | Not ready | Warnings |
| --- | ---: | ---: | ---: | ---: |
| CN | 72 | 66 | 6 | 18 |
| EU | 59 | 37 | 22 | 39 |
| HK | 50 | 47 | 3 | 41 |

## Blockers

| Code | Count |
| --- | ---: |
| `core_statement_canonical_missing` | 33 |
| `financial_check_fail` | 1 |
| `metadata_identity_incomplete` | 2 |
| `quality_financial_fail` | 1 |
| `wiki_payload_minimum_missing` | 2 |

## Warnings

| Code | Count |
| --- | ---: |
| `core_statement_canonical_missing_non_annual` | 2 |
| `core_statements_missing_non_annual` | 1 |
| `financial_check_warning` | 61 |
| `formal_statement_signal_not_found` | 19 |
| `statement_evidence_ratio_low` | 10 |
| `statement_item_sprawl` | 1 |
| `suspicious_statement_title` | 4 |

## Market Profiles

| Market | Profile | Sprawl limits | Unclassified ratio limit | Notes |
| --- | --- | --- | ---: | --- |
| CN | `generic_pdf_wiki_ready_v1` | balance_sheet=320, income_statement=220, cash_flow_statement=120 | None | Common parser result contract and conservative statement quality checks. |
| EU | `eu_ifrs_pdf_wiki_ready_v1` | balance_sheet=120, income_statement=120, cash_flow_statement=80 | None | EU IFRS issuers vary by industry; readiness uses broad IFRS anchors and keeps empty cash-flow extraction as a blocker.<br>Small canonical fact sets are expected from the current EU profile and should be improved by extractor work, not by generic A-share thresholds. |
| HK | `hkex_pdf_wiki_ready_v1` | balance_sheet=680, income_statement=380, cash_flow_statement=140 | None | HK annual reports include industrial, bank, insurance, and US-style issuers; core readiness accepts broader HKFRS/IFRS balance-sheet and cash-flow anchors.<br>A statement with no mapped cash-flow facts remains a blocker. |

## Statement Quality Profile

| Market | Statement | Items p50/p90/max | Source tables p50/p90/max | Unclassified source ratio p50/p90/max |
| --- | --- | ---: | ---: | ---: |
| CN | `balance_sheet` | 92/116/167 | 2/5/10 | 0.0/0.0/0.0 |
| CN | `cash_flow_statement` | 67/89/125 | 3/4/8 | 0.0/0.0/0.0 |
| CN | `income_statement` | 61/75/112 | 2/4/10 | 0.0/0.0/0.0 |
| EU | `balance_sheet` | 4/6/7 | 5/14/26 | 0.0/0.0/0.0 |
| EU | `cash_flow_statement` | 2/5/9 | 4/8/12 | 0.0/0.0/0.0 |
| EU | `income_statement` | 5/8/11 | 5/18/26 | 0.0/0.0/0.0 |
| HK | `balance_sheet` | 31/153/619 | 2/16/49 | 0.0/0.0/0.0 |
| HK | `cash_flow_statement` | 14/20/48 | 2/4/7 | 0.0/0.0/0.0 |
| HK | `income_statement` | 26/97/350 | 4/16/29 | 0.0/0.0/0.0 |

## Not Ready Items

| Market | Task | Company | Blockers |
| --- | --- | --- | --- |
| EU | `0227f921-3412-433a-890e-862c31c57503` | ASML Holding N.V | `core_statement_canonical_missing` |
| EU | `1578ab65-3fa7-4861-93aa-d0d9799864dc` | Sanofi | `core_statement_canonical_missing` |
| EU | `1d13bb90-9ead-481a-a37a-5baf775904a5` | Sanofi | `core_statement_canonical_missing` |
| EU | `236c3e23-16b4-4e3b-aba5-59f3808c70fb` | Deutsche Telekom AG | `core_statement_canonical_missing`, `core_statement_canonical_missing` |
| EU | `328fb3b5-6df1-4095-b5bc-afe7e0b0dc49` | Infineon Technologies AG | `core_statement_canonical_missing` |
| EU | `348ae6fe-7871-4fda-ad41-28e0666a466d` | Deutsche Boerse AG | `core_statement_canonical_missing` |
| CN | `3a4cf3fa-b62f-48fa-8003-fe1b8cc217a4` | 华泰证券 | `core_statement_canonical_missing` |
| EU | `4f21a4d2-001f-490b-8962-1ff18cd4331a` | L'Oreal S.A | `core_statement_canonical_missing` |
| EU | `4ff80ce3-867d-4b9b-8b8c-8142339a5b01` | Bayerische Motoren Werke Aktiengesellschaft | `core_statement_canonical_missing` |
| EU | `6ee5e762-1865-42bb-88ef-2cc841bbe514` | Volkswagen AG | `core_statement_canonical_missing` |
| EU | `79cf0438-d03f-4fbb-bdae-591767d89346` | ASML Holding N.V | `core_statement_canonical_missing` |
| EU | `81b8bcfb-1153-433d-8862-fcb30a66f058` | Muenchener Rueckversicherungs Gesellschaft Aktiengesellschaft in Muenchen | `core_statement_canonical_missing`, `core_statement_canonical_missing` |
| EU | `85492de8-2f0c-4028-931a-bb943724fad4` | Zurich Insurance Group AG | `core_statement_canonical_missing` |
| EU | `916f5bfe-f8f6-4e25-b890-2065692c9375` | TotalEnergies SE | `core_statement_canonical_missing` |
| EU | `9ee5c8aa-227d-4ca6-b277-52232d1cd57b` | TotalEnergies SE | `core_statement_canonical_missing` |
| CN | `a2cffc9b-e992-4b8f-ac34-376d61e5a52d` | 国电南瑞 | `core_statement_canonical_missing` |
| HK | `ae02a926-930e-4e50-b753-8e54578b8798` | INNOVENT BIO | `core_statement_canonical_missing` |
| EU | `ae75fd38-59f6-41d8-af56-d6cbc2285332` | Koninklijke Philips N.V | `core_statement_canonical_missing` |
| EU | `b7e7e13a-2fbc-4de3-b5a4-121e451d306c` | Danone | `core_statement_canonical_missing`, `core_statement_canonical_missing` |
| EU | `bae77e44-68db-42fd-93d9-cd2c05ab73bb` | Givaudan SA | `core_statement_canonical_missing` |
| EU | `be842173-7ae0-4604-8dfc-edde29ba6b95` | Deutsche Telekom AG | `core_statement_canonical_missing`, `core_statement_canonical_missing` |
| EU | `c9a02ec0-e41a-401c-9afa-a57215cc2c9b` | Swiss Re Ltd | `core_statement_canonical_missing` |
| CN | `dd0ad35e-42e5-4145-b42c-21757ae453f8` | 紫金矿业 | `financial_check_fail`, `quality_financial_fail` |
| EU | `ddcb5cbb-2b82-497b-a9f6-f578d379393d` | Koninklijke Philips N.V | `core_statement_canonical_missing` |
| EU | `dedda13a-f79e-4e80-a5ed-393aa554c1fe` | ING Groep N.V | `core_statement_canonical_missing` |
| CN | `doc-19a7b066-289b-45ac-9816-9097f2369396` | 安纳达 | `metadata_identity_incomplete`, `wiki_payload_minimum_missing` |
| CN | `doc-2b786280-3f3c-4afc-8f42-ddb568a7fffb` | ST绿康 | `metadata_identity_incomplete`, `wiki_payload_minimum_missing` |
| EU | `e4ac2393-1efa-4599-a32e-cfc44a76ed96` | LVMH Moet Hennessy Louis Vuitton SE | `core_statement_canonical_missing` |
| HK | `f877c0f9-f2a7-4b13-99fa-8b2d507b1d70` | JD SW | `core_statement_canonical_missing`, `core_statement_canonical_missing` |
| CN | `fc9f2535-e66f-440b-aa53-a6ed1782fd68` | 海天味业 | `core_statement_canonical_missing` |
| HK | `ff6651f7-18be-4cea-aaf5-6d3318c4798a` | CM BANK | `core_statement_canonical_missing` |

## Warning Samples

| Market | Task | Company | Warning samples |
| --- | --- | --- | --- |
| HK | `0194d711-8173-46cb-ba91-192ea3c13746` | HAIDILAO | `financial_check_warning`: {"total": 359, "pass": 307, "fail": 0, "warning": 7, "skipped": 45} |
| EU | `0227f921-3412-433a-890e-862c31c57503` | ASML Holding N.V | `suspicious_statement_title`: {"statement_type": "income_statement", "title": "Operating results of 2025 compared to 2024"}<br>`financial_check_warning`: {"total": 23, "pass": 6, "fail": 0, "warning": 3, "skipped": 14} |
| HK | `0361fdc1-43a3-4178-9167-35bd6340f8d2` | HUA HONG GRACE | `financial_check_warning`: {"total": 183, "pass": 149, "fail": 0, "warning": 5, "skipped": 29} |
| CN | `0581d420-b435-4d0c-b077-5f689dd9db7e` | 迈瑞医疗 | `formal_statement_signal_not_found`: {"statement_type": "balance_sheet"}<br>`formal_statement_signal_not_found`: {"statement_type": "income_statement"} |
| EU | `07499280-054d-4abf-986d-c17424ea08d9` | VINCI SA | `financial_check_warning`: {"total": 15, "pass": 4, "fail": 0, "warning": 2, "skipped": 9} |
| EU | `079842ed-8f7d-4232-95f3-e79a1c2c4871` | Geberit AG | `formal_statement_signal_not_found`: {"statement_type": "cash_flow_statement"} |
| EU | `0912c5f9-d1e3-4dd8-adf1-295126e71917` | HSBC Holdings plc | `financial_check_warning`: {"total": 29, "pass": 4, "fail": 0, "warning": 1, "skipped": 24} |
| HK | `09b433d8-1ffb-43a6-82c3-0955bde241aa` | CHINA UNICOM | `financial_check_warning`: {"total": 89, "pass": 73, "fail": 0, "warning": 2, "skipped": 14} |
| HK | `0b8d4d2e-32f0-4ce7-909b-4c74456a1cbb` | NTES S | `financial_check_warning`: {"total": 126, "pass": 100, "fail": 0, "warning": 4, "skipped": 22} |
| HK | `0cbb79fa-0701-40c3-9178-f42490fc2ddf` | BABA W | `financial_check_warning`: {"total": 128, "pass": 86, "fail": 0, "warning": 12, "skipped": 30} |
| HK | `0fc7b34e-da9d-4d6d-803a-d94475a5d264` | CHINA SHENHUA | `financial_check_warning`: {"total": 85, "pass": 67, "fail": 0, "warning": 8, "skipped": 10} |
| HK | `11155940-55ec-4654-9327-4f3e5ad0300b` | TECHTRONIC IND | `financial_check_warning`: {"total": 85, "pass": 66, "fail": 0, "warning": 9, "skipped": 10} |
| EU | `1301e4a8-c867-48d0-bdd6-8449d6c5f124` | Prosus N.V | `financial_check_warning`: {"total": 15, "pass": 3, "fail": 0, "warning": 2, "skipped": 10} |
| EU | `1578ab65-3fa7-4861-93aa-d0d9799864dc` | Sanofi | `statement_evidence_ratio_low`: {"statement_type": "cash_flow_statement", "ratio": 0.0}<br>`financial_check_warning`: {"total": 15, "pass": 3, "fail": 0, "warning": 1, "skipped": 11} |
| EU | `1d13bb90-9ead-481a-a37a-5baf775904a5` | Sanofi | `statement_evidence_ratio_low`: {"statement_type": "cash_flow_statement", "ratio": 0.0}<br>`financial_check_warning`: {"total": 15, "pass": 3, "fail": 0, "warning": 1, "skipped": 11} |
| HK | `24039b93-d3e3-4a29-a39f-7bea0b5b7d3a` | HSBC HOLDINGS | `financial_check_warning`: {"total": 562, "pass": 499, "fail": 0, "warning": 7, "skipped": 56} |
| HK | `2682411f-b78e-4181-b8ed-f934e0313af1` | HAIER SMARTHOME | `financial_check_warning`: {"total": 115, "pass": 107, "fail": 0, "warning": 6, "skipped": 2} |
| EU | `26ba3f60-3dc3-4834-bbc3-da0585fc46bc` | Heineken N.V | `formal_statement_signal_not_found`: {"statement_type": "income_statement"}<br>`financial_check_warning`: {"total": 21, "pass": 3, "fail": 0, "warning": 2, "skipped": 16} |
| HK | `270b4195-8b66-4b9c-a059-64d17389c086` | WUXI APPTEC | `financial_check_warning`: {"total": 140, "pass": 124, "fail": 0, "warning": 10, "skipped": 6} |
| HK | `274cf782-b04e-4c27-a549-d8bde87cff7b` | TSINGTAO BREW | `financial_check_warning`: {"total": 515, "pass": 462, "fail": 0, "warning": 16, "skipped": 37} |
| CN | `2e358b27-cd33-4fa2-ae4d-0be20ad4cc6c` | 上海银行 | `core_statements_missing_non_annual`: ["income_statement"]<br>`core_statement_canonical_missing_non_annual`: {"statement_type": "balance_sheet", "missing_groups": [["net_assets", "total_equity"]]}<br>`core_statement_canonical_missing_non_annual`: {"statement_type": "cash_flow_statement", "missing_groups": [["cash_generated_from_operations", "operating_cash_flow_net"]]}<br>`formal_statement_signal_not_found`: {"statement_type": "cash_flow_statement"} |
| HK | `3086b12e-24d3-44da-ade6-43e0c04ec76e` | SINOPEC CORP | `financial_check_warning`: {"total": 1011, "pass": 996, "fail": 0, "warning": 2, "skipped": 13} |
| EU | `348ae6fe-7871-4fda-ad41-28e0666a466d` | Deutsche Boerse AG | `statement_evidence_ratio_low`: {"statement_type": "income_statement", "ratio": 0.0} |
| HK | `362176b2-5a57-441d-9191-e060618a3a70` | LI AUTO W | `financial_check_warning`: {"total": 86, "pass": 68, "fail": 0, "warning": 8, "skipped": 10} |
| HK | `437602aa-82b7-4d8f-a181-4b4f2e8ad0ac` | POWER ASSETS | `financial_check_warning`: {"total": 69, "pass": 45, "fail": 0, "warning": 8, "skipped": 16} |
| HK | `4c4f0281-34a2-4e0e-9ee2-e4b6bb6b2163` | BANK OF CHINA | `financial_check_warning`: {"total": 76, "pass": 61, "fail": 0, "warning": 3, "skipped": 12} |
| EU | `4d6f7e9c-97a2-4cd0-9024-7390bf86bb30` | SAP SE | `financial_check_warning`: {"total": 21, "pass": 5, "fail": 0, "warning": 1, "skipped": 15} |
| EU | `4ff80ce3-867d-4b9b-8b8c-8142339a5b01` | Bayerische Motoren Werke Aktiengesellschaft | `statement_evidence_ratio_low`: {"statement_type": "cash_flow_statement", "ratio": 0.0} |
| HK | `50090c9f-a424-4d73-b28c-96fa60dd99ff` | LINK REIT | `financial_check_warning`: {"total": 542, "pass": 387, "fail": 0, "warning": 36, "skipped": 119} |
| HK | `51529553-a60a-46db-96e9-e0b9182e4d35` | PETROCHINA | `financial_check_warning`: {"total": 401, "pass": 392, "fail": 0, "warning": 5, "skipped": 4} |
| EU | `523b8cf6-f56c-4a53-9577-8d35f569cc09` | AstraZeneca PLC | `financial_check_warning`: {"total": 17, "pass": 6, "fail": 0, "warning": 3, "skipped": 8} |
| HK | `65ecbdab-e0e1-4ac2-b6d1-62230a20f002` | HKEX | `financial_check_warning`: {"total": 209, "pass": 189, "fail": 0, "warning": 6, "skipped": 14} |
| HK | `6d186b13-c8fd-4aa8-a7d7-a81592def6a9` | SBP GROUP | `financial_check_warning`: {"total": 114, "pass": 92, "fail": 0, "warning": 16, "skipped": 6} |
| HK | `6e60e03f-997c-4ab9-9aaa-92553b8fa2bc` | KUAISHOU W | `financial_check_warning`: {"total": 91, "pass": 81, "fail": 0, "warning": 2, "skipped": 8} |
| EU | `70902b2b-99ea-481d-b617-1537c148c147` | SAP SE | `financial_check_warning`: {"total": 21, "pass": 5, "fail": 0, "warning": 1, "skipped": 15} |
| HK | `722dc491-c9a8-4764-92de-6b62cae028b3` | BEONE MEDICINES | `financial_check_warning`: {"total": 89, "pass": 75, "fail": 0, "warning": 4, "skipped": 10} |
| EU | `74a677ea-f9af-4caf-88b0-7fc8f2c1e8b0` | DSM Firmenich AG | `financial_check_warning`: {"total": 21, "pass": 3, "fail": 0, "warning": 2, "skipped": 16} |
| HK | `75793460-a52e-46ef-ae3f-3925e5b4d6af` | SHK PPT | `financial_check_warning`: {"total": 515, "pass": 447, "fail": 0, "warning": 14, "skipped": 54} |
| EU | `79cf0438-d03f-4fbb-bdae-591767d89346` | ASML Holding N.V | `suspicious_statement_title`: {"statement_type": "income_statement", "title": "Operating results of 2025 compared to 2024"}<br>`financial_check_warning`: {"total": 23, "pass": 6, "fail": 0, "warning": 3, "skipped": 14} |
| EU | `7a3cebd5-ef87-46ea-bb12-abe2105a913d` | Novartis AG | `financial_check_warning`: {"total": 19, "pass": 5, "fail": 0, "warning": 2, "skipped": 12} |
| HK | `7d6039b8-5868-4953-bf48-1ddb6b9bdfc2` | CNOOC | `financial_check_warning`: {"total": 200, "pass": 166, "fail": 0, "warning": 7, "skipped": 27} |
| EU | `7ed0a4d3-5b5a-477f-bc4a-7a5c91535a96` | AstraZeneca PLC | `financial_check_warning`: {"total": 17, "pass": 6, "fail": 0, "warning": 3, "skipped": 8} |
| HK | `83e1c9b7-4c18-4e7a-8c1f-f4bdca660f8b` | CHINA TELECOM | `financial_check_warning`: {"total": 111, "pass": 95, "fail": 0, "warning": 4, "skipped": 12} |
| EU | `85492de8-2f0c-4028-931a-bb943724fad4` | Zurich Insurance Group AG | `statement_evidence_ratio_low`: {"statement_type": "cash_flow_statement", "ratio": 0.0} |
| HK | `8dc6a8c9-f92e-4621-bc9c-3b4d48f06a1c` | CSPC PHARMA | `financial_check_warning`: {"total": 397, "pass": 362, "fail": 0, "warning": 7, "skipped": 28} |
| HK | `8f2e1192-5261-4ae2-805b-cb5969535d48` | ABC | `financial_check_warning`: {"total": 73, "pass": 54, "fail": 0, "warning": 5, "skipped": 14} |
| EU | `9b426320-6039-43fd-af8d-3e6d6887dca8` | Diageo plc | `financial_check_warning`: {"total": 19, "pass": 8, "fail": 0, "warning": 2, "skipped": 9} |
| HK | `a397cb1f-be46-4921-a750-795cadc99fa3` | NONGFU SPRING | `financial_check_warning`: {"total": 166, "pass": 125, "fail": 0, "warning": 9, "skipped": 32} |
| EU | `a47504aa-a322-4b49-9999-d414ace6751f` | Heineken N.V | `formal_statement_signal_not_found`: {"statement_type": "income_statement"}<br>`financial_check_warning`: {"total": 21, "pass": 3, "fail": 0, "warning": 2, "skipped": 16} |
| HK | `a958b888-ac94-478f-95f0-1f2a5ac71af3` | CHINA TOWER | `financial_check_warning`: {"total": 97, "pass": 89, "fail": 0, "warning": 2, "skipped": 6} |
| HK | `aaba3271-6f9b-44b5-be92-ed926a6cb43d` | CRRC | `financial_check_warning`: {"total": 132, "pass": 119, "fail": 0, "warning": 5, "skipped": 8} |
| HK | `ae02a926-930e-4e50-b753-8e54578b8798` | INNOVENT BIO | `financial_check_warning`: {"total": 68, "pass": 47, "fail": 0, "warning": 7, "skipped": 14} |
| EU | `ae75fd38-59f6-41d8-af56-d6cbc2285332` | Koninklijke Philips N.V | `formal_statement_signal_not_found`: {"statement_type": "income_statement"}<br>`statement_evidence_ratio_low`: {"statement_type": "cash_flow_statement", "ratio": 0.0} |
| HK | `affcc063-56bc-4242-a06d-1252781fa1d0` | BYD COMPANY | `financial_check_warning`: {"total": 210, "pass": 166, "fail": 0, "warning": 6, "skipped": 38} |
| CN | `b4e3f1e7-34c7-45df-afd0-c364df9bc306` | 中兴通讯 | `formal_statement_signal_not_found`: {"statement_type": "balance_sheet"}<br>`formal_statement_signal_not_found`: {"statement_type": "income_statement"}<br>`statement_item_sprawl`: {"statement_type": "cash_flow_statement", "item_count": 125, "limit": 120} |
| CN | `b5aa0e46-90e3-4217-aa77-0f02bbd91f60` | 京东方Ａ | `formal_statement_signal_not_found`: {"statement_type": "balance_sheet"}<br>`formal_statement_signal_not_found`: {"statement_type": "income_statement"}<br>`formal_statement_signal_not_found`: {"statement_type": "cash_flow_statement"} |
| HK | `b78fbbbe-d542-4820-b7f6-fbe9e372f645` | ICBC | `financial_check_warning`: {"total": 97, "pass": 82, "fail": 0, "warning": 3, "skipped": 12} |
| EU | `b7e7e13a-2fbc-4de3-b5a4-121e451d306c` | Danone | `statement_evidence_ratio_low`: {"statement_type": "cash_flow_statement", "ratio": 0.0} |
| EU | `bae77e44-68db-42fd-93d9-cd2c05ab73bb` | Givaudan SA | `statement_evidence_ratio_low`: {"statement_type": "cash_flow_statement", "ratio": 0.0} |
| EU | `c660e4cb-8f50-478f-ba92-39de86c5a067` | Novartis AG | `financial_check_warning`: {"total": 19, "pass": 5, "fail": 0, "warning": 2, "skipped": 12} |
| HK | `c777ee77-b2a6-459d-b366-668a3a7b755a` | JIANGXI COPPER | `financial_check_warning`: {"total": 205, "pass": 168, "fail": 0, "warning": 5, "skipped": 32} |
| EU | `c9a02ec0-e41a-401c-9afa-a57215cc2c9b` | Swiss Re Ltd | `statement_evidence_ratio_low`: {"statement_type": "cash_flow_statement", "ratio": 0.0} |
| HK | `d25426c7-bc1e-4f39-a0ec-a8d7a5d43d6a` | SUNNY OPTICAL | `financial_check_warning`: {"total": 101, "pass": 85, "fail": 0, "warning": 6, "skipped": 10} |
| HK | `d3c23ef3-d2e1-4be0-a362-541f0d8827f8` | CHINA RES BEER | `financial_check_warning`: {"total": 94, "pass": 78, "fail": 0, "warning": 8, "skipped": 8} |
| HK | `dab19462-22ce-45eb-99bf-cbfb0a879210` | BIDU SW | `financial_check_warning`: {"total": 122, "pass": 96, "fail": 0, "warning": 6, "skipped": 20} |
| EU | `dab4d056-3c8b-4e7d-8cf8-d46b743ca1bd` | Rio Tinto plc | `financial_check_warning`: {"total": 19, "pass": 5, "fail": 0, "warning": 4, "skipped": 10} |
| CN | `dd0ad35e-42e5-4145-b42c-21757ae453f8` | 紫金矿业 | `formal_statement_signal_not_found`: {"statement_type": "balance_sheet"}<br>`formal_statement_signal_not_found`: {"statement_type": "income_statement"}<br>`formal_statement_signal_not_found`: {"statement_type": "cash_flow_statement"} |
| HK | `dd73d6f3-1a2d-4ce5-aebd-117111e50fd5` | GEELY AUTO | `financial_check_warning`: {"total": 119, "pass": 75, "fail": 0, "warning": 7, "skipped": 37} |
| EU | `ddcb5cbb-2b82-497b-a9f6-f578d379393d` | Koninklijke Philips N.V | `formal_statement_signal_not_found`: {"statement_type": "income_statement"}<br>`statement_evidence_ratio_low`: {"statement_type": "cash_flow_statement", "ratio": 0.0} |
| EU | `e68ea9dd-e9fd-4e3f-b18a-6f911c03c105` | London Stock Exchange Group plc | `suspicious_statement_title`: {"statement_type": "income_statement", "title": "Financial review"}<br>`financial_check_warning`: {"total": 15, "pass": 11, "fail": 0, "warning": 4, "skipped": 0} |
| HK | `e89555a9-359e-4d96-9737-198694c5a402` | BOC HONG KONG | `financial_check_warning`: {"total": 81, "pass": 62, "fail": 0, "warning": 3, "skipped": 16} |
| EU | `f10db2fd-027c-4e7d-874f-ed78e432f69a` | Compagnie Financiere Richemont SA | `suspicious_statement_title`: {"statement_type": "income_statement", "title": "Financial review"}<br>`financial_check_warning`: {"total": 21, "pass": 6, "fail": 0, "warning": 4, "skipped": 11} |
| HK | `f877c0f9-f2a7-4b13-99fa-8b2d507b1d70` | JD SW | `financial_check_warning`: {"total": 76, "pass": 40, "fail": 0, "warning": 11, "skipped": 25} |
| CN | `f8a2f174-e413-479e-ba41-18118de341ee` | 工商银行 | `formal_statement_signal_not_found`: {"statement_type": "balance_sheet"}<br>`formal_statement_signal_not_found`: {"statement_type": "income_statement"}<br>`formal_statement_signal_not_found`: {"statement_type": "cash_flow_statement"} |
| HK | `faead375-c944-4e2d-9ab9-c3dd61573410` | PING AN | `financial_check_warning`: {"total": 97, "pass": 81, "fail": 0, "warning": 2, "skipped": 14} |
| HK | `ff6651f7-18be-4cea-aaf5-6d3318c4798a` | CM BANK | `financial_check_warning`: {"total": 74, "pass": 52, "fail": 0, "warning": 6, "skipped": 16} |
