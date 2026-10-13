# Governed document context V1

**Status:** `IAOS_GOVERNED_DOCUMENT_CONTEXT_V1=FROZEN`

```ini
BACKEND_HEAD=133c86e
HARNESS_HEAD=a248f3f
UI_HEAD=09a4a0e

PDF_CONTEXT=PASS
IMAGE_CONTEXT=PASS
REAL_AGNES_RESPONSE=PASS
LIVE_SMOKE_EVIDENCE=PRECOMMIT_SAME_FUNCTIONAL_CODE
CURRENT_CHECKPOINT_REPEAT_SMOKE=NOT_REQUIRED
NO_GOVERNANCE_CHANGE=PASS
```

The live PDF/image smoke is retained as valid evidence because the subsequent commits changed dependency
declaration, isolated-copy test hygiene, and UI composition only; they did not change the inference path.

This slice supports one governed PDF or image per manual skill request. The feature layer receives bytes only after
`fs.read_bytes` authorization and bound-handle execution. Extension rejection happens before any governed read and is
an early reject, not a read or authority grant.

```ini
DOCUMENT_MAX_BYTES=10MB_FEATURE_LAYER_LIMIT
PROVIDER_LIMIT=UNKNOWN
FS_READ_MEMORY_LIMIT=NOT_ENFORCED_PRE_READ
DOCUMENT_MODEL_CONTEXT_LIMIT=100K_CHARS_AGGREGATE
PDF_OCR=NO
```

PDF text is extracted in memory with `pypdf`; images are attached as the authorized bytes. OCR, multi-document input,
and advanced PDF handling are intentionally outside this V1 boundary.
