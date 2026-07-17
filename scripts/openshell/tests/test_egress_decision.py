from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.openshell import egress_decision as module

PUBLIC_IP = "93.184.216.34"


def _request(
    host: str,
    *,
    method: str = "GET",
    content_type: str | None = None,
    body_bytes: int | None = 0,
    scheme: str = "https",
    port: int = 443,
    client: str = "curl",
    resolved_ips: list[str] | None = None,
) -> module.RequestProjection:
    return module.project_request(
        {
            "scheme": scheme,
            "host": host,
            "port": port,
            "method": method,
            "content_type": content_type,
            "body_bytes": body_bytes,
            "resolved_ips": [PUBLIC_IP] if resolved_ips is None else resolved_ips,
            "client": client,
        }
    )


@pytest.fixture(scope="module")
def allowlist() -> module.Allowlist:
    return module.load_allowlist()


def test_tracked_allowlist_is_strict_secret_free_and_covers_reviewed_destinations(allowlist) -> None:
    raw = json.loads(module.DEFAULT_ALLOWLIST.read_text(encoding="utf-8"))
    hosts = {host for rule in allowlist.rules for host in rule.host_patterns}

    assert raw["schema_version"] == module.SCHEMA_VERSION
    assert raw["unknown_json_post_max_bytes"] == 128 * 1024
    assert hosts == {
        "api.exa.ai",
        "api.github.com",
        "api.kimi.com",
        "api.minimax.chat",
        "api.stepfun.com",
        "api.tavily.com",
        "github.com",
        "open.feishu.cn",
        "open.larksuite.com",
        "uploads.github.com",
    }
    serialized = json.dumps(raw, sort_keys=True).lower()
    assert "api_key" not in serialized
    assert "password" not in serialized
    assert "authorization" not in serialized
    assert "cookie" not in serialized


@pytest.mark.parametrize(
    ("host", "rule_id"),
    [
        ("api.minimax.chat", "approved_model_minimax"),
        ("api.stepfun.com", "approved_model_stepfun"),
        ("api.kimi.com", "approved_model_kimi"),
        ("api.tavily.com", "approved_search_tavily"),
        ("api.exa.ai", "approved_search_exa"),
    ],
)
def test_approved_model_and_search_json_posts_are_allowed(allowlist, host: str, rule_id: str) -> None:
    result = module.decide(
        _request(host, method="POST", content_type="application/json; charset=utf-8", body_bytes=512 * 1024),
        allowlist,
    )

    assert result.as_dict() == {
        "rule_id": rule_id,
        "decision": "allow",
        "host": {"scheme": "https", "hostname": host, "port": 443},
    }


@pytest.mark.parametrize(
    "projection",
    [
        _request("api.github.com", method="PUT", content_type="application/vnd.github+json", body_bytes=4096),
        _request(
            "uploads.github.com",
            method="POST",
            content_type="application/octet-stream",
            body_bytes=8 * 1024 * 1024,
        ),
        _request("github.com", method="POST", content_type="application/x-git-receive-pack-request", body_bytes=8192),
        _request("open.feishu.cn", method="POST", content_type="multipart/form-data; boundary=abc", body_bytes=8192),
        _request("open.larksuite.com", method="PUT", content_type="application/octet-stream", body_bytes=8192),
    ],
)
def test_explicit_github_and_lark_upload_rules_are_allowed(allowlist, projection) -> None:
    assert module.decide(projection, allowlist).decision == "allow"


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_unknown_get_and_head_are_allowed(allowlist, method: str) -> None:
    result = module.decide(_request("public.example", method=method, body_bytes=None), allowlist)
    assert result.rule_id == "unknown_safe_read"
    assert result.decision == "allow"


@pytest.mark.parametrize("host", ["api.tavily.com", "api.exa.ai"])
def test_approved_search_provider_get_is_allowed_without_a_body(allowlist, host: str) -> None:
    result = module.decide(_request(host, method="GET", body_bytes=None), allowlist)

    assert result.decision == "allow"
    assert result.rule_id.startswith("approved_search_")


@pytest.mark.parametrize("size", [0, 1, 128 * 1024])
def test_unknown_small_json_post_is_audited_and_forwardable(allowlist, size: int) -> None:
    result = module.decide(
        _request("public.example", method="POST", content_type="application/problem+json", body_bytes=size),
        allowlist,
    )
    assert result.rule_id == "unknown_json_post_audit"
    assert result.decision == "audit_only"


@pytest.mark.parametrize(
    ("projection", "rule_id"),
    [
        (
            _request("public.example", method="POST", content_type="application/json", body_bytes=None),
            "unknown_body_size",
        ),
        (
            _request("public.example", method="POST", content_type="application/json", body_bytes=128 * 1024 + 1),
            "unknown_body_too_large",
        ),
        (
            _request("public.example", method="POST", content_type="multipart/form-data", body_bytes=1),
            "unknown_multipart_upload",
        ),
        (
            _request("public.example", method="POST", content_type="application/octet-stream", body_bytes=1),
            "unknown_octet_stream_upload",
        ),
        (_request("public.example", method="PUT", content_type="application/json", body_bytes=1), "unknown_put_upload"),
        (_request("public.example", method="POST", content_type="text/plain", body_bytes=1), "unknown_non_json_post"),
    ],
)
def test_unknown_upload_risks_are_denied(allowlist, projection, rule_id: str) -> None:
    result = module.decide(projection, allowlist)
    assert result.rule_id == rule_id
    assert result.decision == "deny"


@pytest.mark.parametrize(
    ("host", "resolved_ips", "rule_id"),
    [
        ("169.254.169.254", ["169.254.169.254"], "ssrf_non_public_ip"),
        ("127.0.0.1", ["127.0.0.1"], "ssrf_non_public_ip"),
        ("10.0.0.10", ["10.0.0.10"], "ssrf_non_public_ip"),
        ("[::1]", ["::1"], "ssrf_non_public_ip"),
        ("0.0.0.0", ["0.0.0.0"], "ssrf_non_public_ip"),
        ("224.0.0.1", ["224.0.0.1"], "ssrf_non_public_ip"),
        ("api.stepfun.com", ["192.168.1.10"], "ssrf_non_public_ip"),
        ("metadata.google.internal", [], "ssrf_metadata_host"),
        ("host.openshell.internal", [], "ssrf_internal_hostname"),
        ("localhost", ["127.0.0.1"], "ssrf_internal_hostname"),
        ("public.example", [], "ssrf_dns_projection_missing"),
    ],
)
def test_metadata_loopback_link_local_private_and_missing_dns_are_denied(
    allowlist,
    host: str,
    resolved_ips: list[str],
    rule_id: str,
) -> None:
    result = module.decide(_request(host, resolved_ips=resolved_ips), allowlist)
    assert result.rule_id == rule_id
    assert result.decision == "deny"


@pytest.mark.parametrize("client", ["scp", "/usr/bin/sftp", "rsync", "rclone", "ftp", "nc", "ncat", "socat"])
def test_unknown_transfer_clients_are_denied(allowlist, client: str) -> None:
    result = module.decide(_request("public.example", client=client), allowlist)
    assert result.rule_id == "blocked_transfer_client"
    assert result.decision == "deny"


def test_blocked_transfer_scheme_is_denied_even_with_generic_client(allowlist) -> None:
    result = module.decide(_request("public.example", scheme="sftp", port=22), allowlist)
    assert result.rule_id == "blocked_transfer_scheme"
    assert result.decision == "deny"


def test_non_http_scheme_cannot_use_unknown_get_allowance(allowlist) -> None:
    result = module.decide(_request("public.example", scheme="gopher", port=70), allowlist)
    assert result.rule_id == "unsupported_egress_scheme"
    assert result.decision == "deny"


def test_approved_host_rule_mismatch_fails_closed(allowlist) -> None:
    wrong_content = _request("api.stepfun.com", method="POST", content_type="multipart/form-data", body_bytes=10)
    too_large = _request(
        "api.tavily.com",
        method="POST",
        content_type="application/json",
        body_bytes=16 * 1024 * 1024 + 1,
    )
    wrong_method = _request("uploads.github.com", method="PUT", content_type="application/octet-stream", body_bytes=10)
    get_with_body = _request("api.github.com", method="GET", content_type="application/json", body_bytes=10)

    assert {
        module.decide(item, allowlist).rule_id for item in (wrong_content, too_large, wrong_method, get_with_body)
    } == {"approved_destination_rule_mismatch"}


def test_every_redirect_hop_is_re_evaluated_and_any_deny_wins(allowlist) -> None:
    chain = [
        _request("api.stepfun.com", method="POST", content_type="application/json", body_bytes=1024),
        _request("public.example", method="GET"),
        _request("169.254.169.254", resolved_ips=["169.254.169.254"]),
    ]
    result = module.evaluate_redirect_chain(chain, allowlist)
    assert result.rule_id == "ssrf_non_public_ip"
    assert result.host["hostname"] == "169.254.169.254"


def test_redirect_chain_preserves_audit_only_when_later_hops_allow(allowlist) -> None:
    result = module.evaluate_redirect_chain(
        [
            _request("public.example", method="POST", content_type="application/json", body_bytes=100),
            _request("api.github.com", method="GET"),
        ],
        allowlist,
    )
    assert result.rule_id == "unknown_json_post_audit"
    assert result.decision == "audit_only"


def test_output_contract_cannot_contain_path_query_headers_or_body(allowlist) -> None:
    result = module.decide(_request("PUBLIC.EXAMPLE."), allowlist).as_dict()
    assert set(result) == {"rule_id", "decision", "host"}
    assert set(result["host"]) == {"scheme", "hostname", "port"}
    serialized = json.dumps(result, sort_keys=True)
    assert result["host"]["hostname"] == "public.example"
    assert "path" not in serialized
    assert "query" not in serialized
    assert "header" not in serialized
    assert "body" not in serialized


def test_request_projection_rejects_extra_sensitive_fields_without_echoing_value() -> None:
    payload = {
        "scheme": "https",
        "host": "public.example",
        "port": 443,
        "method": "POST",
        "content_type": "application/json",
        "body_bytes": 10,
        "resolved_ips": [PUBLIC_IP],
        "client": "curl",
        "body": "unique-secret",
    }
    with pytest.raises(module.EgressConfigurationError) as raised:
        module.project_request(payload)
    assert str(raised.value) == "request_projection_schema_invalid"
    assert "unique-secret" not in str(raised.value)


@pytest.mark.parametrize(
    "pattern",
    ["*.com", "*.co.uk", "*.co.in", "api.*.example.com", "*.*.example.com"],
)
def test_allowlist_rejects_tld_or_uncontrolled_wildcards(pattern: str) -> None:
    payload = json.loads(module.DEFAULT_ALLOWLIST.read_text(encoding="utf-8"))
    payload["rules"][0]["host_patterns"] = [pattern]
    with pytest.raises(module.EgressConfigurationError):
        module.parse_allowlist(payload)


def test_controlled_wildcard_matches_exactly_one_first_label() -> None:
    payload = json.loads(module.DEFAULT_ALLOWLIST.read_text(encoding="utf-8"))
    payload["rules"][0]["host_patterns"] = ["*.models.example.com"]
    allowlist = module.parse_allowlist(payload)

    assert (
        module.decide(
            _request("cn.models.example.com", method="POST", content_type="application/json", body_bytes=10),
            allowlist,
        ).rule_id
        == "approved_model_minimax"
    )
    assert (
        module.decide(
            _request("a.cn.models.example.com", method="POST", content_type="application/json", body_bytes=10),
            allowlist,
        ).rule_id
        == "unknown_json_post_audit"
    )


def test_allowlist_rejects_overlapping_wildcard_and_exact_host_in_one_rule() -> None:
    payload = json.loads(module.DEFAULT_ALLOWLIST.read_text(encoding="utf-8"))
    payload["rules"][0]["host_patterns"] = ["*.models.example.com", "cn.models.example.com"]
    with pytest.raises(module.EgressConfigurationError, match="allowlist_host_pattern_overlap"):
        module.parse_allowlist(payload)


def test_allowlist_schema_rejects_unknown_fields_and_symlink(tmp_path: Path) -> None:
    payload = json.loads(module.DEFAULT_ALLOWLIST.read_text(encoding="utf-8"))
    payload["rules"][0]["api_key"] = "unique-secret"
    with pytest.raises(module.EgressConfigurationError) as raised:
        module.parse_allowlist(payload)
    assert str(raised.value) == "allowlist_rule_schema_invalid"
    assert "unique-secret" not in str(raised.value)

    real = tmp_path / "allowlist.json"
    real.write_text(module.DEFAULT_ALLOWLIST.read_text(encoding="utf-8"), encoding="utf-8")
    alias = tmp_path / "alias.json"
    alias.symlink_to(real)
    with pytest.raises(module.EgressConfigurationError, match="allowlist_file_invalid"):
        module.load_allowlist(alias)

    real_dir = tmp_path / "real-dir"
    real_dir.mkdir()
    nested = real_dir / "allowlist.json"
    nested.write_text(module.DEFAULT_ALLOWLIST.read_text(encoding="utf-8"), encoding="utf-8")
    alias_dir = tmp_path / "alias-dir"
    alias_dir.symlink_to(real_dir, target_is_directory=True)
    with pytest.raises(module.EgressConfigurationError, match="allowlist_file_invalid"):
        module.load_allowlist(alias_dir / "allowlist.json")


def test_allowlist_rejects_internal_destination_pattern() -> None:
    payload = json.loads(module.DEFAULT_ALLOWLIST.read_text(encoding="utf-8"))
    payload["rules"][0]["host_patterns"] = ["provider.internal"]
    with pytest.raises(module.EgressConfigurationError, match="allowlist_internal_host_forbidden"):
        module.parse_allowlist(payload)


def test_request_projection_rejects_client_whitespace_and_excessive_dns_projection() -> None:
    base = {
        "scheme": "https",
        "host": "public.example",
        "port": 443,
        "method": "GET",
        "content_type": None,
        "body_bytes": 0,
        "resolved_ips": [PUBLIC_IP],
        "client": "nc ",
    }
    with pytest.raises(module.EgressConfigurationError, match="request_client_invalid"):
        module.project_request(base)

    base["client"] = "curl"
    base["resolved_ips"] = [f"8.8.8.{index}" for index in range(17)]
    with pytest.raises(module.EgressConfigurationError, match="request_resolved_ips_invalid"):
        module.project_request(base)


def test_cli_emits_only_sanitized_decision_and_uses_deny_exit_code(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps(
            {
                "requests": [
                    {
                        "scheme": "https",
                        "host": "public.example",
                        "port": 443,
                        "method": "POST",
                        "content_type": "application/octet-stream",
                        "body_bytes": 10,
                        "resolved_ips": [PUBLIC_IP],
                        "client": "curl",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert module.main(["--input", str(input_path)]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "rule_id": "unknown_octet_stream_upload",
        "decision": "deny",
        "host": {"scheme": "https", "hostname": "public.example", "port": 443},
    }


def test_engine_is_explicitly_not_a_network_enforcement_proxy() -> None:
    assert "does not enforce or proxy network traffic" in (module.__doc__ or "")
