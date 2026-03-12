import os, json, re, base64, tempfile, shutil
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import anthropic
import pdfplumber
import fitz  # pymupdf
import openpyxl
import zipfile
from supabase import create_client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your Vercel URL in production
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

MODEL      = "claude-sonnet-4-20250514"
MAX_TOKENS = 8000

client   = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

# ── Prompts ───────────────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """You are an expert childcare acquisition analyst for Acquira, an Australian deal intelligence platform. Your task is to read an Information Memorandum (IM) for a childcare centre and extract structured data into a strict JSON format.

RULES:
1. Return ONLY valid JSON. No preamble, no explanation, no markdown code fences.
2. Never invent or estimate numbers. If a field is not present in the IM, set it to null.
3. Use null (not zero, not "N/A", not "unknown") for any field you cannot find.
4. Be conservative: if you are uncertain whether a number is correct, set it to null and add the field name to the missing_fields array.
5. All dollar amounts in AUD as plain numbers (e.g. 1250000 not "$1.25M"). All percentages as plain numbers 0-100 (e.g. 78.5 not 0.785). All dates as ISO 8601 strings (YYYY-MM-DD).
6. You are state-aware: VIC, NSW, and QLD have different kinder/preschool funding regimes, regulatory bodies, and wage award rates.
7. For occupancy: use the most recent data available. Priority order: (1) most recent 4-week average, (2) most recent month, (3) annual average.
8. For financials: extract ALL years available (FY23, FY24, FY25). Prefer audited or management accounts over vendor summaries.
9. RATIO CALCULATIONS - always calculate from raw numbers:
   - labour_ratio_pct = (total_labour_cost / revenue) x 100
   - rent_ratio_pct = (rent_pa / revenue) x 100
   - ebitda_margin_pct = (ebitda / revenue) x 100
   - ebitda = revenue - total_labour_cost - rent_pa - other_operating_costs
10. For asking price: if the IM states "Price on Application", "POA" -> set asking_price to null and add "asking_price_poa": true to meta.
11. Flag unusual patterns or vendor-inflated items in the anomalies array.
12. Return all numbers as plain JSON numbers. Never use a leading + sign.
13. labour_ratio_pct >100 or <20 is almost certainly an error - recheck and set to null if unresolvable.

VIC-SPECIFIC CONTEXT:
- VKF (Vic Kinder Funding) = State Government kindergarten subsidy, counted as revenue.
- NQS ratings: Excellent > Exceeding NQS > Meeting NQS > Working Towards NQS > Significant Improvement Required.
- Owner-operator director wages are a standard addback item.

Return this exact JSON structure (set fields to null if not found):

{
  "meta": {
    "extraction_version": "1.1",
    "extraction_date": "",
    "source_type": "pdf_im",
    "source_files": [],
    "data_quality": "MEDIUM",
    "missing_fields_count": 0,
    "missing_fields": [],
    "asking_price_poa": false,
    "anomalies": []
  },
  "centre": {
    "name": null, "trading_name": null, "address": null, "suburb": null,
    "state": null, "postcode": null, "lga": null, "operator": null,
    "operator_type": "unknown", "licensed_places": null, "nqs_rating": null,
    "nqs_date": null, "service_approval_number": null
  },
  "occupancy": {
    "current_month_pct": null, "avg_4wk_pct": null, "avg_13wk_pct": null,
    "avg_52wk_pct": null, "peak_pct": null, "peak_week": null,
    "fy23_avg_pct": null, "fy24_avg_pct": null, "fy25_avg_pct": null,
    "trend_fy23_to_fy25": null, "waitlist_depth": null, "waitlist_notes": null
  },
  "financials": {
    "primary_year": "FY25",
    "fy23": {"revenue": null, "total_labour_cost": null, "rent_pa": null, "ebitda": null, "labour_ratio_pct": null, "rent_ratio_pct": null, "ebitda_margin_pct": null},
    "fy24": {"revenue": null, "total_labour_cost": null, "rent_pa": null, "ebitda": null, "labour_ratio_pct": null, "rent_ratio_pct": null, "ebitda_margin_pct": null},
    "fy25": {"revenue": null, "total_labour_cost": null, "rent_pa": null, "ebitda": null, "labour_ratio_pct": null, "rent_ratio_pct": null, "ebitda_margin_pct": null},
    "ebitda_3yr_average": null, "revenue_trend": null, "labour_trend": null,
    "asking_price": null, "asking_price_ebitda_multiple": null,
    "vendor_excess_wages_claim": null, "addbacks_total": null, "normalised_ebitda": null
  },
  "lease": {
    "commencement_date": null, "expiry_date": null, "status": "UNKNOWN",
    "term_years": null, "options": null, "remaining_term_years": null,
    "base_rent_pa_fy25": null, "rent_review_type": null, "rent_review_detail": null,
    "turnover_rent_clause": null, "assignment_clause": null,
    "demolition_redevelopment_clause": null, "make_good_obligations": null,
    "outgoings_type": null, "permitted_use": null, "lessor": null, "lessee": null
  },
  "hard_flags": [],
  "key_ratios": {
    "occupancy_latest_4wk_pct": null, "occupancy_peak_pct": null,
    "revenue_fy25": null, "ebitda_fy25": null, "ebitda_margin_fy25_pct": null,
    "labour_ratio_fy25_pct": null, "rent_ratio_fy25_pct": null,
    "ebitda_3yr_avg": null, "rent_pa_fy25": null, "licensed_places": null,
    "asking_price": null, "ebitda_multiple": null
  }
}"""

# v2 scoring prompt — aligned with scoring-v2.ts and route.ts schema
SCORING_SYSTEM_PROMPT = """You are an expert childcare acquisition analyst for Acquira. You receive structured data extracted from a childcare centre Information Memorandum (IM) and score it across 17 dimensions.

ABSOLUTE OUTPUT RULES:
1. Return ONLY valid JSON. No preamble, no markdown fences, no text outside the JSON.
2. All numbers must be plain JSON numbers. Never use a leading + sign.
3. Use null for any value that cannot be determined — never omit a key.
4. temperature is 0 — your output must be fully deterministic given the same input.

SCORING PHILOSOPHY:
- Start each dimension at 5.0 (neutral). Apply point adjustments based on signals.
- Every adjustment MUST quote the actual number from the data.
- Dimension summaries must be 2-3 sentences specific to THIS deal.

DIMENSION WEIGHTS (server enforces these):
occupancy_demand:        0.15
profitability_cashflow:  0.15
revenue_pricing:         0.08
staffing_resilience:     0.08
lease_economics:         0.08
valuation_structure:     0.08
market_position:         0.07
management_systems:      0.06
regulatory_quality:      0.05
upside_levers:           0.05
ccs_risk:                0.03
lease_tail:              0.03
capex_liability:         0.02
staff_qualification_mix: 0.02
fee_benchmarking:        0.02
operator_quality:        0.02
enrolment_trend:         0.01

POINT TABLE:
occupancy_demand: +2.0 occ>=75%, +1.0 65-74%, 0.0 55-64%, -1.5 45-54%, -3.0 <45%
staffing_resilience: +2.0 labour<55%, +1.0 55-60%, 0.0 60-65%, -1.0 65-70%, -2.0 70-75%, -3.0 >75%
profitability_cashflow: +2.0 margin>=20%, +1.0 15-19%, 0.0 10-14%, -1.0 5-9%, -2.0 <5%, -3.0 negative
lease_economics: +0.5 rent<15% rev, 0.0 15-20%, -0.5 20-25%, -1.5 >25%
lease_tail: +2.0 tenure>=15yr, +1.0 10-14yr, 0.0 5-9yr, -2.0 2-4yr, -3.0 <2yr or expired
regulatory_quality: +1.5 Exceeding NQS, +1.0 Meeting NQS, -0.5 Working Towards, -2.0 SIR
valuation_structure: +2.0 <2x EBITDA, +1.0 2-3x, 0.0 3-4x, -1.0 4-5x, -2.0 >5x, -1.0 POA

DEAL-BREAKER FLAGS to evaluate (set triggered true/false):
occupancy_critical (occ<50%), occupancy_warning (50-65%), rent_ratio_danger (rent>15% rev),
labour_ratio_danger (labour>65% rev), ebitda_negative, lease_short_no_options (<3yr, no options),
lease_short_with_options (<3yr with options), owner_operator_dependency,
nqs_working_towards, capex_high, ccs_exposure_high, valuation_premium (>4x EBITDA turnaround)

Return this exact JSON schema (null for unknowns, never omit keys):
{
  "centre_name": string,
  "total_score": number,
  "dimensions": {
    "occupancy_demand":       {"score": 0-10, "label": string, "summary": string, "data_used": []},
    "revenue_pricing":        {"score": 0-10, "label": string, "summary": string, "data_used": []},
    "staffing_resilience":    {"score": 0-10, "label": string, "summary": string, "data_used": []},
    "profitability_cashflow": {"score": 0-10, "label": string, "summary": string, "data_used": []},
    "lease_economics":        {"score": 0-10, "label": string, "summary": string, "data_used": []},
    "regulatory_quality":     {"score": 0-10, "label": string, "summary": string, "data_used": []},
    "market_position":        {"score": 0-10, "label": string, "summary": string, "data_used": []},
    "management_systems":     {"score": 0-10, "label": string, "summary": string, "data_used": []},
    "valuation_structure":    {"score": 0-10, "label": string, "summary": string, "data_used": []},
    "upside_levers":          {"score": 0-10, "label": string, "summary": string, "data_used": []},
    "ccs_risk":               {"score": 0-10, "label": "CCS / Subsidy Risk", "summary": string, "data_used": [], "detail": {"estimated_ccs_dependent_pct": null, "activity_test_exposure": "unknown", "subsidy_cliff_note": string}},
    "lease_tail":             {"score": 0-10, "label": "Lease Tail", "summary": string, "data_used": [], "detail": {"years_remaining": null, "options_available": null, "option_years_each": null, "total_potential_tenure": null, "landlord_obligations_noted": null}},
    "capex_liability":        {"score": 0-10, "label": "Renovation / CAPEX Liability", "summary": string, "data_used": [], "detail": {"fit_out_age_years": null, "capex_mentioned_in_im": false, "estimated_capex_risk": "unknown", "notes": string}},
    "staff_qualification_mix":{"score": 0-10, "label": "Staff Qualification Mix", "summary": string, "data_used": [], "detail": {"degree_qualified_pct": null, "certificate_pct": null, "diploma_pct": null, "wage_trajectory_risk": "unknown"}},
    "fee_benchmarking":       {"score": 0-10, "label": "Fee Benchmarking", "summary": string, "data_used": [], "detail": {"centre_daily_fee": null, "suburb_median_fee": null, "fee_position": "unknown", "pricing_power_note": string}},
    "operator_quality":       {"score": 0-10, "label": "Operator Quality Signal", "summary": string, "data_used": [], "detail": {"nqs_rating": "unknown", "last_assessment_date": null, "months_since_assessment": null, "exceeding_areas_count": null, "active_conditions": null, "active_notices": null, "compliance_note": string}},
    "enrolment_trend":        {"score": 0-10, "label": "Enrolment Trend & Waitlist", "summary": string, "data_used": [], "detail": {"current_occupancy_pct": null, "trend_direction": "unknown", "waitlist_depth": "unknown", "occupancy_snapshot_date": null, "trend_note": string}}
  },
  "deal_breaker_flags": {
    "any_triggered": false,
    "flags": [{"id": string, "triggered": bool, "severity": "critical"|"high", "label": string, "reason": string}]
  },
  "audit_trail": {
    "fields_missing": [],
    "confidence": "medium",
    "confidence_note": string
  },
  "verdict": {
    "category": "passive_hold"|"turnaround"|"distressed"|"pass",
    "one_liner": string,
    "recommended_buyer_profile": string
  }
}"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def classify_file(filename: str) -> str:
    f = filename.lower()
    if any(x in f for x in ['p&l', 'p_l', 'profit', 'loss']): return 'pl_excel'
    if any(x in f for x in ['occupancy', 'utilisation', 'utilization']): return 'occupancy_excel'
    if 'transaction' in f: return 'transaction_excel'
    if 'payroll' in f: return 'payroll_excel'
    if any(x in f for x in ['lease', 'deed of variation', 'tenancy']): return 'lease_pdf'
    if 'service approval' in f: return 'service_approval_pdf'
    if any(x in f for x in ['nqs', 'acecqa', 'rating']): return 'nqs_pdf'
    if f.endswith('.pdf'): return 'im_pdf'
    return 'unknown'

def extract_pdf_text(pdf_path: str) -> str:
    try:
        text = ''
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + '\n'
        return text[:80000]
    except Exception:
        return ''

def is_pdf_scanned(text: str) -> bool:
    trimmed = text.strip()
    if len(trimmed) < 200:
        return True
    has_dollars = bool(re.search(r'\$[\d,]+|[\d,]+\s*(revenue|ebitda|wages|labour|rent)', trimmed, re.I))
    avg_chars = len(trimmed) / max(len(trimmed.split('\n\n')), 1)
    return avg_chars < 300 and not has_dollars

async def extract_scanned_pdf_text(pdf_path: str, purpose: str) -> str:
    try:
        doc = fitz.open(pdf_path)
        images = []
        for i, page in enumerate(doc):
            if i >= 60: break
            if len(page.get_text().strip()) < 30 and i > 3: continue
            mat = fitz.Matrix(1.5, 1.5)
            pix = page.get_pixmap(matrix=mat)
            images.append(base64.standard_b64encode(pix.tobytes('png')).decode())
            if len(images) >= 30: break

        if not images:
            return ''

        content = [
            *[{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}} for img in images],
            {"type": "text", "text": f"Extract all text content from these document pages. This is a {purpose}. Return plain text only, preserving structure and numbers accurately."}
        ]

        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            temperature=0,
            messages=[{"role": "user", "content": content}]
        )
        return response.content[0].text if response.content[0].type == 'text' else ''
    except Exception as e:
        print(f"Vision extraction failed: {e}")
        return ''

def extract_excel_text(xlsx_path: str) -> str:
    try:
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        out = []
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            out.append(f'Sheet: {sheet}')
            for row in ws.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    out.append(','.join(str(v) if v is not None else '' for v in row))
        return '\n'.join(out[:500])
    except Exception:
        return ''

def clean_json(text: str) -> str:
    text = re.sub(r'^```json\s*', '', text, flags=re.M)
    text = re.sub(r'^```\s*', '', text, flags=re.M)
    text = re.sub(r'```$', '', text, flags=re.M)
    text = re.sub(r':\s*\+([0-9])', r': \1', text)
    return text.strip()

# ── SSE helper ────────────────────────────────────────────────────────────────

def sse_event(event: str, data: dict) -> str:
    """Format a Server-Sent Event string."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

# ── Request model ─────────────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    storagePath: str
    filename: str

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/pipeline")
async def pipeline(req: PipelineRequest):
    """
    Streaming pipeline endpoint using Server-Sent Events.
    Emits progress events then a final 'complete' event with full results.

    Event types:
      progress  — { step, label, detail? }
      error     — { message }
      complete  — { extracted, scored, meta }
    """

    async def generate():
        work_dir = tempfile.mkdtemp(prefix='acquira-')
        try:
            # ── Step 1: Download & parse ──────────────────────────────────
            yield sse_event("progress", {
                "step": 1, "total": 5,
                "label": "Downloading file",
                "detail": req.filename
            })

            response    = supabase.storage.from_('uploads').download(req.storagePath)
            file_bytes  = response
            filename    = req.filename.lower()

            combined_text = ''
            source_files  = []
            file_classes  = {}

            if filename.endswith('.zip'):
                zip_path = os.path.join(work_dir, 'upload.zip')
                with open(zip_path, 'wb') as f:
                    f.write(file_bytes)

                yield sse_event("progress", {
                    "step": 1, "total": 5,
                    "label": "Unpacking ZIP",
                    "detail": "Reading data room files"
                })

                with zipfile.ZipFile(zip_path, 'r') as zf:
                    for entry_name in zf.namelist():
                        base_name = Path(entry_name).name
                        if not base_name or base_name.startswith('.'): continue

                        file_class = classify_file(base_name)
                        file_classes[base_name] = file_class
                        if file_class == 'unknown': continue

                        entry_path = os.path.join(work_dir, re.sub(r'[^a-zA-Z0-9._-]', '_', base_name))
                        with open(entry_path, 'wb') as f:
                            f.write(zf.read(entry_name))
                        source_files.append(base_name)

                        if base_name.endswith('.pdf'):
                            text = extract_pdf_text(entry_path)
                            if is_pdf_scanned(text):
                                yield sse_event("progress", {
                                    "step": 1, "total": 5,
                                    "label": "Reading scanned PDF",
                                    "detail": base_name
                                })
                                text = await extract_scanned_pdf_text(entry_path, file_class.replace('_', ' '))
                            combined_text += f'\n\n=== {base_name} ({file_class}) ===\n{text}'
                        elif base_name.endswith(('.xlsx', '.xls')):
                            combined_text += f'\n\n=== {base_name} ({file_class}) ===\n{extract_excel_text(entry_path)}'

            elif filename.endswith('.pdf'):
                pdf_path = os.path.join(work_dir, 'input.pdf')
                with open(pdf_path, 'wb') as f:
                    f.write(file_bytes)
                source_files.append(req.filename)

                text = extract_pdf_text(pdf_path)
                if is_pdf_scanned(text):
                    yield sse_event("progress", {
                        "step": 1, "total": 5,
                        "label": "Reading scanned PDF",
                        "detail": "Using vision extraction"
                    })
                    text = await extract_scanned_pdf_text(pdf_path, 'Information Memorandum')
                combined_text = text
            else:
                yield sse_event("error", {"message": "Unsupported file type. Upload a PDF or ZIP."})
                return

            if not combined_text.strip():
                yield sse_event("error", {"message": "Could not extract text from file."})
                return

            # ── Step 2: Extract ───────────────────────────────────────────
            yield sse_event("progress", {
                "step": 2, "total": 5,
                "label": "Extracting metrics",
                "detail": f"Reading {len(source_files)} file{'s' if len(source_files) != 1 else ''} · {len(combined_text):,} characters"
            })

            extraction_response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=0,   # deterministic extraction
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"Extract structured data from this childcare centre document.\n\nSource files: {', '.join(source_files)}\n\nCONTENT:\n{combined_text[:60000]}"
                }]
            )
            extracted_text = clean_json(extraction_response.content[0].text)
            extracted      = json.loads(extracted_text)

            centre_name = extracted.get('centre', {}).get('name') or 'centre'

            # ── Step 3: Score ─────────────────────────────────────────────
            yield sse_event("progress", {
                "step": 3, "total": 5,
                "label": "Scoring 17 dimensions",
                "detail": f"Analysing {centre_name}"
            })

            scoring_response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=0,   # deterministic scoring — same IM always same score
                system=SCORING_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"Score this childcare centre acquisition.\n\nEXTRACTED DATA:\n{json.dumps(extracted, indent=2)}"
                }]
            )
            scored_text = clean_json(scoring_response.content[0].text)
            scored      = json.loads(scored_text)

            # ── Step 4: Clean up storage ──────────────────────────────────
            yield sse_event("progress", {
                "step": 4, "total": 5,
                "label": "Generating report",
                "detail": "Mapping competitors · Building analysis"
            })

            try:
                supabase.storage.from_('uploads').remove([req.storagePath])
            except Exception:
                pass

            # ── Step 5: Complete ──────────────────────────────────────────
            yield sse_event("progress", {
                "step": 5, "total": 5,
                "label": "Complete",
                "detail": "Analysis ready"
            })

            yield sse_event("complete", {
                "success": True,
                "extracted": extracted,
                "scored": scored,
                "meta": {"source_files": source_files, "file_classes": file_classes}
            })

        except Exception as e:
            print(f"Pipeline error: {e}")
            yield sse_event("error", {"message": str(e)})
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering on Railway
        }
    )
