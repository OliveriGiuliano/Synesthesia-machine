# Diagnostics package

Hardware summaries, structured runtime evidence, and bounded diagnostic-bundle export.

Bundles are privacy-redacted by default. Paths in graph summaries, logs, manifests, hardware data,
and executable locations must all pass through the same redaction policy; frame pixels and secrets
are never included. Adding an artifact requires a size/count bound and a test for both redacted and
explicit path-including modes.
