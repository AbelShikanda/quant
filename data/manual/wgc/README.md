# Manual central-bank gold holdings

This directory is for the **human-downloaded monthly WGC/IMF XLSX** used as a diagnostic input.

Expected filename:

```text
world_official_gold_holdings.xlsx
```

Rules:

- Do **not** commit the XLSX. It is ignored by `.gitignore`.
- Download it manually from the publisher. Do not scrape or bypass access controls.
- Record the publication month, download date, source URL, and file hash in
  `refresh_event.json`.
- The validator checks only mechanical properties: presence, parseability, age, schema, and the
  reminder/refresh event.
- Content correctness is **HUMAN_JUDGMENT**.
- Weight in the v1 entry score: **0.00**.
- **NOT consumed by the overseer (§9) in v1.**

Required label for reports:

> derived from human-downloaded file; weight in entry score = 0; NOT consumed by the overseer (§9) in v1.

Example `refresh_event.json`:

```json
{
  "last_refresh_utc": "2026-11-11T00:00:00Z",
  "publication_month": "2026-10",
  "source_url": "https://www.gold.org/...",
  "sha256": "replace-with-real-sha256",
  "downloaded_by": "human"
}
```

If the file is absent or stale, the validator reports `HUMAN_JUDGMENT`, not fabricated data.
