import kr_market_profile as kr


def test_detect_market_prefers_explicit_task_market_and_filename():
    assert kr.is_kr_market({"submit_config": {"market": "KR"}}, "anything.pdf")
    assert kr.is_kr_market({"market": "kr"}, "anything.pdf")
    assert kr.is_kr_market({}, "Samsung-Electronics-Co.,-Ltd_KR_005930_2025.pdf")
    assert kr.is_kr_market({}, "Celltrion,-Inc_KR_068270_2025-12-31_年报_2026-03-16_dart_public_e65803cd.pdf")
    assert not kr.is_kr_market({"submit_config": {"market": "CN"}}, "Samsung-Electronics-Co.,-Ltd_KR_005930_2025.pdf")


def test_kr_candidate_groups_find_dart_sections_and_core_statements():
    markdown = """
    I. 회사의 개요
    II. 사업의 내용
    III. 재무에 관한 사항
    1. 요약재무정보
    2-1. 연결 재무상태표
    2-2. 연결 손익계산서
    2-3. 연결 포괄손익계산서
    2-4. 연결 자본변동표
    2-5. 연결 현금흐름표
    """
    table_index = [
        {"table_index": 1, "line": 10, "preview": "요약재무정보 매출액 영업이익 당기순이익 자산총계"},
        {"table_index": 2, "line": 30, "preview": "연결 재무상태표 자산 유동자산 자산총계 부채총계 자본총계"},
        {"table_index": 3, "line": 50, "preview": "연결 손익계산서 매출액 영업이익 법인세비용차감전순이익 당기순이익"},
        {"table_index": 4, "line": 70, "preview": "연결 포괄손익계산서 당기순이익 기타포괄손익 총포괄손익"},
        {"table_index": 5, "line": 90, "preview": "연결 현금흐름표 영업활동 현금흐름 투자활동 현금흐름 기말현금"},
        {"table_index": 6, "line": 110, "preview": "연결 자본변동표 자본금 이익잉여금 자본총계"},
        {"table_index": 7, "line": 130, "preview": "영업부문별 정보 매출액 영업이익 부문자산"},
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert kr.found_sections(markdown, table_index)[:3] == ["회사의 개요", "사업의 내용", "재무에 관한 사항"]
    assert candidates["요약재무정보"][0]["table_index"] == 1
    assert candidates["Consolidated Statement of Financial Position"][0]["table_index"] == 2
    assert candidates["Consolidated Statement of Profit or Loss"][0]["table_index"] == 3
    assert candidates["Consolidated Statement of Comprehensive Income"][0]["table_index"] == 4
    assert candidates["Consolidated Statement of Cash Flows"][0]["table_index"] == 5
    assert candidates["Consolidated Statement of Changes in Equity"][0]["table_index"] == 6
    assert candidates["Segment Information"][0]["table_index"] == 7
    assert all(row["_source"] == "kr_market_profile" for rows in candidates.values() for row in rows)


def test_kr_candidate_groups_prefer_financial_statement_zone_over_business_or_note_tables():
    table_index = [
        {
            "table_index": 21,
            "line": 610,
            "pdf_page_number": 31,
            "heading": "(단위 : 백만원)",
            "source_footnote": ["단일 사업부문이므로 사업부문별 비중은 작성하지 않음"],
            "preview": "구 분 주요제품 금 액 총매출액 승용 RV 상용 등 197,118,422 내부매출액 (82,977,503) 순매출액 114,140,919 영업이익 9,078,148",
        },
        {
            "table_index": 62,
            "line": 1456,
            "pdf_page_number": 77,
            "heading": "[PDF_PAGE: 77]",
            "source_footnote": ["제82기(당기)는 주주총회 승인 전 연결재무제표입니다."],
            "preview": "제 82 기 제 81 기 제 80 기 자산 유동자산 현금및현금성자산 단기금융상품 매출채권 재고자산 비유동자산 자산총계 부채 유동부채 비유동부채 부채총계 자본 자본총계",
        },
        {
            "table_index": 63,
            "line": 1471,
            "pdf_page_number": 78,
            "heading": "(단위 : 백만원)",
            "source_footnote": ["제82기(당기)는 주주총회 승인 전 연결재무제표입니다."],
            "preview": "제 82 기 제 81 기 제 80 기 매출액 매출원가 매출총이익 판매비와관리비",
        },
        {
            "table_index": 68,
            "line": 1563,
            "pdf_page_number": 85,
            "heading": "① 제 82(당) 기",
            "preview": "(단위: 백만원) 회사명 자산총액 부채총액 매출액 당기순이익 KUS 14,695,685 7,217,965 41,924,262 1,170,322",
        },
        {
            "table_index": 556,
            "line": 6053,
            "pdf_page_number": 320,
            "heading": "(단위 : 백만원)",
            "source_footnote": ["※ 2025년말 별도재무제표 기준"],
            "preview": "구분 법인 또는 단체의 명칭 현대자동차(주) 자산총계 98,580,388 부채총계 28,611,827 자본총계 69,968,561 매출액 78,766,791 영업이익 3,515,052 당기순이익 4,054,878",
        },
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert candidates["Consolidated Statement of Financial Position"][0]["table_index"] == 62
    assert candidates["Consolidated Statement of Profit or Loss"][0]["table_index"] == 63


def test_kr_quality_messages_and_checks_do_not_use_a_share_missing_table_warnings():
    warnings, info = kr.kr_quality_report_messages(
        table_count=12,
        single_row_table_count=0,
        image_ref_count=2,
        found_core_table_count=1,
        suspicious_table_count=0,
    )
    checks = kr.build_kr_financial_checks(
        {
            "task_id": "kr-task",
            "filename": "Samsung-Electronics-Co.,-Ltd_KR_005930_2025.pdf",
            "market": "KR",
            "report_kind": "kr_business_report",
            "warnings": [],
            "statements": [],
        }
    )

    combined = warnings + checks["warnings"]
    assert any("KR" in item for item in combined)
    assert any("图片引用" in item for item in info)
    assert checks["market"] == "KR"
    assert checks["overall_status"] == "skipped"
    assert checks["summary"] == {"total": 0, "pass": 0, "fail": 0, "warning": 0, "skipped": 0}
    assert not any("合并资产负债表" in item or "合并利润表" in item or "合并现金流量表" in item for item in combined)
