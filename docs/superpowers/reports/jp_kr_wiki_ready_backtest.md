# PDF Parser Wiki-Ready Backtest

- Generated at: `2026-07-08T00:51:38Z`
- Markets: `JP, KR`
- Total: `59`
- Wiki ready: `59`
- Not ready: `0`

## By Market

| Market | Total | Wiki ready | Not ready | Warnings |
| --- | ---: | ---: | ---: | ---: |
| JP | 30 | 30 | 0 | 0 |
| KR | 29 | 29 | 0 | 30 |

## Blockers

| Code | Count |
| --- | ---: |
| _none_ | 0 |

## Warnings

| Code | Count |
| --- | ---: |
| `financial_check_warning` | 26 |
| `statement_item_sprawl` | 3 |
| `unclassified_statement_source_sprawl` | 1 |

## Market Profiles

| Market | Profile | Sprawl limits | Unclassified ratio limit | Notes |
| --- | --- | --- | ---: | --- |
| JP | `jp_edinet_wiki_ready_v1` | balance_sheet=360, income_statement=380, cash_flow_statement=120 | None | EDINET PDF statements can be split across adjacent tables and pages.<br>High parsed_financial_table ratio is tracked in quality profile, not treated as note-sprawl by itself. |
| KR | `kr_dart_wiki_ready_v1` | balance_sheet=560, income_statement=500, cash_flow_statement=140 | 0.35 | DART PDFs contain many note/detail tables; unclassified statement facts are suspicious unless strongly detected.<br>Larger Korean financial statements are allowed, but extreme item counts remain review warnings. |

## Statement Quality Profile

| Market | Statement | Items p50/p90/max | Source tables p50/p90/max | Unclassified source ratio p50/p90/max |
| --- | --- | ---: | ---: | ---: |
| JP | `balance_sheet` | 164/211/261 | 24/33/47 | 0.583/0.722/0.79 |
| JP | `cash_flow_statement` | 36/50/55 | 3/6/7 | 0.0/0.045/0.075 |
| JP | `income_statement` | 122/215/360 | 20/26/29 | 0.45/0.663/0.778 |
| KR | `balance_sheet` | 230/423/577 | 14/24/35 | 0.0/0.0/0.065 |
| KR | `cash_flow_statement` | 49/97/142 | 4/10/14 | 0.0/0.092/0.455 |
| KR | `income_statement` | 108/228/681 | 9/17/25 | 0.0/0.0/0.0 |

## Warning Samples

| Market | Task | Company | Warning samples |
| --- | --- | --- | --- |
| KR | `13c9353d-1f65-4946-b362-3aca3d3ca319` | NAVER Corporation | `financial_check_warning`: {"total": 381, "pass": 366, "fail": 0, "warning": 9, "skipped": 6} |
| KR | `1c9880fa-c11a-4b88-aab6-16c665cf4ba7` | Samsung Biologics Co., Ltd | `financial_check_warning`: {"total": 474, "pass": 429, "fail": 0, "warning": 12, "skipped": 33} |
| KR | `207fa4d9-f5c5-4dc2-9dbf-d626fc7d9f02` | Amorepacific Corporation | `financial_check_warning`: {"total": 272, "pass": 252, "fail": 0, "warning": 2, "skipped": 18} |
| KR | `22114eda-8e55-4354-81e3-7ee82865f827` | POSCO Holdings Inc | `financial_check_warning`: {"total": 780, "pass": 748, "fail": 0, "warning": 5, "skipped": 27} |
| KR | `2d036a09-0c64-4875-b595-632c87c50ba8` | Lotte Shopping Co., Ltd | `financial_check_warning`: {"total": 478, "pass": 455, "fail": 0, "warning": 5, "skipped": 18} |
| KR | `3572dce1-c8e4-4bb4-8f47-2cb43eadc982` | Doosan Enerbility Co., Ltd | `financial_check_warning`: {"total": 470, "pass": 433, "fail": 0, "warning": 13, "skipped": 24} |
| KR | `3e27fbba-bf58-4145-9ba1-88acba6e4921` | Kia Corporation | `financial_check_warning`: {"total": 260, "pass": 253, "fail": 0, "warning": 4, "skipped": 3} |
| KR | `4c6b46ae-4957-4042-83de-e20fed8c954e` | Kakao Corp | `financial_check_warning`: {"total": 855, "pass": 845, "fail": 0, "warning": 2, "skipped": 8} |
| KR | `4f91e58e-04eb-4427-9c82-90c9a8ed7a08` | Samsung Electronics Co., Ltd | `statement_item_sprawl`: {"statement_type": "balance_sheet", "item_count": 577, "limit": 560}<br>`financial_check_warning`: {"total": 781, "pass": 760, "fail": 0, "warning": 7, "skipped": 14} |
| KR | `5cd163a4-cff0-4fc1-9f74-cbf99f37e33f` | SK hynix Inc | `financial_check_warning`: {"total": 428, "pass": 394, "fail": 0, "warning": 10, "skipped": 24} |
| KR | `5f5fdfda-fe61-4d55-8ba2-63ebccc6ec8e` | Korea Electric Power Corporation | `financial_check_warning`: {"total": 458, "pass": 444, "fail": 0, "warning": 11, "skipped": 3} |
| KR | `6f85821c-3753-4e13-8e3a-c515f7f29640` | Samsung C&T Corporation | `financial_check_warning`: {"total": 559, "pass": 500, "fail": 0, "warning": 8, "skipped": 51} |
| KR | `7184cd22-0f0c-4a79-8842-fa7ac0bbfabb` | LG Electronics Inc | `financial_check_warning`: {"total": 581, "pass": 559, "fail": 0, "warning": 7, "skipped": 15} |
| KR | `72bcc948-8ae9-4d09-9850-4b83286d381b` | Korean Air Lines Co., Ltd | `financial_check_warning`: {"total": 440, "pass": 330, "fail": 0, "warning": 24, "skipped": 86} |
| KR | `7d84950f-d5d7-4f1f-8356-60e3d9c9ec1c` | Samsung SDI Co., Ltd | `financial_check_warning`: {"total": 420, "pass": 408, "fail": 0, "warning": 8, "skipped": 4} |
| KR | `8e0dd586-c62f-4406-b116-2b031bdd4985` | Hyundai Mobis Co., Ltd | `financial_check_warning`: {"total": 469, "pass": 455, "fail": 0, "warning": 8, "skipped": 6} |
| KR | `8e8fba92-dcba-457d-8b84-4bafa0610282` | CJ CheilJedang Corporation | `financial_check_warning`: {"total": 662, "pass": 648, "fail": 0, "warning": 7, "skipped": 7} |
| KR | `9b597cab-3779-4600-8377-9b729831582c` | Krafton, Inc | `financial_check_warning`: {"total": 605, "pass": 477, "fail": 0, "warning": 23, "skipped": 105} |
| KR | `a1e3ca2b-625a-45c7-934c-dcf863e1c595` | Hana Financial Group Inc | `statement_item_sprawl`: {"statement_type": "income_statement", "item_count": 681, "limit": 500}<br>`financial_check_warning`: {"total": 1030, "pass": 1009, "fail": 0, "warning": 7, "skipped": 14} |
| KR | `b2aeff92-19be-4c4b-bd58-5fd8aec578bc` | Celltrion, Inc | `financial_check_warning`: {"total": 348, "pass": 333, "fail": 0, "warning": 12, "skipped": 3} |
| KR | `c9475f3b-32bb-467b-ba00-9f3a1bd287db` | Hyundai Motor Company | `statement_item_sprawl`: {"statement_type": "cash_flow_statement", "item_count": 142, "limit": 140}<br>`financial_check_warning`: {"total": 803, "pass": 791, "fail": 0, "warning": 5, "skipped": 7} |
| KR | `d89e1237-5661-4351-9993-985dbcb74fc5` | LG Chem, Ltd | `unclassified_statement_source_sprawl`: {"statement_type": "cash_flow_statement", "item_count": 44, "parsed_financial_table_items": 20, "ratio": 0.455, "limit": 0.35}<br>`financial_check_warning`: {"total": 380, "pass": 345, "fail": 0, "warning": 14, "skipped": 21} |
| KR | `e9d1d068-bff6-4d91-82ce-760e86bbee1d` | HD Hyundai Heavy Industries Co., Ltd | `financial_check_warning`: {"total": 505, "pass": 492, "fail": 0, "warning": 4, "skipped": 9} |
| KR | `ebc7e046-d042-407e-82c5-7a66093603a9` | LG Energy Solution, Ltd | `financial_check_warning`: {"total": 410, "pass": 373, "fail": 0, "warning": 13, "skipped": 24} |
| KR | `efc6c108-d5c5-4ae9-9537-1062d6b384be` | Shinhan Financial Group Co., Ltd | `financial_check_warning`: {"total": 505, "pass": 473, "fail": 0, "warning": 5, "skipped": 27} |
| KR | `f4ccf296-376c-4477-8b20-2d133a00779a` | Korea Gas Corporation | `financial_check_warning`: {"total": 414, "pass": 408, "fail": 0, "warning": 6, "skipped": 0} |
