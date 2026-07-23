# Quarantine digest

Every block that failed `hub/inbox_ingest.py`'s scan-before-commit gate, and every
publish blocked by `hub/snapshot_publish.py`'s egress scrub, is logged here —
nothing is silently dropped. `hub/brain_merge.py` and `hub/inbox_ingest.py` both
append new entries automatically; this file is the template/header they append to.

Entry shape:

```
## <timestamp> — QUARANTINE (<provider>)
- file: hub/quarantine/<provider>/<date>-<filename>
- reason: secret-scan | imperative-scan | schema-invalid | parse-error
- detail: <masked match or offending line>
```

or, for a blocked republish:

```
## <timestamp> — PUBLISH BLOCKED (egress scrub)
- reason: secret found in packet: <masked match>
- action: rotate the secret, then re-run snapshot_publish.py
```
