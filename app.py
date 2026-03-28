import streamlit as st
from groq import Groq
import base64, json, re, zipfile, io, csv
from datetime import datetime
from io import BytesIO, StringIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
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

    prompt = f"""You are a senior clinical neuropsychologist writing a concise HEG neurofeedback progress report for a physician.

Patient: {patient_name}
Total Sessions: {len(sessions)}

Session Data:
{chr(10).join(lines)}

Write a clinical report with EXACTLY these 5 sections. Each section is ONE tight paragraph (3-4 sentences). Cite specific numbers. No bullet points. No markdown formatting.

SESSION OVERVIEW
Summarise the training block: number of sessions, date range, average duration, general trajectory.

METRICS ANALYSIS
Analyse trends in Mean HEG, %Correct, Points, and Difficulty. Compare first vs last session numbers. Note Threshold Min and Max evolution — did the challenge level rise across sessions?

CORTICAL ACTIVATION
Interpret what the combined data reveals about prefrontal cortex activation quality, self-regulation capacity, and session-to-session consistency.

PROGRESS & RECOMMENDATIONS
State clearly whether the patient is progressing, plateauing, or inconsistent — with data to support it. Give 2 specific recommendations for the next training block (threshold, session length, or protocol adjustments).

PHYSICIAN SUMMARY
Two sentences only. Plain language. Suitable for medical file. Include overall progress verdict and one key recommendation."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()

# ── PDF builder — 1 page, half table / half interpretation ────────────────────
def build_pdf(patient_name: str, sessions: list, report_text: str) -> BytesIO:
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
    WHITE  = colors.white

    def S(n, **k): return ParagraphStyle(n, **k)

    TITLE  = S("T",  fontName="Helvetica-Bold",   fontSize=13, textColor=WHITE,  leading=17, alignment=TA_CENTER)
    TSUB   = S("TS", fontName="Helvetica",         fontSize=8,  textColor=HexColor("#A8C8E8"), leading=10, alignment=TA_CENTER)
    META_B = S("MB", fontName="Helvetica-Bold",    fontSize=8,  textColor=STEEL,  leading=10)
    META   = S("M",  fontName="Helvetica",         fontSize=8,  textColor=TEXT_M, leading=10)
    SH     = S("SH", fontName="Helvetica-Bold",    fontSize=8.5,textColor=STEEL,  leading=11, spaceBefore=4, spaceAfter=2)
    TH     = S("TH", fontName="Helvetica-Bold",    fontSize=6.5,textColor=STEEL,  leading=8,  alignment=TA_CENTER)
    TD     = S("TD", fontName="Helvetica",         fontSize=6.5,textColor=TEXT,   leading=8,  alignment=TA_CENTER)
    BODY   = S("B",  fontName="Helvetica",         fontSize=8,  textColor=TEXT,   leading=11.5, spaceAfter=3, alignment=TA_JUSTIFY)
    BODY_B = S("BB", fontName="Helvetica-Bold",    fontSize=8,  textColor=STEEL,  leading=10, spaceAfter=1)
    FOOT   = S("F",  fontName="Helvetica-Oblique", fontSize=6.5,textColor=TEXT_L, alignment=TA_CENTER)

    buf = BytesIO()
    W_page, H_page = A4
    LM = RM = 1.5*cm
    TM = BM = 1.3*cm
    W = W_page - LM - RM   # content width

    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=LM, rightMargin=RM,
                            topMargin=TM,  bottomMargin=BM)
    story = []

    # ── Banner (compact) ──
    banner = Table([
        [Paragraph("nIR HEG NEUROFEEDBACK — Clinical Progress Report", TITLE)],
        [Paragraph("Dr. Hany Elhennawy Psychiatric Center", TSUB)],
    ], colWidths=[W])
    banner.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), NAVY),
        ("TOPPADDING",    (0,0),(-1,-1), 7),
        ("BOTTOMPADDING", (0,0),(-1,-1), 7),
        ("LEFTPADDING",   (0,0),(-1,-1), 12),
    ]))
    story.append(banner)
    story.append(Spacer(1, 4))

    # ── Meta strip ──
    mc = [1.6*cm, 4.5*cm, 1.8*cm, 1.8*cm, 2.2*cm, W - 11.9*cm]
    meta_tbl = Table([[
        Paragraph("Patient:", META_B), Paragraph(patient_name, META),
        Paragraph("Sessions:", META_B), Paragraph(str(len(sessions)), META),
        Paragraph("Report Date:", META_B), Paragraph(datetime.now().strftime("%d.%m.%Y"), META),
    ]], colWidths=mc)
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

    # ════════════════════════════════════
    # TOP HALF — Session data table
    # ════════════════════════════════════
    story.append(Paragraph("Session Data", SH))
    story.append(HRFlowable(width="100%", thickness=0.8, color=STEEL, spaceAfter=3))

    # Columns: # | Date | Dur | Mean | Max | Min | Range | %Corr | ThMin | ThMax | Diff | Pts
    hdr_labels = ["#","Date","Duration","Mean","Max","Min","Range","%Correct","Thresh\nMin","Thresh\nMax","Difficulty","Points"]
    n_cols = len(hdr_labels)
    # fixed widths (cm) — sum must = W
    sw_cm = [0.55, 1.8, 1.7, 1.2, 1.2, 1.2, 1.2, 1.4, 1.4, 1.4, 1.6, 1.35]
    sw = [x*cm for x in sw_cm]
    sw[-1] = W - sum(sw[:-1])   # adjust last col to fill exactly

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

    # Dynamic row height: aim for table to occupy ~half the page
    # Page usable H ≈ 29.7 - 2.6 = 27.1cm
    # Banner+meta+spacers ≈ 3.5cm, section head ≈ 0.6cm, divider line ≈ 0.3cm → overhead top ≈ 4.4cm
    # Footer ≈ 0.6cm, interp section head + divider ≈ 0.6cm, 5 interp blocks ≈ 9cm → bottom half ≈ 10.2cm
    # Table gets: 27.1 - 4.4 - 10.2 = 12.5cm target
    # Header row ~0.9cm, data rows: (12.5 - 0.9) / n_sessions
    n_sessions = len(sessions) if sessions else 1
    data_row_h = max(0.65*cm, min(1.1*cm, (12.5*cm - 0.9*cm) / n_sessions))

    s_tbl = Table(s_rows, colWidths=sw, repeatRows=1,
                  rowHeights=[None] + [data_row_h]*len(sessions))
    alt = [("BACKGROUND", (0,r),(-1,r), fills[r%2]) for r in range(1, len(s_rows))]
    s_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), NAVY),
        ("TEXTCOLOR",     (0,0),(-1,0), WHITE),
        *alt,
        ("BOX",           (0,0),(-1,-1), 0.7, STEEL_L),
        ("INNERGRID",     (0,0),(-1,-1), 0.25, SILVER),
        ("TOPPADDING",    (0,0),(-1,-1), 3),
        ("BOTTOMPADDING", (0,0),(-1,-1), 3),
        ("LEFTPADDING",   (0,0),(-1,-1), 3),
        ("RIGHTPADDING",  (0,0),(-1,-1), 2),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(s_tbl)
    story.append(Spacer(1, 6))

    # ════════════════════════════════════
    # BOTTOM HALF — Clinical interpretation
    # ════════════════════════════════════
    story.append(Paragraph("Clinical Interpretation", SH))
    story.append(HRFlowable(width="100%", thickness=0.8, color=STEEL, spaceAfter=4))

    sections = [
        ("SESSION OVERVIEW",           ICE2,   False),
        ("METRICS ANALYSIS",           WARM,   False),
        ("CORTICAL ACTIVATION",        ICE2,   False),
        ("PROGRESS & RECOMMENDATIONS", WARM,   False),
        ("PHYSICIAN SUMMARY",          TEAL_L, True),
    ]

    remaining = report_text
    for title, fill, is_summary in sections:
        if title not in remaining:
            continue
        parts = remaining.split(title, 1)
        remaining = parts[1] if len(parts) > 1 else ""
        next_start = len(remaining)
        for other, _, _ in sections:
            if other != title and other in remaining:
                idx = remaining.index(other)
                if idx < next_start:
                    next_start = idx
        body = remaining[:next_start].strip()
        remaining = remaining[next_start:]

        label = "🩺 Physician Summary" if is_summary else title.title()
        label_color = TEAL if is_summary else STEEL

        sec = Table([
            [Paragraph(label, S("sl", fontName="Helvetica-Bold", fontSize=7.5,
                                textColor=label_color, leading=10))],
            [Paragraph(body, BODY)],
        ], colWidths=[W])
        sec.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), fill),
            ("BACKGROUND",    (0,0),(-1,0),  HexColor("#DDEEF9") if not is_summary else TEAL_L),
            ("BOX",           (0,0),(-1,-1), 0.5, SILVER),
            ("LINEBELOW",     (0,0),(-1,0),  0.4, SILVER),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 7),
            ("RIGHTPADDING",  (0,0),(-1,-1), 7),
        ]))
        story.append(sec)
        story.append(Spacer(1, 2))

    # ── Footer ──
    story.append(Spacer(1, 3))
    story.append(HRFlowable(width="100%", thickness=0.4, color=SILVER, spaceAfter=2))
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
        st.caption(f"1-page PDF: session data table (top half) + clinical interpretation (bottom half).")

        if st.button("📋 Generate Report", type="primary"):
            with st.spinner("Generating report with Groq..."):
                try:
                    report_text = generate_report(patient, sessions)
                    pdf_buf     = build_pdf(patient, sessions, report_text)
                    st.success("✅ Report ready!")
                    with st.expander("Preview report text"):
                        st.text(report_text)
                    fname = f"HEG_Report_{patient.replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                    st.download_button("⬇️ Download PDF Report", data=pdf_buf,
                                       file_name=fname, mime="application/pdf")
                except Exception as e:
                    st.error(f"Error: {e}")
