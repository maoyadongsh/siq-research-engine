from services import market_report_assist_service as service


def test_extract_json_object_accepts_fenced_and_embedded_json():
    assert service.extract_json_object('```json\n{"intent": {"market": "US"}}\n```') == {"intent": {"market": "US"}}
    assert service.extract_json_object('prefix {"ok": true} suffix') == {"ok": True}
    assert service.extract_json_object('["not", "object"]') is None
    assert service.extract_json_object('no json here') is None


def test_assist_user_payload_compacts_candidates_and_retry_hint():
    request = {
        "prompt": "下载三星电子 2025 年年报",
        "market": "KR",
        "company_name": "三星电子",
        "ticker": "005930",
        "company_id": "KR:005930",
        "report_year": 2025,
        "report_types": ["annual"],
        "ignored": "x",
        "candidates": [
            {
                "document_url": f"https://dart.example/{idx}",
                "title": f"사업보고서 {idx}",
                "report_type": "annual",
                "report_end": "2025-12-31",
                "published_at": "2026-03-15",
                "extra": "not sent",
            }
            for idx in range(35)
        ],
    }
    base = {"intent": {"market": "KR"}, "candidate_explanations": []}

    payload = service.assist_user_payload(request, base)
    retry_payload = service.assist_retry_user_payload(request, base)

    assert payload["prompt"] == request["prompt"]
    assert payload["request"] == {
        "market": "KR",
        "company_name": "三星电子",
        "ticker": "005930",
        "company_id": "KR:005930",
        "report_year": 2025,
        "report_types": ["annual"],
    }
    assert len(payload["official_candidates"]) == 30
    assert payload["official_candidates"][0] == {
        "document_url": "https://dart.example/0",
        "title": "사업보고서 0",
        "report_type": "annual",
        "report_end": "2025-12-31",
        "published_at": "2026-03-15",
    }
    assert "retry_hint" in retry_payload


def test_merge_assist_enriches_intent_and_preserves_rule_candidate_order():
    base = {
        "intent": {"market": "US", "company_query": "苹果", "report_types": ["annual"]},
        "candidate_explanations": [
            {
                "document_url": "https://sec.example/a",
                "title_zh": "规则标题 A",
                "recommended": True,
            },
            {
                "document_url": "https://sec.example/b",
                "title_zh": "规则标题 B",
                "recommended": False,
            },
        ],
        "assistant_mode": "rules",
    }
    llm = {
        "intent": {"company_query": "Apple Inc.", "ticker": "AAPL", "company_id": "0000320193"},
        "candidate_explanations": [
            {
                "document_url": "https://sec.example/b",
                "title_zh": "模型标题 B",
                "recommendation": "报告期不匹配",
            },
            {
                "document_url": "https://sec.example/a",
                "recommendation": "报告期和年报类型匹配",
            },
        ],
        "assistant_mode": "llm:cloud:test",
    }

    merged = service.merge_assist(base, llm)

    assert merged["intent"] == {
        "market": "US",
        "company_query": "Apple Inc.",
        "report_types": ["annual"],
        "ticker": "AAPL",
        "company_id": "0000320193",
    }
    assert [item["document_url"] for item in merged["candidate_explanations"]] == [
        "https://sec.example/a",
        "https://sec.example/b",
    ]
    assert merged["candidate_explanations"][0]["title_zh"] == "规则标题 A"
    assert merged["candidate_explanations"][0]["recommendation"] == "报告期和年报类型匹配"
    assert merged["candidate_explanations"][1]["title_zh"] == "模型标题 B"
    assert merged["assistant_mode"] == "llm:cloud:test"
