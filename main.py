import os, json, re, base64, tempfile, shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import anthropic
import pdfplumber
import fitz  # pymupdf
import openpyxl
import zipfile
import docx as python_docx   # python-docx
import xlrd                   # legacy .xls support
from supabase import create_client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your Vercel URL in production
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

MODEL      = "claude-sonnet-4-20250514"
MAX_TOKENS = 12000

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
- ADDBACK RULES: Normalised EBITDA = reported EBITDA + verified addbacks.
  Standard addbacks to extract and itemise:
  (1) Owner/director salary above market replacement cost (~$80-110K for a centre manager)
  (2) One-off non-recurring expenses clearly stated in the IM
  (3) Personal expenses run through the business (must be stated in IM)
  Do NOT invent addbacks. Only include what is explicitly stated in the documents.
- Always extract BOTH: reported_ebitda (as stated) AND normalised_ebitda (after addbacks).
- If addbacks are claimed but unverified, flag them in anomalies with reason.
- Labour ratio and rent ratio should be calculated on REPORTED revenue, not normalised.

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
    "addbacks": {
      "owner_salary_addback": null,
      "owner_salary_note": null,
      "other_addbacks": [],
      "addbacks_total": null,
      "reported_ebitda": null,
      "normalised_ebitda": null,
      "normalised_ebitda_multiple": null,
      "addback_confidence": "none"
    }
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

SCORING_SYSTEM_PROMPT = """You are an expert childcare acquisition analyst for Acquira. You receive structured data extracted from a childcare centre Information Memorandum (IM) and score it across 17 dimensions.

ABSOLUTE OUTPUT RULES:
1. Return ONLY valid JSON. No preamble, no markdown fences, no text outside the JSON.
2. All numbers must be plain JSON numbers. Never use a leading + sign.
3. Use null for any value that cannot be determined — never omit a key.
4. temperature is 0 — your output must be fully deterministic given the same input.

SCORING PHILOSOPHY:
- Score each dimension on a 0-10 scale using the rubric below.
- 5.0 = industry average / neutral. 7.0+ = genuinely good. 9.0+ = exceptional.
- Every score MUST quote the actual number from the data in its summary.
- Dimension summaries must be 2-3 sentences specific to THIS deal.
- The server will recalculate total_score using weights — set it to 0 in your output.
- If data is missing for a dimension, score it 5.0 (neutral) and note it in summary.

DIMENSION WEIGHTS (for reference — server recalculates):
occupancy_demand:        0.15
profitability_cashflow:  0.15
revenue_pricing:         0.08
staffing_resilience:     0.08
lease_economics:         0.08
valuation_structure:     0.08
market_position:         0.07
management_systems:      0.04
regulatory_quality:      0.05
upside_levers:           0.03
ccs_risk:                0.07
lease_tail:              0.03
capex_liability:         0.02
staff_qualification_mix: 0.02
fee_benchmarking:        0.02
operator_quality:        0.02
enrolment_trend:         0.01

SCORING RUBRIC (0-10 per dimension):

occupancy_demand:
  9-10: occ >= 90%, strong waitlist
  7-8:  occ 75-89%, some waitlist
  5-6:  occ 60-74%, stable
  3-4:  occ 45-59%, declining or flat
  1-2:  occ < 45%, critical

profitability_cashflow:
  9-10: EBITDA margin >= 25%, positive 3yr trend
  7-8:  margin 18-24%
  5-6:  margin 10-17%
  3-4:  margin 3-9%
  1-2:  margin < 3% or negative
  IMPORTANT: If 3yr average EBITDA is negative even when FY25 is positive,
  cap this score at 4.0 maximum and note the sustained loss history.
  Use normalised_ebitda if addbacks are verified.
  Stress test: model a +10% wage cost increase (apply to total_labour_cost).
  If the resulting EBITDA drops below 0, flag HIGH RISK: Wage stress wipes EBITDA.
  If the resulting defensive yield (stressed EBITDA / asking_price) falls below 4.5%, flag HIGH RISK: Defensive yield below 4.5% under wage stress.
  State the stressed EBITDA and stressed yield in your summary.

staffing_resilience:
  9-10: labour ratio < 52%
  7-8:  labour 52-58%
  5-6:  labour 58-65%
  3-4:  labour 65-72%
  1-2:  labour > 72%

lease_economics:
  9-10: rent < 10% of revenue
  7-8:  rent 10-15%
  5-6:  rent 15-20%
  3-4:  rent 20-25%
  1-2:  rent > 25%

lease_tail:
  9-10: >= 15 years remaining tenure (incl options)
  7-8:  10-14 years
  5-6:  5-9 years
  3-4:  2-4 years
  1-2:  < 2 years or expired

regulatory_quality:
  9-10: Exceeding NQS, recent assessment
  7-8:  Meeting NQS
  5-6:  Meeting NQS, assessment overdue
  3-4:  Working Towards NQS
  1-2:  SIR or active compliance notices

valuation_structure:
  9-10: <= 2x EBITDA
  7-8:  2-3x EBITDA
  5-6:  3-4x EBITDA
  3-4:  4-5x EBITDA
  1-2:  > 5x EBITDA or POA

market_position:
  9-10: < 1.5 competitors per licensed place within 3km, strong demographics
  7-8:  balanced supply zone
  5-6:  average competition
  3-4:  oversupplied market
  1-2:  heavily oversupplied
  Saturation penalty: apply a haircut to your initial score based on competitor count within 3km:
    1-2 competitors: -10% (multiply score by 0.90)
    3 competitors:   -15% (multiply score by 0.85)
    4-5 competitors: -25% (multiply score by 0.75)
    6+ competitors:  -35% (multiply score by 0.65)
  State the multiplier used in your summary.
  Approved-but-unbuilt centres: if the IM or context mentions DA-approved or under-construction centres nearby,
  apply an additional -20% penalty and flag Pipeline supply risk.

management_systems:
  7-8:  professional management team, strong systems, not owner-dependent
  5-6:  semi-professional, some key-person risk
  3-4:  owner-operator, high transition risk
  1-2:  sole operator, no documented systems

revenue_pricing:
  7-8:  fees above suburb median, strong CCS mix, multiple revenue streams
  5-6:  fees at market, standard CCS dependency
  3-4:  fees below market, limited pricing power
  1-2:  fees significantly below market, no uplift path

upside_levers:
  7-8:  clear fee uplift headroom, occupancy expansion possible, kinder funding upside
  5-6:  some upside, limited by market or occupancy ceiling
  3-4:  limited upside, near capacity or market ceiling
  1-2:  no meaningful upside identified

ccs_risk:
  7-8:  low CCS cliff exposure, diverse family demographics
  5-6:  moderate CCS dependency
  3-4:  high CCS dependency, activity test risk
  1-2:  critical CCS exposure
  Cohort risk: estimate the % of revenue derived from the 3-5 age group (kindy/preschool cohort).
  If that % is >40% AND the IM mentions Pre-Prep, kindy, or preschool competition nearby,
  flag HIGH RISK: Systematic cohort loss risk. Add cohort_35_pct_estimated to the detail block.
  Revenue topology: classify the CCS revenue routing as one of:
    Direct (government pays centre directly) → LOW risk,
    Parent-routed (subsidy flows via parent) → MEDIUM risk,
    Program-dependent (tied to a specific govt program that could end) → HIGH risk.
  State the classification and rationale in your summary.

capex_liability:
  9-10: new fit-out, no CAPEX required
  7-8:  < 3 years old, minimal CAPEX
  5-6:  3-7 years, routine maintenance only
  3-4:  7-12 years, significant refresh likely
  1-2:  > 12 years or major CAPEX flagged

staff_qualification_mix:
  7-8:  > 35% degree qualified, low wage trajectory risk
  5-6:  20-35% degree qualified
  3-4:  < 20% degree qualified, high wage risk
  1-2:  unknown or significant compliance risk

fee_benchmarking:
  7-8:  fees >= suburb median, room to increase
  5-6:  fees within 5% of median
  3-4:  fees > 5% below median
  1-2:  fees significantly below market
  Fee ceiling risk: if the centre's daily fee is at or above the suburb's top quartile (estimated as median + 10%)
  AND no premium differentiation is noted (e.g. no Reggio, Forest School, specialty program, Exceeding NQS),
  flag Fee ceiling risk and cap this dimension score at 5.0 maximum.

operator_quality:
  9-10: Exceeding NQS, no conditions/notices, strong compliance history
  7-8:  Meeting NQS, clean record
  5-6:  Meeting NQS, minor issues resolved
  3-4:  Working Towards NQS or unresolved conditions
  1-2:  SIR, active notices, enforcement history

enrolment_trend:
  9-10: occupancy improving, strong waitlist across age groups
  7-8:  stable at high occupancy, moderate waitlist
  5-6:  stable, no waitlist
  3-4:  declining occupancy trend
  1-2:  significant decline

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
    # Check extension first to avoid e.g. 'lease terms.docx' -> lease_pdf
    if f.endswith(('.xlsx', '.xls', '.csv')):
        if any(x in f for x in ['p&l', 'p_l', 'profit', 'loss']): return 'pl_excel'
        if any(x in f for x in ['occupancy', 'utilisation', 'utilization']): return 'occupancy_excel'
        if 'transaction' in f: return 'transaction_excel'
        if 'payroll' in f: return 'payroll_excel'
        return 'pl_excel'
    if f.endswith('.pdf'):
        if any(x in f for x in ['lease', 'deed', 'tenancy']): return 'lease_pdf'
        if 'service approval' in f: return 'service_approval_pdf'
        if any(x in f for x in ['nqs', 'acecqa', 'rating']): return 'nqs_pdf'
        return 'im_pdf'
    if f.endswith('.docx'):
        if any(x in f for x in ['lease', 'deed', 'tenancy']): return 'lease_docx'
        return 'im_docx'
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
    """Extract text from .xlsx or legacy .xls files."""
    path_lower = xlsx_path.lower()
    try:
        if path_lower.endswith('.xls') and not path_lower.endswith('.xlsx'):
            # Legacy BIFF format — openpyxl cannot read this
            wb = xlrd.open_workbook(xlsx_path)
            out = []
            for sheet in wb.sheets():
                out.append(f'Sheet: {sheet.name}')
                for rx in range(sheet.nrows):
                    row = [str(sheet.cell_value(rx, cx)) for cx in range(sheet.ncols)]
                    line = ','.join(v for v in row if v.strip())
                    if line:
                        out.append(line)
                    if len(out) >= 1000:
                        break
            return '\n'.join(out[:1000])
        else:
            wb = openpyxl.load_workbook(xlsx_path, data_only=True)
            out = []
            for sheet in wb.sheetnames:
                ws = wb[sheet]
                out.append(f'Sheet: {sheet}')
                for row in ws.iter_rows(values_only=True):
                    if any(v is not None for v in row):
                        out.append(','.join(str(v) if v is not None else '' for v in row))
                    if len(out) >= 1000:
                        break
            return '\n'.join(out[:1000])
    except Exception as e:
        print(f"Excel extraction failed ({xlsx_path}): {e}")
        return ''

def extract_docx_text(docx_path: str) -> str:
    """Extract text from .docx using the public python-docx API."""
    try:
        doc = python_docx.Document(docx_path)
        parts = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                parts.append(text)
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                seen = []
                for c in cells:
                    if c and (not seen or c != seen[-1]):
                        seen.append(c)
                line = '\t'.join(seen)
                if line:
                    parts.append(line)
        return '\n'.join(parts)
    except Exception as e:
        print(f"DOCX extraction failed: {e}")
        return ''

def _extract_file_text(file_path: str, filename: str) -> str:
    """Route a file to the correct extractor by extension."""
    f = filename.lower()
    if f.endswith('.pdf'):
        return extract_pdf_text(file_path)
    elif f.endswith('.docx'):
        return extract_docx_text(file_path)
    elif f.endswith(('.xlsx', '.xls', '.csv')):
        return extract_excel_text(file_path)
    return ''

def clean_json(text: str) -> str:
    text = re.sub(r'^```json\s*', '', text, flags=re.M)
    text = re.sub(r'^```\s*', '', text, flags=re.M)
    text = re.sub(r'```$', '', text, flags=re.M)
    text = re.sub(r':\s*\+([0-9])', r': \1', text)
    return text.strip()

# ── SSE helper ────────────────────────────────────────────────────────────────

def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

# ── Request model ─────────────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    # Multi-file (new)
    storagePaths: Optional[list[str]] = None
    filenames:    Optional[list[str]] = None
    # Single-file (legacy — backwards compat)
    storagePath:  Optional[str]       = None
    filename:     Optional[str]       = None

    def resolved_paths(self) -> list[str]:
        if self.storagePaths:
            return self.storagePaths
        if self.storagePath:
            return [self.storagePath]
        raise ValueError("No storage path provided")

    def resolved_filenames(self) -> list[str]:
        if self.filenames:
            return self.filenames
        if self.filename:
            return [self.filename]
        return [p.split('/')[-1] for p in self.resolved_paths()]

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/pipeline")
async def pipeline(req: PipelineRequest):
    """
    Streaming pipeline endpoint using Server-Sent Events.

    Accepts single file (legacy) or multiple files/ZIP (new).
    Event types:
      progress  — { step, label, detail? }
      error     — { message }
      complete  — { extracted, scored, meta }
    """

    async def generate():
        work_dir      = tempfile.mkdtemp(prefix='acquira-')
        storage_paths = req.resolved_paths()
        all_filenames = req.resolved_filenames()

        try:
            # ── Step 1: Download & parse all files ───────────────────────
            yield sse_event("progress", {
                "step": 1, "total": 5,
                "label": "Downloading files",
                "detail": f"{len(storage_paths)} file{'s' if len(storage_paths) != 1 else ''}"
            })

            # Text budget per file class: (max_chars_per_file, max_file_count)
            # Keeps combined_text under ~120k chars even with 30 files.
            TEXT_BUDGET: dict[str, tuple[int, int]] = {
                'im_pdf':               (50000, 3),
                'im_docx':              (50000, 3),
                'pl_excel':             (10000, 10),
                'occupancy_excel':      (10000, 10),
                'transaction_excel':    (8000,  5),
                'payroll_excel':        (8000,  5),
                'lease_pdf':            (8000,  5),
                'lease_docx':           (8000,  5),
                'service_approval_pdf': (4000,  3),
                'nqs_pdf':              (4000,  3),
            }
            CLAUDE_CHAR_LIMIT = 120_000

            class_counts: dict[str, int] = {}
            combined_text = ''
            source_files:  list[str] = []
            file_classes:  dict[str, str] = {}
            skipped:       list[str] = []

            for storage_path, filename in zip(storage_paths, all_filenames):
                fname_lower = filename.lower()
                file_bytes  = supabase.storage.from_('uploads').download(storage_path)

                # ── ZIP: extract contained files ──────────────────────────
                if fname_lower.endswith('.zip'):
                    yield sse_event("progress", {
                        "step": 1, "total": 5,
                        "label": "Unpacking ZIP",
                        "detail": filename
                    })
                    zip_path = os.path.join(work_dir, 'upload.zip')
                    with open(zip_path, 'wb') as f:
                        f.write(file_bytes)

                    with zipfile.ZipFile(zip_path, 'r') as zf:
                        for entry_name in zf.namelist():
                            base_name = Path(entry_name).name
                            if not base_name or base_name.startswith('.'): continue

                            file_class = classify_file(base_name)
                            if file_class == 'unknown': continue

                            max_chars, max_count = TEXT_BUDGET.get(file_class, (4000, 3))
                            if class_counts.get(file_class, 0) >= max_count:
                                skipped.append(base_name)
                                continue

                            entry_path = os.path.join(
                                work_dir, re.sub(r'[^a-zA-Z0-9._-]', '_', base_name)
                            )
                            with open(entry_path, 'wb') as f:
                                f.write(zf.read(entry_name))

                            text = _extract_file_text(entry_path, base_name)

                            # Vision fallback for scanned PDFs
                            if base_name.lower().endswith('.pdf') and is_pdf_scanned(text):
                                yield sse_event("progress", {
                                    "step": 1, "total": 5,
                                    "label": "Reading scanned PDF",
                                    "detail": base_name
                                })
                                text = await extract_scanned_pdf_text(
                                    entry_path, file_class.replace('_', ' ')
                                )

                            if text:
                                combined_text += f'\n\n=== {base_name} ({file_class}) ===\n{text[:max_chars]}'
                                source_files.append(base_name)
                                file_classes[base_name] = file_class
                                class_counts[file_class] = class_counts.get(file_class, 0) + 1

                # ── Single file ───────────────────────────────────────────
                else:
                    file_class = classify_file(filename)
                    if file_class == 'unknown':
                        skipped.append(filename)
                        continue

                    max_chars, max_count = TEXT_BUDGET.get(file_class, (30000, 1))
                    if class_counts.get(file_class, 0) >= max_count:
                        skipped.append(filename)
                        continue

                    file_path = os.path.join(
                        work_dir, re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
                    )
                    with open(file_path, 'wb') as f:
                        f.write(file_bytes)

                    text = _extract_file_text(file_path, filename)

                    # Vision fallback for scanned PDFs
                    if fname_lower.endswith('.pdf') and is_pdf_scanned(text):
                        yield sse_event("progress", {
                            "step": 1, "total": 5,
                            "label": "Reading scanned PDF",
                            "detail": "Using vision extraction"
                        })
                        text = await extract_scanned_pdf_text(
                            file_path, file_class.replace('_', ' ')
                        )

                    if text:
                        combined_text += f'\n\n=== {filename} ({file_class}) ===\n{text[:max_chars]}'
                        source_files.append(filename)
                        file_classes[filename] = file_class
                        class_counts[file_class] = class_counts.get(file_class, 0) + 1
                    else:
                        skipped.append(filename)

            if skipped:
                print(f"[pipeline] skipped {len(skipped)} file(s): {skipped}")

            if not combined_text.strip():
                yield sse_event("error", {"message": "Could not extract text from any uploaded file."})
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
                temperature=0,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Extract structured data from this childcare centre document.\n\n"
                        f"Source files: {', '.join(source_files)}\n\n"
                        f"CONTENT:\n{combined_text[:CLAUDE_CHAR_LIMIT]}"
                    )
                }]
            )
            extracted_text = clean_json(extraction_response.content[0].text)
            extracted      = json.loads(extracted_text)
            centre_name    = extracted.get('centre', {}).get('name') or 'centre'

            # ── Step 3: Score ─────────────────────────────────────────────
            yield sse_event("progress", {
                "step": 3, "total": 5,
                "label": "Scoring 17 dimensions",
                "detail": f"Analysing {centre_name}"
            })

            scoring_response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=0,
                system=SCORING_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": (
                        "Score this childcare centre acquisition.\n\n"
                        "IMPORTANT: Use normalised_ebitda for profitability and valuation scoring "
                        "if addbacks are present and confidence is 'high' or 'medium'. "
                        "Always state in the dimension summary whether you used reported or normalised EBITDA "
                        "and why.\n\n"
                        f"EXTRACTED DATA:\n{json.dumps(extracted, indent=2)}"
                    )
                }]
            )
            scored_text = clean_json(scoring_response.content[0].text)
            scored      = json.loads(scored_text)

            # ── Server recalculates total_score — never trust Claude's value ──
            WEIGHTS = {
                'occupancy_demand':        0.15,
                'profitability_cashflow':  0.15,
                'revenue_pricing':         0.08,
                'staffing_resilience':     0.08,
                'lease_economics':         0.08,
                'valuation_structure':     0.08,
                'market_position':         0.07,
                'management_systems':      0.04,  # was 0.06; reduced to fund ccs_risk increase
                'regulatory_quality':      0.05,
                'upside_levers':           0.03,  # was 0.05; reduced to fund ccs_risk increase
                'ccs_risk':                0.07,  # was 0.03; increased — CCS risk is systemic
                'lease_tail':              0.03,
                'capex_liability':         0.02,
                'staff_qualification_mix': 0.02,
                'fee_benchmarking':        0.02,
                'operator_quality':        0.02,
                'enrolment_trend':         0.01,
                # Total: 1.00 ✓
            }
            dims = scored.get('dimensions', {})
            weighted_sum  = 0.0
            weight_used   = 0.0
            for dim_id, weight in WEIGHTS.items():
                dim = dims.get(dim_id, {})
                raw = dim.get('score')
                if isinstance(raw, (int, float)) and 0 <= raw <= 10:
                    weighted_sum += raw * weight
                    weight_used  += weight
            if weight_used > 0:
                # weighted_sum is in 0-10 range; scale to 0-100
                scored['total_score'] = round((weighted_sum / weight_used) * 10, 1)
            scored['scoring_version']  = '2.2'
            scored['scoring_timestamp'] = datetime.now(timezone.utc).isoformat()

            # ── Step 4: Clean up ALL uploaded paths ───────────────────────
            yield sse_event("progress", {
                "step": 4, "total": 5,
                "label": "Generating report",
                "detail": "Mapping competitors · Building analysis"
            })

            try:
                supabase.storage.from_('uploads').remove(storage_paths)
            except Exception:
                pass

            # ── Step 5: Complete ──────────────────────────────────────────
            yield sse_event("progress", {
                "step": 5, "total": 5,
                "label": "Complete",
                "detail": "Analysis ready"
            })

            yield sse_event("complete", {
                "success":   True,
                "extracted": extracted,
                "scored":    scored,
                "meta": {
                    "source_files":  source_files,
                    "file_classes":  file_classes,
                    "skipped_files": skipped,
                }
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
            "X-Accel-Buffering": "no",
        }
    )
