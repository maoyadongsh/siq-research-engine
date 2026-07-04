from __future__ import annotations

from hk_evidence_lib import parsed_tables_from_document_full


def test_parsed_tables_from_document_full_reads_nested_enhanced_raw_preview():
    enhanced = {
        "tables": [
            {
                "table_id": "hk_table_0001",
                "table_index": 1,
                "title": None,
                "raw": {
                    "pdf_page_number": 42,
                    "structure": {
                        "header_preview": [
                            "Note | 2026HK$M | 2025HK$M",
                            "Revenue | 5 | 13,938 | 14,223",
                            "Total assets | 209,556 | 220,413",
                        ]
                    },
                    "preview": "Revenue 13,938 14,223",
                },
            }
        ]
    }

    tables = parsed_tables_from_document_full({"content_list": []}, enhanced)

    assert len(tables) == 1
    assert tables[0].page_number == 42
    assert tables[0].rows[1] == ["Revenue", "5", "13,938", "14,223"]
