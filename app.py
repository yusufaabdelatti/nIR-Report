import streamlit as st
from groq import Groq
import base64, json, re, zipfile, io, csv
from datetime import datetime
from io import BytesIO, StringIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.colors import HexColor
from reportlab.lib import colors

st.set_page_config(page_title="nIR HEG Tracker", page_icon="🧠", layout="wide")

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
  .main { background-color: #F4F8FE; }
  .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
  .hero-banner {
    background: linear-gradient(135deg, #4A6FA5 0%, #3A8A8F 100%);
    border-radius: 16px; padding: 24px 32px; color: white; margin-bottom: 20px;
  }
  .hero-banner h1 { margin:0; font-size:1.7rem; font-weight:700; }
  .hero-banner p  { margin:5px 0 0; opacity:0.85; font-size:0.9rem; }
  .card { background:white; border-radius:12px; padding:18px 22px;
          box-shadow:0 2px 12px rgba(74,111,165,0.08); margin-bottom:14px; }
  .card h3 { color:#4A6FA5; font-size:0.95rem; font-weight:600; margin:0 0 10px; }
  .tag-good  { background:#E8F8F0; color:#1E8449; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }
  .tag-warn  { background:#FEF9E7; color:#B7950B; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }
  .tag-alert { background:#FDEDEC; color:#C0392B; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }
  div[data-testid="stButton"] > button { border-radius:8px; font-weight:600; }
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
for k, v in [("authenticated", False), ("patients", {}), ("active_patient", None)]:
    if k not in st.session_state:
        st.session_state[k] = v

def get_groq():
    return Groq(api_key=st.secrets["GROQ_API_KEY"])

# ── CSV parser ─────────────────────────────────────────────────────────────────
def parse_csv(content: str) -> dict:
    meta, stats_rows, in_stats, headers = {}, [], False, []
    for line in content.splitlines():
        line = line.strip()
        if not line: continue
        if line.startswith("[Statistics HEG-Ratio]"):
            in_stats = True; continue
        if line.startswith("[") and in_stats:
            in_stats = False; continue
        if not in_stats:
            if "=" in line and not line.startswith("["):
                parts = line.split(";", 2)
                if len(parts) >= 2:
                    meta[parts[0].replace("=","").strip()] = parts[1].strip()
        else:
            parts = [p.strip() for p in line.split(";")]
            if not headers: headers = parts
            elif len(parts) >= len(headers):
                stats_rows.append(dict(zip(headers, parts)))

    def flt(v):
        try: return round(float(v), 2)
        except: return None
    def it(v):
        try: return int(float(v))
        except: return None

    parsed_rows = []
    for r in stats_rows:
        parsed_rows.append({
            "state":           r.get("State","").strip().lower(),
            "percent_correct": it(r.get("percentCorrect")),
            "percent_false":   it(r.get("percentFalse")),
            "min":             flt(r.get("min")),
            "max":             flt(r.get("max")),
            "mean":            flt(r.get("mean")),
            "range":           flt(r.get("range")),
            "points":          it(r.get("points")),
            "difficulty":      r.get("Difficulty 1:super easy - 5:super hard","").strip(),
            "threshold_max":   flt(r.get("ThresholdMax")),
            "threshold_min":   flt(r.get("ThresholdMin")),
        })

    total = next((r for r in parsed_rows if r["state"] == "total"), parsed_rows[-1] if parsed_rows else {})
    dur = meta.get("TotalDuration","").strip()

    return {
        "patient_name": meta.get("Client","Unknown").strip(),
        "date":         meta.get("MeasurementDate","").replace(".","/"),
        "time":         meta.get("MeasurementTime","").rsplit(":",1)[0],
        "duration":     dur,
        "rows":         parsed_rows,
        "total":        total,
    }

def parse_zip(zip_bytes: bytes) -> list:
    sessions = []
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        csv_files = sorted([f for f in zf.namelist() if f.lower().endswith(".csv")])
        for fname in csv_files:
            try:
                raw = zf.read(fname).decode("utf-8", errors="replace")
                parsed = parse_csv(raw)
                parsed["filename"] = fname
                sessions.append(parsed)
            except Exception as e:
                st.warning(f"Could not parse {fname}: {e}")
    return sessions

# ── Groq report ────────────────────────────────────────────────────────────────
def generate_report(patient_name: str, sessions: list) -> dict:
    """Returns dict with keys: overview, metrics, activation, recommendations, summary"""
    client = get_groq()
    lines = []
    for i, s in enumerate(sessions, 1):
        t = s.get("total", {})
        lines.append(
            f"Session {i} | Date: {s.get('date','?')} | Duration: {s.get('duration','?')} | "
            f"Mean: {t.get('mean','?')} | Max: {t.get('max','?')} | Min: {t.get('min','?')} | "
            f"Range: {t.get('range','?')} | %Correct: {t.get('percent_correct','?')}% | "
            f"Difficulty: {t.get('difficulty','?')} | Points: {t.get('points','?')} | "
            f"Threshold Min: {t.get('threshold_min','?')} | Threshold Max: {t.get('threshold_max','?')}"
        )

    # Page 1 section — detailed overview for parents (plain language)
    prompt_p1 = f"""You are writing a neurofeedback session summary for a PARENT. Use plain, warm, non-technical language. Be encouraging but honest.

Patient: {patient_name} | Sessions: {len(sessions)}
Data:
{chr(10).join(lines)}

Write EXACTLY this section. 3-4 sentences. No bullets. No markdown. No technical jargon.

SESSION OVERVIEW FOR PARENT
Describe: how many sessions completed, the date range, how long sessions typically lasted, and a simple warm description of the overall direction of progress (e.g. improving, consistent, building well). Make the parent feel informed and involved."""

    r1 = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt_p1}],
        max_tokens=300, temperature=0.4,
    )
    overview_text = r1.choices[0].message.content.strip()
    # Clean section header if model included it
    overview_text = re.sub(r'^SESSION OVERVIEW FOR PARENT\s*\n?', '', overview_text, flags=re.IGNORECASE).strip()

    # Page 2 sections — clinical interpretation for staff
    prompt_p2 = f"""You are a senior clinical neuropsychologist writing a HEG neurofeedback progress report for clinic staff (therapists and doctors). Be precise, cite numbers, use clinical language.

Patient: {patient_name} | Sessions: {len(sessions)}
Data:
{chr(10).join(lines)}

Write EXACTLY these 4 sections. Each section: 2-3 sentences. Cite specific numbers. No bullets. No markdown.

METRICS ANALYSIS
Analyse trends in Mean HEG (first vs last), %Correct, Points, Threshold Min/Max evolution across sessions.

CORTICAL ACTIVATION
Interpret PFC activation quality, self-regulation consistency, and what the range and threshold trends reveal neurologically.

PROGRESS & RECOMMENDATIONS
Clear verdict on progress (improving/plateau/inconsistent) with supporting data. Give 2 specific clinical recommendations for the next block.

PHYSICIAN SUMMARY
2 sentences maximum. Suitable for attaching to a medical file. Include overall verdict and one key clinical next step."""

    r2 = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt_p2}],
        max_tokens=600, temperature=0.3,
    )
    clinical_text = r2.choices[0].message.content.strip()

    return {
        "overview":  overview_text,
        "clinical":  clinical_text,
        "raw_combined": overview_text + "\n\n" + clinical_text
    }

# ── PDF builder — 2 pages ─────────────────────────────────────────────────────
def build_pdf(patient_name: str, sessions: list, report: dict) -> BytesIO:
    STEEL  = HexColor("#4A6FA5")
    ICE    = HexColor("#EAF1FB")
    ICE2   = HexColor("#F4F8FE")
    TEAL   = HexColor("#3A8A8F")
    TEAL_L = HexColor("#E3F4F5")
    SILVER = HexColor("#E0E6EF")
    WARM   = HexColor("#F8FAFD")
    NAVY   = HexColor("#1A2B4A")
    TEXT   = HexColor("#1E2A3A")
    TEXT_M = HexColor("#5A6880")
    TEXT_L = HexColor("#8A96A8")
    STEEL_L= HexColor("#6B8FC2")
    GREEN_L= HexColor("#E8F8F0")
    WHITE  = colors.white

    def S(n, **k): return ParagraphStyle(n, **k)

    TITLE   = S("T",  fontName="Helvetica-Bold",   fontSize=13, textColor=WHITE,  leading=16, alignment=TA_CENTER)
    TSUB    = S("TS", fontName="Helvetica",         fontSize=8,  textColor=HexColor("#A8C8E8"), leading=10, alignment=TA_CENTER)
    META_B  = S("MB", fontName="Helvetica-Bold",    fontSize=8,  textColor=STEEL,  leading=10)
    META    = S("M",  fontName="Helvetica",         fontSize=8,  textColor=TEXT_M, leading=10)
    SH      = S("SH", fontName="Helvetica-Bold",    fontSize=9,  textColor=STEEL,  leading=11, spaceBefore=6, spaceAfter=3)
    SH_TEAL = S("ST", fontName="Helvetica-Bold",    fontSize=9,  textColor=TEAL,   leading=11, spaceBefore=6, spaceAfter=3)
    TH      = S("TH", fontName="Helvetica-Bold",    fontSize=6.5,textColor=STEEL,  leading=8,  alignment=TA_CENTER)
    TD      = S("TD", fontName="Helvetica",         fontSize=6.5,textColor=TEXT,   leading=8,  alignment=TA_CENTER)
    BODY    = S("B",  fontName="Helvetica",         fontSize=9,  textColor=TEXT,   leading=13, spaceAfter=6, alignment=TA_JUSTIFY)
    BODY_SM = S("BS", fontName="Helvetica",         fontSize=8.5,textColor=TEXT,   leading=12, spaceAfter=4, alignment=TA_JUSTIFY)
    SEC_LBL = S("SL", fontName="Helvetica-Bold",    fontSize=8,  textColor=STEEL,  leading=10)
    SEC_TEAL= S("STL",fontName="Helvetica-Bold",    fontSize=8,  textColor=TEAL,   leading=10)
    FOOT    = S("F",  fontName="Helvetica-Oblique", fontSize=6.5,textColor=TEXT_L, alignment=TA_CENTER)
    BADGE   = S("BG", fontName="Helvetica-Bold",    fontSize=7,  textColor=WHITE,  leading=9,  alignment=TA_CENTER)

    W_page, H_page = A4
    LM = RM = 1.5*cm
    TM = BM = 1.3*cm
    W = W_page - LM - RM

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=LM, rightMargin=RM,
                            topMargin=TM,  bottomMargin=BM)
    story = []

    def banner(subtitle, badge_text, badge_color):
        """Reusable page banner with a colored badge indicating audience"""
        badge_col = 2.2*cm
        title_col = W - badge_col
        return Table([[
            Paragraph("nIR HEG NEUROFEEDBACK — Progress Report", TITLE),
            Paragraph(badge_text, BADGE),
        ]], colWidths=[title_col, badge_col],
        rowHeights=[1.1*cm])

    def page_banner(badge_text, badge_color):
        badge_col = 2.2*cm
        title_col = W - badge_col
        tbl = Table([[
            [Paragraph("nIR HEG NEUROFEEDBACK — Progress Report", TITLE),
             Paragraph("Dr. Hany Elhennawy Psychiatric Center", TSUB)],
            Paragraph(badge_text, BADGE),
        ]], colWidths=[title_col, badge_col])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(0,-1), NAVY),
            ("BACKGROUND",    (1,0),(1,-1), badge_color),
            ("TOPPADDING",    (0,0),(-1,-1), 6),
            ("BOTTOMPADDING", (0,0),(-1,-1), 6),
            ("LEFTPADDING",   (0,0),(0,-1), 12),
            ("LEFTPADDING",   (1,0),(1,-1), 4),
            ("RIGHTPADDING",  (0,0),(-1,-1), 6),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("ALIGN",         (1,0),(1,-1), "CENTER"),
        ]))
        return tbl

    def meta_strip(extra_label=None, extra_val=None):
        items = [
            Paragraph("Patient:", META_B), Paragraph(patient_name, META),
            Paragraph("Sessions:", META_B), Paragraph(str(len(sessions)), META),
            Paragraph("Date:", META_B), Paragraph(datetime.now().strftime("%d.%m.%Y"), META),
        ]
        if extra_label:
            items += [Paragraph(extra_label, META_B), Paragraph(extra_val, META)]
            mc = [1.5*cm, 3.8*cm, 1.7*cm, 1.5*cm, 1.8*cm, 2.5*cm, 2.0*cm, W-14.8*cm]
        else:
            mc = [1.5*cm, 4.2*cm, 1.7*cm, 1.5*cm, 1.8*cm, W-10.7*cm]
        tbl = Table([items], colWidths=mc, rowHeights=[0.55*cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), ICE),
            ("BOX",           (0,0),(-1,-1), 0.5, SILVER),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 7),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ]))
        return tbl

    def footer_line(label):
        story.append(Spacer(1, 5))
        story.append(HRFlowable(width="100%", thickness=0.4, color=SILVER, spaceAfter=3))
        story.append(Paragraph(
            f"nIR HEG Sessions · Dr. Hany Elhennawy Psychiatric Center · "
            f"Generated {datetime.now().strftime('%d.%m.%Y %H:%M')} · {label}",
            FOOT))

    # ══════════════════════════════════════════════════════
    # PAGE 1 — FOR PARENTS: full data table + session overview
    # ══════════════════════════════════════════════════════

    story.append(page_banner("FOR PARENTS", HexColor("#3A8A8F")))
    story.append(Spacer(1, 5))
    story.append(meta_strip())
    story.append(Spacer(1, 8))

    # ── Full session data table ──
    story.append(Paragraph("Session Data — Complete Record", SH))
    story.append(HRFlowable(width="100%", thickness=0.8, color=STEEL, spaceAfter=4))

    hdr_labels = ["#", "Date", "Duration", "Mean\nHEG", "Max\nHEG", "Min\nHEG",
                  "Range", "% Correct", "Thresh.\nMin", "Thresh.\nMax", "Difficulty", "Points"]
    sw_cm = [0.6, 1.9, 1.8, 1.3, 1.3, 1.3, 1.3, 1.5, 1.5, 1.5, 1.7, 1.4]
    sw = [x*cm for x in sw_cm]
    sw[-1] = W - sum(sw[:-1])

    hdr_row = [Paragraph(t, TH) for t in hdr_labels]
    s_rows = [hdr_row]
    fills = [ICE, WARM]
    for i, s in enumerate(sessions):
        t = s.get("total", {})
        pct = t.get("percent_correct")
        s_rows.append([Paragraph(str(v) if v is not None else "—", TD) for v in [
            i+1,
            s.get("date","—"),
            s.get("duration","—").strip(),
            t.get("mean","—"), t.get("max","—"), t.get("min","—"), t.get("range","—"),
            f"{pct}%" if pct is not None else "—",
            t.get("threshold_min","—"), t.get("threshold_max","—"),
            t.get("difficulty","—"), t.get("points","—"),
        ]])

    # Row heights: aim for ~13cm total table height
    n_sessions = max(len(sessions), 1)
    HDR_H  = 0.85*cm
    DATA_H = max(0.65*cm, min(1.1*cm, (13.0*cm - HDR_H) / n_sessions))

    s_tbl = Table(s_rows, colWidths=sw, repeatRows=1,
                  rowHeights=[HDR_H] + [DATA_H]*len(sessions))
    alt = [("BACKGROUND", (0,r),(-1,r), fills[r%2]) for r in range(1, len(s_rows))]
    s_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), NAVY),
        ("TEXTCOLOR",     (0,0),(-1,0), WHITE),
        *alt,
        ("BOX",           (0,0),(-1,-1), 0.7, STEEL_L),
        ("INNERGRID",     (0,0),(-1,-1), 0.3, SILVER),
        ("TOPPADDING",    (0,0),(-1,-1), 3),
        ("BOTTOMPADDING", (0,0),(-1,-1), 3),
        ("LEFTPADDING",   (0,0),(-1,-1), 4),
        ("RIGHTPADDING",  (0,0),(-1,-1), 3),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(s_tbl)
    story.append(Spacer(1, 10))

    # ── Session overview for parents ──
    story.append(Paragraph("Session Overview", SH_TEAL))
    story.append(HRFlowable(width="100%", thickness=0.8, color=TEAL, spaceAfter=6))

    overview_box = Table([[Paragraph(report["overview"], BODY)]],
                         colWidths=[W])
    overview_box.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), TEAL_L),
        ("BOX",           (0,0),(-1,-1), 0.8, TEAL),
        ("TOPPADDING",    (0,0),(-1,-1), 12),
        ("BOTTOMPADDING", (0,0),(-1,-1), 12),
        ("LEFTPADDING",   (0,0),(-1,-1), 14),
        ("RIGHTPADDING",  (0,0),(-1,-1), 14),
    ]))
    story.append(overview_box)
    story.append(Spacer(1, 10))

    # ── Quick legend for parents ──
    legend_items = [
        ("% Correct", ">75% = Excellent   |   60–75% = Good   |   45–60% = Moderate   |   <45% = Needs review"),
        ("Points",    "Combined score of success rate and difficulty. Rising over sessions = real progress."),
        ("Mean HEG",  "Average brain activation level per session. Higher and rising = PFC engagement improving."),
        ("Threshold", "The target level set for the session. Rising Min/Max = brain being challenged more over time."),
    ]
    leg_rows = []
    for label, desc in legend_items:
        leg_rows.append(Table([[
            Paragraph(label, S("ll", fontName="Helvetica-Bold", fontSize=8, textColor=STEEL, leading=10)),
            Paragraph(desc,  S("ld", fontName="Helvetica",      fontSize=8, textColor=TEXT_M, leading=10)),
        ]], colWidths=[2.4*cm, W-2.4*cm]))
    
    legend_data = [[
        Paragraph(label, S("ll", fontName="Helvetica-Bold", fontSize=7.5, textColor=STEEL, leading=10)),
        Paragraph(desc,  S("ld", fontName="Helvetica",      fontSize=7.5, textColor=TEXT_M, leading=10)),
    ] for label, desc in legend_items]
    
    leg_tbl = Table(legend_data, colWidths=[2.5*cm, W-2.5*cm])
    leg_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), ICE2),
        ("BOX",           (0,0),(-1,-1), 0.5, SILVER),
        ("INNERGRID",     (0,0),(-1,-1), 0.3, SILVER),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("RIGHTPADDING",  (0,0),(-1,-1), 6),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
    ]))
    story.append(Paragraph("Quick Reference Guide", S("qr", fontName="Helvetica-Bold", fontSize=8, textColor=TEXT_M, leading=10, spaceAfter=4)))
    story.append(leg_tbl)

    footer_line("Patient / Parent Copy")

    # ══════════════════════════════════════════════════════
    # PAGE 2 — FOR CLINIC STAFF: clinical interpretation
    # ══════════════════════════════════════════════════════
    story.append(PageBreak())

    story.append(page_banner("CLINICAL STAFF", HexColor("#4A6FA5")))
    story.append(Spacer(1, 5))
    story.append(meta_strip())
    story.append(Spacer(1, 10))

    story.append(Paragraph("Clinical Interpretation", SH))
    story.append(HRFlowable(width="100%", thickness=0.8, color=STEEL, spaceAfter=8))

    # Parse the clinical sections from Groq output
    clinical_sections = [
        ("METRICS ANALYSIS",           ICE2,   STEEL,   False),
        ("CORTICAL ACTIVATION",        WARM,   STEEL,   False),
        ("PROGRESS & RECOMMENDATIONS", ICE2,   STEEL,   False),
        ("PHYSICIAN SUMMARY",          TEAL_L, TEAL,    True),
    ]

    remaining = report["clinical"]
    for title, fill, border_color, is_summary in clinical_sections:
        if title not in remaining:
            body_text = ""
        else:
            parts = remaining.split(title, 1)
            remaining = parts[1] if len(parts) > 1 else ""
            next_start = len(remaining)
            for other, _, _, _ in clinical_sections:
                if other != title and other in remaining:
                    idx = remaining.index(other)
                    if idx < next_start:
                        next_start = idx
            body_text = remaining[:next_start].strip()
            remaining = remaining[next_start:]

        display_title = "🩺 Physician Summary" if is_summary else title.title()
        label_style   = SEC_TEAL if is_summary else SEC_LBL
        lbl_fill      = HexColor("#D4EEF0") if is_summary else HexColor("#DDEEF9")

        sec = Table([
            [Paragraph(display_title, label_style)],
            [Paragraph(body_text, BODY_SM)],
        ], colWidths=[W])
        sec.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), fill),
            ("BACKGROUND",    (0,0),(0,0),   lbl_fill),
            ("BOX",           (0,0),(-1,-1), 0.6, border_color),
            ("LINEBELOW",     (0,0),(0,0),   0.4, SILVER),
            ("TOPPADDING",    (0,0),(-1,-1), 7),
            ("BOTTOMPADDING", (0,0),(-1,-1), 7),
            ("LEFTPADDING",   (0,0),(-1,-1), 10),
            ("RIGHTPADDING",  (0,0),(-1,-1), 10),
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ]))
        story.append(sec)
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 10))

    # ── Trend summary table on page 2 ──
    story.append(Paragraph("Metrics Trend Summary", SH))
    story.append(HRFlowable(width="100%", thickness=0.8, color=STEEL, spaceAfter=6))

    def get_trend(key):
        vals = [s.get("total",{}).get(key) for s in sessions if s.get("total",{}).get(key) is not None]
        if len(vals) < 2: return "—", "—", "—"
        first, last = vals[0], vals[-1]
        arrow = "↑ Improving" if last > first else ("↓ Declining" if last < first else "= Stable")
        return str(first), str(last), arrow

    trend_metrics = [
        ("Mean HEG",       "mean"),
        ("Max HEG",        "max"),
        ("% Correct",      "percent_correct"),
        ("Points",         "points"),
        ("Threshold Min",  "threshold_min"),
        ("Threshold Max",  "threshold_max"),
    ]

    trend_hdr = [Paragraph(t, TH) for t in ["Metric", "First Session", "Last Session", "Trend"]]
    trend_rows = [trend_hdr]
    for label, key in trend_metrics:
        first, last, arrow = get_trend(key)
        color = HexColor("#1E8449") if "↑" in arrow else (HexColor("#C0392B") if "↓" in arrow else TEXT_M)
        trend_rows.append([
            Paragraph(label, S("tl", fontName="Helvetica-Bold", fontSize=8, textColor=NAVY, leading=10)),
            Paragraph(first, S("tv", fontName="Helvetica", fontSize=8, textColor=TEXT_M, leading=10, alignment=TA_CENTER)),
            Paragraph(last,  S("tv2",fontName="Helvetica", fontSize=8, textColor=TEXT_M, leading=10, alignment=TA_CENTER)),
            Paragraph(arrow, S("ta", fontName="Helvetica-Bold", fontSize=8, textColor=color, leading=10, alignment=TA_CENTER)),
        ])

    tw = [3.5*cm, 3.0*cm, 3.0*cm, W-9.5*cm]
    t_tbl = Table(trend_rows, colWidths=tw)
    alt2 = [("BACKGROUND", (0,r),(-1,r), ICE if r%2==0 else WARM) for r in range(1, len(trend_rows))]
    t_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), NAVY),
        ("TEXTCOLOR",     (0,0),(-1,0), WHITE),
        *alt2,
        ("BOX",           (0,0),(-1,-1), 0.7, STEEL_L),
        ("INNERGRID",     (0,0),(-1,-1), 0.3, SILVER),
        ("TOPPADDING",    (0,0),(-1,-1), 6),
        ("BOTTOMPADDING", (0,0),(-1,-1), 6),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("RIGHTPADDING",  (0,0),(-1,-1), 6),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("ALIGN",         (1,0),(-1,-1), "CENTER"),
    ]))
    story.append(t_tbl)

    footer_line("Clinical Staff Copy — Confidential")

    doc.build(story)
    buf.seek(0)
    return buf

# ══════════════════════════════════════════════════════════════════════════════
# UI — identical to original
# ══════════════════════════════════════════════════════════════════════════════

if not st.session_state.authenticated:
    st.markdown("""
    <div class="hero-banner">
        <h1>🧠 nIR HEG Session Tracker</h1>
        <p>Dr. Hany Elhennawy Psychiatric Center — Clinical Neurofeedback Suite</p>
    </div>""", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        st.markdown('<div class="card"><h3>🔒 Access Required</h3>', unsafe_allow_html=True)
        code = st.text_input("Enter access code", type="password", placeholder="Access code")
        if st.button("Enter", use_container_width=True):
            valid = [c.strip() for c in st.secrets.get("ACCESS_CODE","").split(",")]
            if code in valid:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Invalid access code.")
        st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

st.markdown("""
<div class="hero-banner">
    <h1>🧠 nIR HEG Session Tracker</h1>
    <p>Dr. Hany Elhennawy Psychiatric Center — Upload ZIP · Parse sessions · Generate clinical reports</p>
</div>""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### 👤 Patients")
    with st.expander("➕ Add New Patient", expanded=not st.session_state.patients):
        new_name = st.text_input("Patient name", placeholder="Full name")
        if st.button("Add Patient", use_container_width=True):
            name = new_name.strip()
            if name:
                if name not in st.session_state.patients:
                    st.session_state.patients[name] = []
                st.session_state.active_patient = name
                st.rerun()
            else:
                st.warning("Please enter a name.")
    st.divider()
    for pname in list(st.session_state.patients.keys()):
        n = len(st.session_state.patients[pname])
        is_active = st.session_state.active_patient == pname
        if st.button(
            f"{'▶ ' if is_active else ''}{pname}  ({n} session{'s' if n!=1 else ''})",
            key=f"p_{pname}", use_container_width=True,
            type="primary" if is_active else "secondary"
        ):
            st.session_state.active_patient = pname
            st.rerun()
    st.divider()
    if st.button("🔒 Log Out", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()

if not st.session_state.active_patient:
    st.info("👈 Select or add a patient from the sidebar to get started.")
    st.stop()

patient  = st.session_state.active_patient
sessions = st.session_state.patients[patient]

st.markdown(f"## {patient}")
st.caption(f"{len(sessions)} session{'s' if len(sessions)!=1 else ''} recorded")

tab1, tab2 = st.tabs(["📥 Upload ZIP", "📊 Sessions & Report"])

with tab1:
    st.markdown('<div class="card"><h3>📦 Upload Session ZIP File</h3>', unsafe_allow_html=True)
    st.caption("Export the results ZIP from the Body & Mind app (via email) and upload here. All CSV sessions will be parsed automatically.")
    uploaded = st.file_uploader("Choose ZIP file", type=["zip"], label_visibility="collapsed")
    if uploaded:
        st.info(f"**{uploaded.name}** · {uploaded.size/1024:.1f} KB")
        if st.button("📂 Parse & Import All Sessions", type="primary"):
            with st.spinner("Reading ZIP and parsing all sessions..."):
                try:
                    parsed = parse_zip(uploaded.read())
                    if not parsed:
                        st.error("No valid CSV files found in the ZIP.")
                    else:
                        st.session_state.patients[patient] = parsed
                        st.success(f"✅ {len(parsed)} session{'s' if len(parsed)!=1 else ''} imported!")
                        for i, s in enumerate(parsed, 1):
                            t = s.get("total", {})
                            pct = t.get("percent_correct", 0) or 0
                            tag = "good" if pct >= 60 else ("warn" if pct >= 40 else "alert")
                            st.markdown(
                                f"**#{i}** · {s.get('date','?')} · {s.get('duration','?').strip()} · "
                                f"Mean: **{t.get('mean','?')}** · "
                                f'<span class="tag-{tag}">{pct}% correct</span> · '
                                f"Points: **{t.get('points','?')}**",
                                unsafe_allow_html=True)
                        st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
    st.markdown('</div>', unsafe_allow_html=True)

with tab2:
    if not sessions:
        st.info("No sessions yet. Upload a ZIP in the Upload tab.")
    else:
        st.markdown("### Session Log")
        cols_h = st.columns([0.4,1.2,1.4,0.9,0.9,0.9,0.9,1.0,1.1,1.1,1.3,0.8])
        for col, h in zip(cols_h, ["#","Date","Duration","Mean","Max","Min","Range","%Correct","Thresh Min","Thresh Max","Difficulty","Points"]):
            col.markdown(f"**{h}**")
        st.divider()

        for i, s in enumerate(sessions):
            t = s.get("total", {})
            pct = t.get("percent_correct", 0) or 0
            tag = "good" if pct >= 60 else ("warn" if pct >= 40 else "alert")
            cols = st.columns([0.4,1.2,1.4,0.9,0.9,0.9,0.9,1.0,1.1,1.1,1.3,0.8])
            cols[0].write(f"**#{i+1}**")
            cols[1].write(s.get("date","—"))
            cols[2].write(s.get("duration","—").strip())
            cols[3].write(str(t.get("mean","—")))
            cols[4].write(str(t.get("max","—")))
            cols[5].write(str(t.get("min","—")))
            cols[6].write(str(t.get("range","—")))
            cols[7].markdown(f'<span class="tag-{tag}">{pct}%</span>', unsafe_allow_html=True)
            cols[8].write(str(t.get("threshold_min","—")))
            cols[9].write(str(t.get("threshold_max","—")))
            cols[10].write(str(t.get("difficulty","—")))
            cols[11].write(str(t.get("points","—")))

        st.divider()

        if len(sessions) >= 2:
            st.markdown("### Trend Overview")
            tc = st.columns(6)
            def trend(key):
                vals = [s.get("total",{}).get(key) for s in sessions if s.get("total",{}).get(key) is not None]
                if len(vals) < 2: return "—", "—"
                return ("↑" if vals[-1] > vals[0] else ("↓" if vals[-1] < vals[0] else "=")), str(vals[-1])
            for col, (label, key) in zip(tc, [
                ("Mean","mean"), ("%Correct","percent_correct"),
                ("Points","points"), ("Thresh Min","threshold_min"),
                ("Thresh Max","threshold_max"), ("Difficulty","difficulty")
            ]):
                arr, last = trend(key)
                col.metric(label, arr, f"Last: {last}")

        st.divider()
        st.markdown("### Generate Clinical Report")
        st.caption("2-page PDF — Page 1 (parent copy): full data table + plain-language overview · Page 2 (staff copy): clinical interpretation + trend summary")

        if st.button("📋 Generate Report", type="primary"):
            with st.spinner("Generating report with Groq..."):
                try:
                    report  = generate_report(patient, sessions)
                    pdf_buf = build_pdf(patient, sessions, report)
                    st.success("✅ Report ready — 2 pages generated!")

                    with st.expander("Preview report content"):
                        st.markdown("**Page 1 — Session Overview (Parent Copy):**")
                        st.text(report["overview"])
                        st.markdown("**Page 2 — Clinical Interpretation (Staff Copy):**")
                        st.text(report["clinical"])

                    fname = f"HEG_Report_{patient.replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                    st.download_button("⬇️ Download 2-Page PDF Report", data=pdf_buf,
                                       file_name=fname, mime="application/pdf")
                except Exception as e:
                    st.error(f"Error generating report: {e}")
