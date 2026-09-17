# Diagnostics package

Hardware summaries, structured runtime evidence, and bounded diagnostic-bundle export.

Bundles are privacy-redacted by default. Every artifact the bundle writes declares its redaction
strategy in `ARTIFACT_REDACTION` — path-field redaction for hardware and graph data, path-token
redaction for live metrics and node profiles, and line redaction for logs (via `LOG_REDACTION`) — so
paths in graph summaries, logs, manifests, and executable locations all pass through one declared
policy; frame pixels and secrets are never included. Adding an artifact requires declaring its
strategy in the table, a size/count bound, and a test for both redacted and explicit path-including
modes.
