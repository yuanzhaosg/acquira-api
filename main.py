import os, json, re, base64, tempfile, shutil
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 8000

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
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

SCORING_SYSTEM_PROMPT = """You are an expert childcare acquisition analyst for Acquira. You receive structured data extracted from a childcare centre deal and score it across 10 dimensions.

SCORING RULES:
1. Return ONLY valid JSON. No preamble, no markdown, no explanation outside the JSON.
2. Return all numbers as plain JSON numbers. Never use a leading + sign.
3. Score each dimension from 0-10. Start each dimension at 5.0 (neutral).
4. Apply point adjustments based on signals. Show every adjustment with reasoning.
5. Never invent data. If a field is null, treat it as unknown.

CRITICAL QUALITY RULES:
A. Every signal reasoning MUST quote the actual number from the extracted data.
B. The analyst_summary MUST name the centre, suburb, and reference at least 3 specific metrics.
C. Conditionals must be specific and verifiable.
D. Dimension summaries must be 2-3 sentences specific to this deal.

WEIGHTS:
D1 Occupancy & Demand Quality       20%
D2 Staffing & Labour Resilience      18%
D3 Revenue & Pricing Power           12%
D4 Profitability & Cashflow          12%
D5 Lease & Property Economics        10%
D6 Regulatory & Quality Profile       8%
D7 Market & Competitive Position      8%
D8 Valuation & Deal Structure         8%
D9 Management & Systems               2%
D10 Upside Levers                     2%

POINT TABLE:
D1: +2.0 occ>=75%, +1.0 occ 65-74%, 0.0 occ 55-64%, -1.5 occ 45-54%, -3.0 occ<45%
D2: +2.0 labour<55%, +1.0 55-60%, 0.0 60-65%, -1.0 65-70%, -2.0 70-75%, -3.0 >75%
D4: +2.0 EBITDA margin>=20%, +1.0 15-19%, 0.0 10-14%, -1.0 5-9%, -2.0 <5%, -3.0 negative
D5: +2.0 lease>=15yr, +1.0 10-14yr, 0.0 5-9yr, -2.0 2-4yr, -3.0 <2yr or expired
D6: +1.5 Exceeding NQS, +1.0 Meeting NQS, -0.5 Working Towards NQS
D8: +2.0 <2x EBITDA, +1.0 2-3x, 0.0 3-4x, -1.0 4-5x, -2.0 >5x, -1.0 POA

HARD FLAG RULES:
- lease_expired: D5 capped at 2.0, overall capped at 5.0
- labour_ratio_critical: D2 capped at 2.0
- ebitda_negative_no_ramp: D4 capped at 2.0
- occupancy_critical: D1 capped at 2.0

APPROVED HARD FLAG IDs:
lease_expired, lease_critical, labour_ratio_critical, occupancy_critical,
ebitda_negative_no_ramp, multi_site_labour_distortion, demolition_clause,
assignment_consent_required, related_party_lease

Return this exact JSON structure:
{
  "scoring_version": "1.3",
  "scoring_timestamp": "",
  "centre_name": "",
  "overall_score": 0.0,
  "overall_verdict": "",
  "hard_flags_triggered": [],
  "score_capped": false,
  "score_cap_reason": "",
  "dimensions": {
    "D1": {"name": "Occupancy & Demand Quality", "weight": 0.20, "raw_score": 0.0, "weighted_score": 0.0, "signals": [], "summary": ""},
    "D2": {"name": "Staffing & Labour Resilience", "weight": 0.18, "raw_score": 0.0, "weighted_score": 0.0, "signals": [], "summary": ""},
    "D3": {"name": "Revenue & Pricing Power", "weight": 0.12, "raw_score": 0.0, "weighted_score": 0.0, "signals": [], "summary": ""},
    "D4": {"name": "Profitability & Cashflow", "weight": 0.12, "raw_score": 0.0, "weighted_score": 0.0, "signals": [], "summary": ""},
    "D5": {"name": "Lease & Property Economics", "weight": 0.10, "raw_score": 0.0, "weighted_score": 0.0, "signals": [], "summary": ""},
    "D6": {"name": "Regulatory & Quality Profile", "weight": 0.08, "raw_score": 0.0, "weighted_score": 0.0, "signals": [], "summary": ""},
    "D7": {"name": "Market & Competitive Position", "weight": 0.08, "raw_score": 0.0, "weighted_score": 0.0, "signals": [], "summary": ""},
    "D8": {"name": "Valuation & Deal Structure", "weight": 0.08, "raw_score": 0.0, "weighted_score": 0.0, "signals": [], "summary": ""},
    "D9": {"name": "Management & Systems", "weight": 0.02, "raw_score": 0.0, "weighted_score": 0.0, "signals": [], "summary": ""},
    "D10": {"name": "Upside Levers", "weight": 0.02, "raw_score": 0.0, "weighted_score": 0.0, "signals": [], "summary": ""}
  },
  "conditionals": [],
  "analyst_summary": ""
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
    work_dir = tempfile.mkdtemp(prefix='acquira-')
    try:
        # ── 1. Download from Supabase Storage ────────────
        response = supabase.storage.from_('uploads').download(req.storagePath)
        file_bytes = response
        filename = req.filename.lower()

        combined_text = ''
        source_files = []
        file_classes = {}

        if filename.endswith('.zip'):
            zip_path = os.path.join(work_dir, 'upload.zip')
            with open(zip_path, 'wb') as f:
                f.write(file_bytes)

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
                text = await extract_scanned_pdf_text(pdf_path, 'Information Memorandum')
            combined_text = text

        else:
            raise HTTPException(status_code=400, detail='Unsupported file type. Upload a PDF or ZIP.')

        if not combined_text.strip():
            raise HTTPException(status_code=422, detail='Could not extract text from file.')

        # ── 2. Extraction ─────────────────────────────────
        extraction_response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Extract structured data from this childcare centre document.\n\nSource files: {', '.join(source_files)}\n\nCONTENT:\n{combined_text[:60000]}"
            }]
        )
        extracted_text = clean_json(extraction_response.content[0].text)
        extracted = json.loads(extracted_text)

        # ── 3. Scoring ────────────────────────────────────
        scoring_response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SCORING_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Score this childcare centre deal.\n\nEXTRACTED DATA:\n{json.dumps(extracted, indent=2)}"
            }]
        )
        scored_text = clean_json(scoring_response.content[0].text)
        scored = json.loads(scored_text)

        # Clean up storage
        try:
            supabase.storage.from_('uploads').remove([req.storagePath])
        except Exception:
            pass

        return {
            "success": True,
            "extracted": extracted,
            "scored": scored,
            "meta": {"source_files": source_files, "file_classes": file_classes}
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Pipeline error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
