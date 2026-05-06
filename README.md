# Acquira API Ops Notes

## Verifying PDF image-table extraction locally

PDF pages that contain embedded financial tables, such as scanned Profit and Loss pages, use Anthropic vision extraction after sparse high-value page detection.

Required environment:

```bash
export ANTHROPIC_API_KEY=...
```

Smoke-test provider authentication without using client documents:

```bash
python3 scripts/qa_extract_documents.py --vision-smoke-test --out /tmp/acquira-vision-smoke.json
```

Run the real document QA harness:

```bash
python3 scripts/qa_extract_documents.py \
  --pdf '/Users/yuanyuanzhao/Desktop/IMChildcareSHCCC_signed(1).pdf' \
  --xlsx "/Users/yuanyuanzhao/Desktop/20260316 - Jenny's Norlane - Databook vSENT[1].xlsx" \
  --out /tmp/acquira-extraction-qa.json
```

Expected Surrey Hills IM result with valid provider auth:

- 15 PDF pages seen.
- Pages 12 and 13 detected as high-value financial pages.
- Vision fallback invoked for pages 12 and 13.
- P&L image-table text extracted with page refs and `EXTRACTION_METHOD: pdf_vision`.

If auth is missing or invalid, the QA JSON reports `vision_provider.auth_status` as `missing_api_key` or `invalid_auth`, and PDF warnings explain that image-table extraction could not complete. The API key value is never logged or returned.
