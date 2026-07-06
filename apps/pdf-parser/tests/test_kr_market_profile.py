import kr_market_profile as kr


def test_detect_market_prefers_explicit_task_market_and_filename():
    assert kr.KR_PROFILE_RULE_VERSION == "kr-pdf-profile-v3"
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
    assert candidates["요약재무정보"][0]["confidence"] == "high"
    assert candidates["Consolidated Statement of Financial Position"][0]["confidence"] == "high"
    assert candidates["Consolidated Statement of Profit or Loss"][0]["confidence"] == "high"
    assert candidates["Consolidated Statement of Comprehensive Income"][0]["confidence"] == "high"
    assert candidates["Consolidated Statement of Cash Flows"][0]["confidence"] == "high"
    assert candidates["Consolidated Statement of Changes in Equity"][0]["confidence"] == "high"
    assert all(row["_source"] == "kr_market_profile" for rows in candidates.values() for row in rows)


def test_kr_short_local_statement_titles_are_high_confidence():
    table_index = [
        {"table_index": 10, "heading": "연결 재무상태표", "preview": "자산총계 부채총계 자본총계"},
        {"table_index": 11, "heading": "연결 손익계산서", "preview": "매출액 영업이익 당기순이익"},
        {"table_index": 12, "heading": "연결 포괄손익계산서", "preview": "당기순이익 기타포괄손익 총포괄손익"},
        {"table_index": 13, "heading": "연결 현금흐름표", "preview": "영업활동 현금흐름 투자활동 현금흐름 기말현금"},
        {"table_index": 14, "heading": "연결 자본변동표", "preview": "자본금 이익잉여금 자본총계"},
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    for name in kr.KR_CORE_FINANCIAL_TABLE_NAMES[1:]:
        assert candidates[name][0]["confidence"] == "high"


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


def test_kr_candidate_groups_ignore_contents_table_and_accept_cash_flow_ocr_variants():
    table_index = [
        {
            "table_index": 16,
            "line": 271,
            "preview": "1. 연결재무상태표 20 2. 연결포괄손익계산서 21 3. 연결자본변동표 22 4. 연결한금흐를표 24 5. 연결재무제표에 대한 주석 25 10. 현금흐를표 30",
            "rows": 11,
            "numeric_ratio": 1.0,
        },
        {
            "table_index": 44,
            "line": 482,
            "preview": "과목 제 48(단)기 제 48(단)기 III. 재무활동으로 대한 현금초를 4,681,592,250,005 1,065,666,102,189 단가차입금의 증가",
            "rows": 8,
            "numeric_ratio": 0.7,
        },
        {
            "table_index": 50,
            "line": 519,
            "preview": "과적 제 49 (단)가 제 48 (단)가 III. 제부활동으로 대한 현금조율 4,672,139,044,974 1,047,337,253,887",
            "rows": 8,
            "numeric_ratio": 0.7,
        },
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert "Consolidated Statement of Financial Position" not in candidates
    assert candidates["Consolidated Statement of Cash Flows"][0]["table_index"] == 44


def test_kr_candidate_groups_suppress_subsidiary_summary_note_tables():
    table_index = [
        {
            "table_index": 595,
            "line": 7662,
            "heading": "① 제 65(당) 기말",
            "source_footnote": ["종속기업의 요약 재무정보는 다음과 같습니다."],
            "preview": (
                "종속기업명 자산총액 부채총액 매출액 당기순손익 "
                "한국수력원자력(주) 76,378,365 49,741,461 15,346,722 1,579,701"
            ),
        },
        {
            "table_index": 56,
            "line": 1447,
            "heading": "(단위: 천원)",
            "preview": (
                "구 분 자산 부채 자본 영업수익 당기순손익 총포괄손익 "
                "NAVER J.Hub Corporation 1,924,218,642 811,824,518 1,112,394,124"
            ),
        },
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert "요약재무정보" not in candidates
    assert "Consolidated Statement of Comprehensive Income" not in candidates


def test_kr_candidate_groups_promote_top_level_summary_with_title_in_footnote():
    table_index = [
        {
            "table_index": 64,
            "line": 1428,
            "heading": "※ 연결에 포함된 회사수는 한국가스공사를 제외한 숫자입니다.",
            "source_footnote": ["※ 상기요약연결재무정보는 한국채택국제회계기준(K-IFRS)에 따라서 작성하였습니다."],
            "preview": (
                "계정과목 제43기 제42기 제41기 [자산] 53,627,847 57,669,637 "
                "I.유동자산 13,634,386 [부채] 37,000,000 [자본] 16,000,000 "
                "매출액 45,000,000 영업이익 2,000,000 당기순이익 132,251"
            ),
        }
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert candidates["요약재무정보"][0]["table_index"] == 64
    assert candidates["요약재무정보"][0]["confidence"] == "high"


def test_kr_candidate_groups_promote_connected_summary_caption_title():
    table_index = [
        {
            "table_index": 118,
            "line": 3241,
            "heading": "(단위 : 백만원)",
            "source_caption": ["가. 연결요약재무정보", "(단위 : 백만원)"],
            "preview": "구 분 제58기 제57기 [유동자산] 43,483,869 [부채] 30,000,000 [자본] 20,000,000 매출액 80,000,000 당기순이익 3,000,000",
        }
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert candidates["요약재무정보"][0]["confidence"] == "high"


def test_kr_candidate_groups_suppress_major_shareholder_financial_status_tables():
    table_index = [
        {
            "table_index": 1270,
            "line": 12149,
            "heading": "나. 최대주주(법인 또는 단체)의 최근 결산기 재무현황",
            "preview": "구분 법인 또는 단체의 명칭 국민연금공단 자산총계 228,993 부채총계 584,915 자본총계 -355,922 매출액 44,544,620 영업이익 3,288 당기순이익 -17,155",
        }
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert "요약재무정보" not in candidates


def test_kr_candidate_groups_suppress_guaranteed_buyer_and_major_subsidiary_summaries():
    table_index = [
        {
            "table_index": 47,
            "line": 983,
            "heading": "(**) 보장매수자의 주요 재무정보는 다음과 같습니다.",
            "preview": "보장매수자 자산 부채 자본 영업수익 당기순손익 엘이피제일차(주) 29,898,030 30,208,504",
        },
        {
            "table_index": 1070,
            "line": 11825,
            "heading": "[PDF_PAGE: 424]",
            "source_footnote": ["EWP America Inc.의 요약 재무정보는 외 3개 종속기업의 재무정보를 포함한 연결재무정보입니다."],
            "preview": "상호 설립일 주소 주요사업 최근사업연도말자산총액 지배관계 근거 주요종속회사 여부",
        },
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert "요약재무정보" not in candidates


def test_kr_candidate_groups_promote_strong_equity_statement_body_without_title():
    table_index = [
        {
            "table_index": 53,
            "line": 1401,
            "heading": "(단위 : 원)",
            "preview": (
                "자본 지배기업 소유주지분 비지배지분 자본 합계 자본금 자본잉여금 "
                "기타자본구성요소 이익잉여금 2023.01.01 기초자본 당기순이익 "
                "총포괄손익 배당 기말자본"
            ),
        }
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert candidates["Consolidated Statement of Changes in Equity"][0]["table_index"] == 53
    assert candidates["Consolidated Statement of Changes in Equity"][0]["confidence"] == "high"


def test_kr_candidate_groups_promote_formal_comprehensive_income_body_without_title():
    table_index = [
        {
            "table_index": 412,
            "line": 4463,
            "heading": "(단위 : 백만원)",
            "preview": (
                "제 24 기 제 23 기 제 22 기 당기순이익 1,030,595 1,136,633 "
                "법인세비용차감후기타포괄이익 후속적으로 당기손익으로 재분류되지 않는 항목 "
                "후속적으로 당기손익으로 재분류되는 항목 총포괄이익"
            ),
        }
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert candidates["Consolidated Statement of Comprehensive Income"][0]["table_index"] == 412
    assert candidates["Consolidated Statement of Comprehensive Income"][0]["confidence"] == "high"


def test_kr_candidate_groups_promote_bank_comprehensive_income_body_without_title():
    table_index = [
        {
            "table_index": 158,
            "line": 2099,
            "heading": "(단위 : 백만원)",
            "preview": (
                "과 목 제 21기 총영업이익 영업이익 법인세비용차감전순이익 "
                "당기순이익 1,745,571 당기총포괄이익 1,745,498 주당이익 기타포괄손익"
            ),
        }
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert candidates["Consolidated Statement of Comprehensive Income"][0]["confidence"] == "high"


def test_kr_candidate_groups_promote_cash_flow_operating_section_body_without_title():
    table_index = [
        {
            "table_index": 54,
            "line": 1416,
            "heading": "(단위 : 원)",
            "preview": (
                "제 27 기 제 26 기 영업활동현금흐름 영업에서 창출된 현금흐름 "
                "이자의 수입 이자의 지급 법인세의 납부"
            ),
        }
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert candidates["Consolidated Statement of Cash Flows"][0]["table_index"] == 54
    assert candidates["Consolidated Statement of Cash Flows"][0]["confidence"] == "high"


def test_kr_candidate_groups_do_not_promote_segment_table_to_profit_or_loss():
    table_index = [
        {
            "table_index": 77,
            "line": 1628,
            "heading": "(단위 : 억원, %)",
            "preview": "사업부문 구분 제 24 기 금액 비율 HS 매출액 외부고객 매출 내부고객 매출 영업이익",
        }
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert "Consolidated Statement of Profit or Loss" not in candidates


def test_kr_candidate_groups_do_not_promote_retained_earnings_appropriation_to_comprehensive_income():
    table_index = [
        {
            "table_index": 524,
            "line": 6620,
            "heading": "(단위 : 천원)",
            "preview": (
                "공시금액 미처분이익잉여금 전기이월이익잉여금 당기순이익 "
                "기타포괄손익 공정가치측정 지분상품 처분손익 이익잉여금 처분액 배당금"
            ),
        }
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert "Consolidated Statement of Comprehensive Income" not in candidates
    assert "Consolidated Statement of Changes in Equity" not in candidates


def test_kr_candidate_groups_suppress_interest_average_balance_as_equity_statement():
    table_index = [
        {
            "table_index": 91,
            "line": 1285,
            "heading": "(단위 : 억원)",
            "source_footnote": ["(주) 평균잔액은 월말잔액의 평균임."],
            "preview": (
                "구 분 조달항목 2025년 연간 평균잔액 이자율 비중 자본 자본금 1,155 "
                "자본잉여금 1,676 이익잉여금 255 소 계 3,086"
            ),
        }
    ]

    candidates = kr.group_kr_key_table_candidates(table_index)

    assert "Consolidated Statement of Changes in Equity" not in candidates


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
