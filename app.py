import streamlit as st
from groq import Groq
import base64, json, re, zipfile, io, csv
from datetime import datetime
from io import BytesIO, StringIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepInFrame
from reportlab.lib.colors import HexColor
from reportlab.lib import colors

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="nIR HEG Tracker", page_icon="🧠", layout="wide")

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
  .main { background-color: #F4F8FE; }
  .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
  .hero-banner {
    background: linear-gradient(135deg, #4A6FA5 0%, #3A8A8F 100%);
    border-radius: 16px; padding: 28px 36px; color: white; margin-bottom: 24px;
  }
  .hero-banner h1 { margin:0; font-size:1.8rem; font-weight:700; }
  .hero-banner p  { margin:6px 0 0; opacity:0.85; font-size:0.95rem; }
  .card { background:white; border-radius:12px; padding:20px 24px;
          box-shadow:0 2px 12px rgba(74,111,165,0.08); margin-bottom:16px; }
  .card h3 { color:#4A6FA5; font-size:1rem; font-weight:600; margin:0 0 12px; }
  .tag-good  { background:#E8F8F0; color:#1E8449; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }
  .tag-warn  { background:#FEF9E7; color:#B7950B; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }
  .tag-alert { background:#FDEDEC; color:#C0392B; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }
  div[data-testid="stButton"] > button { border-radius:8px; font-weight:600; }
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
for key, val in [("authenticated", False), ("patients", {}), ("active_patient", None)]:
    if key not in st.session_state:
        st.session_state[key] = val

# ── Groq client ────────────────────────────────────────────────────────────────
def get_groq():
    return Groq(api_key=st.secrets["GROQ_API_KEY"])

# ── CSV parser ─────────────────────────────────────────────────────────────────
def parse_csv(content: str) -> dict:
    """Parse a single Body & Mind HEG CSV file into a structured dict."""
    meta, stats_rows = {}, []
    in_stats = False
    headers = []

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[Statistics HEG-Ratio]"):
            in_stats = True
            continue
        if line.startswith("[") and in_stats:
            in_stats = False
            continue
        if not in_stats:
            if "=" in line and not line.startswith("["):
                parts = line.split(";", 2)
                if len(parts) >= 2:
                    key = parts[0].replace("=","").strip()
                    val = parts[1].strip() if len(parts) > 1 else ""
                    meta[key] = val
        else:
            parts = [p.strip() for p in line.split(";")]
            if not headers:
                headers = parts
            else:
                if len(parts) >= len(headers):
                    row = dict(zip(headers, parts))
                    stats_rows.append(row)

    def flt(v):
        try: return float(v)
        except: return None

    def it(v):
        try: return int(float(v))
        except: return None

    sessions_parsed = []
    for r in stats_rows:
        state_raw = r.get("State","").strip().lower()
        sessions_parsed.append({
            "state":           state_raw,
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

    total_row = next((r for r in sessions_parsed if r["state"] == "total"), sessions_parsed[-1] if sessions_parsed else {})

    return {
        "patient_name": meta.get("Client","Unknown").strip(),
        "date":         meta.get("MeasurementDate","").replace(".","/"),
        "time":         meta.get("MeasurementTime","").rsplit(":",1)[0],
        "duration":     meta.get("TotalDuration","").strip(),
        "rows":         sessions_parsed,
        "total":        total_row,
    }

def parse_zip(zip_bytes: bytes) -> list:
    """Extract and parse all CSV files from a ZIP, sorted by date/time."""
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

# ── Groq report generator ──────────────────────────────────────────────────────
def generate_report(patient_name: str, sessions: list) -> str:
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

    prompt = f"""You are a senior clinical neuropsychologist writing a concise HEG neurofeedback progress report.

Patient: {patient_name}
Total Sessions: {len(sessions)}

Session Data:
{chr(10).join(lines)}

Write a structured clinical report with EXACTLY these 5 sections. Each section must be ONE concise paragraph (3-4 sentences max). Be specific — cite actual numbers.

SESSION OVERVIEW
[One paragraph: training period summary, sessions count, general pattern]

METRICS ANALYSIS
[One paragraph: trends in Mean HEG, %Correct, Points, Difficulty across sessions. Cite first vs last session numbers.]

CORTICAL ACTIVATION
[One paragraph: what the data reveals about PFC activation, self-regulation, and consistency]

PROGRESS & RECOMMENDATIONS
[One paragraph: clear progress verdict + 2 specific actionable recommendations for next sessions]

PHYSICIAN SUMMARY
[2 sentences maximum. Plain language. Suitable for medical file attachment.]

Rules: prose paragraphs only, no bullet points, no markdown, cite real numbers, be concise."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=900,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()

# ── PDF builder — 1 page ───────────────────────────────────────────────────────
def build_pdf(patient_name: str, sessions: list, report_text: str) -> BytesIO:
    STEEL    = HexColor("#4A6FA5")
    ICE      = HexColor("#EAF1FB")
    ICE2     = HexColor("#F4F8FE")
    TEAL     = HexColor("#3A8A8F")
    TEAL_L   = HexColor("#E3F4F5")
    SILVER   = HexColor("#E0E6EF")
    WARM     = HexColor("#F8FAFD")
    NAVY     = HexColor("#1A2B4A")
    TEXT     = HexColor("#1E2A3A")
    TEXT_M   = HexColor("#5A6880")
    TEXT_L   = HexColor("#8A96A8")
    WHITE    = colors.white

    def S(n, **k): return ParagraphStyle(n, **k)
    TITLE  = S("T",  fontName="Helvetica-Bold",   fontSize=14, textColor=WHITE,  leading=18, alignment=TA_CENTER)
    TSUB   = S("TS", fontName="Helvetica",         fontSize=8,  textColor=HexColor("#A8C8E8"), leading=10, alignment=TA_CENTER)
    META   = S("M",  fontName="Helvetica",         fontSize=8,  textColor=TEXT_M, leading=10)
    META_B = S("MB", fontName="Helvetica-Bold",    fontSize=8,  textColor=STEEL,  leading=10)
    SH     = S("SH", fontName="Helvetica-Bold",    fontSize=8.5,textColor=STEEL,  leading=11, spaceBefore=5, spaceAfter=2)
    TH     = S("TH", fontName="Helvetica-Bold",    fontSize=7,  textColor=STEEL,  leading=8,  alignment=TA_CENTER)
    TD     = S("TD", fontName="Helvetica",         fontSize=7,  textColor=TEXT,   leading=8,  alignment=TA_CENTER)
    BODY   = S("B",  fontName="Helvetica",         fontSize=8,  textColor=TEXT,   leading=11, spaceAfter=3, alignment=TA_JUSTIFY)
    BODY_B = S("BB", fontName="Helvetica-Bold",    fontSize=8,  textColor=STEEL,  leading=11, spaceAfter=1)
    FOOT   = S("F",  fontName="Helvetica-Oblique", fontSize=6.5,textColor=TEXT_L, alignment=TA_CENTER)

    buf = BytesIO()
    W = A4[0] - 3.2*cm
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.6*cm, rightMargin=1.6*cm,
                            topMargin=1.2*cm,  bottomMargin=1.2*cm)
    story = []

    # ── Banner ──
    banner = Table([[Paragraph("nIR HEG NEUROFEEDBACK", TITLE)],
                    [Paragraph("Clinical Progress Report — Dr. Hany Elhennawy Psychiatric Center", TSUB)]],
                   colWidths=[W])
    banner.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), NAVY),
        ("TOPPADDING",    (0,0),(-1,-1), 8),
        ("BOTTOMPADDING", (0,0),(-1,-1), 8),
        ("LEFTPADDING",   (0,0),(-1,-1), 12),
    ]))
    story.append(banner)
    story.append(Spacer(1, 5))

    # ── Meta strip ──
    meta_cols = [1.8*cm, 5*cm, 2*cm, 2*cm, 2.2*cm, W-13*cm]
    meta_tbl = Table([[
        Paragraph("Patient:", META_B), Paragraph(patient_name, META),
        Paragraph("Sessions:", META_B), Paragraph(str(len(sessions)), META),
        Paragraph("Date:", META_B), Paragraph(datetime.now().strftime("%d.%m.%Y"), META),
    ]], colWidths=meta_cols)
    meta_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), ICE),
        ("BOX",           (0,0),(-1,-1), 0.5, SILVER),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 5))

    # ── Session data table ──
    story.append(Paragraph("Session Data", SH))
    story.append(HRFlowable(width="100%", thickness=0.8, color=STEEL, spaceAfter=3))

    hdr = [Paragraph(t, TH) for t in
           ["#","Date","Duration","%Correct","Mean","Max","Thresh.Min","Thresh.Max","Difficulty","Points"]]
    sw = [0.6*cm,2.0*cm,1.9*cm,1.5*cm,1.4*cm,1.4*cm,1.7*cm,1.7*cm,1.8*cm,1.5*cm]
    # pad to full width
    sw[-1] = W - sum(sw[:-1])

    s_rows = [hdr]
    fills = [ICE, WARM]
    for i, s in enumerate(sessions):
        t = s.get("total", {})
        pct = t.get("percent_correct")
        s_rows.append([Paragraph(str(v) if v is not None else "—", TD) for v in [
            i+1,
            s.get("date","—"), s.get("duration","—"),
            f"{pct}%" if pct is not None else "—",
            t.get("mean","—"), t.get("max","—"),
            t.get("threshold_min","—"), t.get("threshold_max","—"),
            t.get("difficulty","—"), t.get("points","—"),
        ]])

    s_tbl = Table(s_rows, colWidths=sw, repeatRows=1)
    alt = [("BACKGROUND", (0,r),(-1,r), fills[r%2]) for r in range(1, len(s_rows))]
    s_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), NAVY),
        ("TEXTCOLOR",     (0,0),(-1,0), WHITE),
        *alt,
        ("BOX",           (0,0),(-1,-1), 0.6, SILVER),
        ("INNERGRID",     (0,0),(-1,-1), 0.3, SILVER),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
        ("LEFTPADDING",   (0,0),(-1,-1), 3),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(s_tbl)
    story.append(Spacer(1, 6))

    # ── Clinical report sections ──
    story.append(Paragraph("Clinical Interpretation", SH))
    story.append(HRFlowable(width="100%", thickness=0.8, color=STEEL, spaceAfter=4))

    sections = [
        ("SESSION OVERVIEW",          ICE2),
        ("METRICS ANALYSIS",          WARM),
        ("CORTICAL ACTIVATION",       ICE2),
        ("PROGRESS & RECOMMENDATIONS",WARM),
        ("PHYSICIAN SUMMARY",         TEAL_L),
    ]

    remaining = report_text
    for title, fill in sections:
        if title not in remaining:
            continue
        parts = remaining.split(title, 1)
        remaining = parts[1] if len(parts) > 1 else ""
        # find next section start
        next_start = len(remaining)
        for other_title, _ in sections:
            if other_title != title and other_title in remaining:
                idx = remaining.index(other_title)
                if idx < next_start:
                    next_start = idx
        body = remaining[:next_start].strip()
        remaining = remaining[next_start:]

        is_summary = "PHYSICIAN" in title
        label = "🩺 Physician Summary" if is_summary else title.title()

        sec_tbl = Table([
            [Paragraph(label, S("sl", fontName="Helvetica-Bold", fontSize=7.5,
                                textColor=TEAL if is_summary else STEEL, leading=10))],
            [Paragraph(body, BODY)],
        ], colWidths=[W])
        sec_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), fill),
            ("BACKGROUND",    (0,0),(-1,0),  HexColor("#DDEEF9") if not is_summary else TEAL_L),
            ("BOX",           (0,0),(-1,-1), 0.5, SILVER),
            ("LINEBELOW",     (0,0),(-1,0),  0.5, SILVER),
            ("TOPPADDING",    (0,0),(-1,-1), 5),
            ("BOTTOMPADDING", (0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("RIGHTPADDING",  (0,0),(-1,-1), 8),
        ]))
        story.append(sec_tbl)
        story.append(Spacer(1, 3))

    # ── Footer ──
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.4, color=SILVER, spaceAfter=3))
    story.append(Paragraph(
        f"nIR HEG Sessions · Dr. Hany Elhennawy Psychiatric Center · "
        f"Generated {datetime.now().strftime('%d.%m.%Y %H:%M')} · For clinical use only",
        FOOT))

    doc.build(story)
    buf.seek(0)
    return buf

# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════

# ── Access gate ────────────────────────────────────────────────────────────────
if not st.session_state.authenticated:
    st.markdown("""
    <div class="hero-banner">
        <h1>🧠 nIR HEG Session Tracker</h1>
        <p>Dr. Hany Elhennawy Psychiatric Center — Clinical Neurofeedback Suite</p>
    </div>""", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,1.2,1])
    with col2:
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

# ── Main banner ────────────────────────────────────────────────────────────────
st.markdown("""
<div class="hero-banner">
    <h1>🧠 nIR HEG Session Tracker</h1>
    <p>Dr. Hany Elhennawy Psychiatric Center — Upload ZIP · Parse sessions · Generate clinical reports</p>
</div>""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
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
        label = f"{'▶ ' if is_active else ''}{pname}  ({n} session{'s' if n!=1 else ''})"
        if st.button(label, key=f"p_{pname}", use_container_width=True,
                     type="primary" if is_active else "secondary"):
            st.session_state.active_patient = pname
            st.rerun()
    st.divider()
    if st.button("🔒 Log Out", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()

# ── Guard ──────────────────────────────────────────────────────────────────────
if not st.session_state.active_patient:
    st.info("👈 Select or add a patient from the sidebar to get started.")
    st.stop()

patient  = st.session_state.active_patient
sessions = st.session_state.patients[patient]

st.markdown(f"## {patient}")
st.caption(f"{len(sessions)} session{'s' if len(sessions)!=1 else ''} recorded")

tab1, tab2 = st.tabs(["📥 Upload ZIP", "📊 Sessions & Report"])

# ── Tab 1: Upload ──────────────────────────────────────────────────────────────
with tab1:
    st.markdown('<div class="card"><h3>📦 Upload Session ZIP File</h3>', unsafe_allow_html=True)
    st.caption("Export the results ZIP from the Body & Mind app (via email) and upload it here. All CSV sessions inside will be parsed automatically.")

    uploaded = st.file_uploader("Choose ZIP file", type=["zip"], label_visibility="collapsed")

    if uploaded:
        st.info(f"File received: **{uploaded.name}** ({uploaded.size / 1024:.1f} KB)")
        if st.button("📂 Parse & Import All Sessions", type="primary", use_container_width=False):
            with st.spinner("Reading ZIP and parsing sessions..."):
                try:
                    parsed = parse_zip(uploaded.read())
                    if not parsed:
                        st.error("No valid CSV files found in the ZIP.")
                    else:
                        st.session_state.patients[patient] = parsed
                        st.success(f"✅ {len(parsed)} session{'s' if len(parsed)!=1 else ''} imported successfully!")

                        # Quick preview
                        st.markdown("**Imported sessions:**")
                        for i, s in enumerate(parsed, 1):
                            t = s.get("total", {})
                            pct = t.get("percent_correct")
                            tag = "good" if (pct or 0) >= 60 else ("warn" if (pct or 0) >= 40 else "alert")
                            st.markdown(
                                f"Session {i} · {s.get('date','?')} · {s.get('duration','?')} · "
                                f"Mean: **{t.get('mean','?')}** · "
                                f'<span class="tag-{tag}">{pct}% correct</span> · '
                                f"Points: **{t.get('points','?')}**",
                                unsafe_allow_html=True
                            )
                        st.rerun()
                except Exception as e:
                    st.error(f"Error parsing ZIP: {e}")
    st.markdown('</div>', unsafe_allow_html=True)

# ── Tab 2: Sessions & Report ───────────────────────────────────────────────────
with tab2:
    if not sessions:
        st.info("No sessions yet. Upload a ZIP in the Upload tab.")
    else:
        # Session log table
        st.markdown("### Session Log")
        cols_h = st.columns([0.4,1.3,1.5,1,1,1,1.2,1.2,1.5,0.6])
        for col, h in zip(cols_h,
            ["#","Date","Duration","%Correct","Mean","Max","Thresh.Min","Thresh.Max","Difficulty","Pts"]):
            col.markdown(f"**{h}**")
        st.divider()

        for i, s in enumerate(sessions):
            t = s.get("total", {})
            pct = t.get("percent_correct", 0) or 0
            tag = "good" if pct >= 60 else ("warn" if pct >= 40 else "alert")
            cols = st.columns([0.4,1.3,1.5,1,1,1,1.2,1.2,1.5,0.6])
            cols[0].write(f"**#{i+1}**")
            cols[1].write(s.get("date","—"))
            cols[2].write(s.get("duration","—").strip())
            cols[3].markdown(f'<span class="tag-{tag}">{pct}%</span>', unsafe_allow_html=True)
            cols[4].write(str(t.get("mean","—")))
            cols[5].write(str(t.get("max","—")))
            cols[6].write(str(t.get("threshold_min","—")))
            cols[7].write(str(t.get("threshold_max","—")))
            cols[8].write(str(t.get("difficulty","—")))
            cols[9].write(str(t.get("points","—")))

        st.divider()

        # Trend strip
        if len(sessions) >= 2:
            st.markdown("### Trend Overview")
            tc = st.columns(5)
            def trend(key):
                vals = [s.get("total",{}).get(key) for s in sessions if s.get("total",{}).get(key) is not None]
                if len(vals) < 2: return "—", "—"
                arrow = "↑" if vals[-1] > vals[0] else ("↓" if vals[-1] < vals[0] else "=")
                return arrow, str(vals[-1])
            for col, (label, key) in zip(tc, [
                ("Mean HEG","mean"),("%Correct","percent_correct"),
                ("Points","points"),("Thresh. Max","threshold_max"),("Difficulty","difficulty")
            ]):
                arr, last = trend(key)
                col.metric(label, arr, f"Last: {last}")

        st.divider()

        # Report generation
        st.markdown("### Generate Clinical Report")
        st.caption(f"One-page PDF report summarising all {len(sessions)} sessions for {patient}.")

        if st.button("📋 Generate Report", type="primary"):
            with st.spinner("Generating clinical report with Groq..."):
                try:
                    report_text = generate_report(patient, sessions)
                    pdf_buf     = build_pdf(patient, sessions, report_text)

                    st.success("✅ Report ready!")

                    with st.expander("Preview report text"):
                        st.text(report_text)

                    fname = f"HEG_Report_{patient.replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                    st.download_button(
                        "⬇️ Download PDF Report",
                        data=pdf_buf,
                        file_name=fname,
                        mime="application/pdf"
                    )
                except Exception as e:
                    st.error(f"Error generating report: {e}")
