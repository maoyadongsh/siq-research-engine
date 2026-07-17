# Milvus Sandbox Write Protection

- Decision: `GO`
- Direct sandbox access to `19530`: `denied`
- Broker reads: `Search / Query / Get / Describe`
- Broker mutation routes: `absent`
- Business rows modified by proof: `0`
- Valid for: `3600 seconds`

The proof is bound to the active policy, verified sandbox/container, broker source, OpenShell bridge and a read-only Milvus schema observation.
