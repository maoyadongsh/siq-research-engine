# SIQ OpenShell Egress Boundary Proof

- Decision: `GO`
- Scope: `host_egress_broker`
- Formal business run: `false`
- Formal business sandbox evidence: `false`
- Readiness effect: `none`
- Eligible for completion: `false`
- Resolver mode: `mihomo_fake_ip_verified`
- Bound audit records: `13`
- Raw targets and payload material published: `false`

| Case | Decision | Rule | Broker HTTP | Upstream HTTP |
| --- | --- | --- | ---: | ---: |
| `public_get` | `allow` | `unknown_safe_read` | 200 | 200 |
| `public_head` | `allow` | `unknown_safe_read` | 200 | 200 |
| `unknown_small_json` | `audit_only` | `unknown_json_post_audit` | 200 | 405 |
| `unknown_multipart` | `deny` | `broker_multipart_denied` | 403 | n/a |
| `unknown_octet_stream` | `deny` | `broker_octet_stream_denied` | 403 | n/a |
| `unknown_put` | `deny` | `broker_method_denied` | 403 | n/a |
| `cloud_metadata` | `deny` | `ssrf_non_public_ip` | 403 | n/a |

This proof does not claim formal business sandbox coverage, direct transfer-client execution, or semantic DLP.
