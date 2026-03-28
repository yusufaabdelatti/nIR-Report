import streamlit as st
import anthropic
import base64
import json
import re
from datetime import datetime
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.colors import HexColor
from reportlab.lib import colors

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="nIR HEG Tracker",
    page_icon="🧠",
    layout="wide"
)

# ── Styling ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
  .main { background-color: #F4F8FE; }
  .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

  .hero-banner {
    background: linear-gradient(135deg, #4A6FA5 0%, #3A8A8F 100%);
    border-radius: 16px;
    padding: 28px 36px;
    color: white;
    margin-bottom: 24px;
  }
  .hero-banner h1 { margin:0; font-size:1.8rem; font-weight:700; letter-spacing:-0.5px; }
  .hero-banner p  { margin:6px 0 0; opacity:0.85; font-size:0.95rem; }

  .card {
    background: white;
    border-radius: 12px;
    padding: 20px 24px;
    box-shadow: 0 2px 12px rgba(74,111,165,0.08);
    margin-bottom: 16px;
  }
  .card h3 { color:#4A6FA5; font-size:1rem; font-weight:600; margin:0 0 12px; }

  .metric-chip {
    display:inline-block;
    background:#EAF1FB;
    color:#4A6FA5;
    border-radius:20px;
    padding:3px 12px;
    font-size:0.78rem;
    font-weight:600;
    margin:2px;
  }
  .session-card {
    background:#F8FAFD;
    border:1px solid #E0E6EF;
    border-radius:10px;
    padding:14px 18px;
    margin-bottom:10px;
  }
  .session-card .snum {
    color:#4A6FA5; font-weight:700; font-size:0.9rem;
  }
  .session-card .sdate {
    color:#8A96A8; font-size:0.78rem; margin-left:8px;
  }
  .tag-good  { background:#E8F8F0; color:#1E8449; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }
  .tag-warn  { background:#FEF9E7; color:#B7950B; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }
  .tag-alert { background:#FDEDEC; color:#C0392B; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }

  div[data-testid="stButton"] > button {
    border-radius: 8px;
    font-weight: 600;
    transition: all 0.2s;
  }
  div[data-testid="stFileUploader"] {
    border-radius: 10px;
  }
  .stTextInput > div > div > input {
    border-radius: 8px;
  }
</style>
""", unsafe_allow_html=True)

# ── Session state init ─────────────────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "patients" not in st.session_state:
    st.session_state.patients = {}   # { name: [ {session data}, ... ] }
if "active_patient" not in st.session_state:
    st.session_state.active_patient = None

# ── Helpers ────────────────────────────────────────────────────────────────────
def get_client():
    return anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

def image_to_b64(uploaded_file):
    return base64.standard_b64encode(uploaded_file.read()).decode("utf-8")

def extract_session_data(image_b64, media_type):
    """Use Claude Vision to extract HEG metrics from screenshot."""
    client = get_client()
    prompt = """You are analyzing a screenshot from the Body & Mind HEG neurofeedback app.
Extract ALL visible data from the results table and any progress graph.

Return ONLY a valid JSON object with this exact structure (no markdown, no explanation):
{
  "date": "DD.MM.YYYY or null",
  "duration": "e.g. 00h 06m 16s or null",
  "rows": [
    {
      "start": "00:00:00",
      "stop": "00:06:32",
      "state": "concentration or relaxation or total or total_concentration or total_relaxation",
      "percent_correct": 61,
      "percent_false": 39,
      "min": 105.2,
      "max": 109.0,
      "mean": 107.8,
      "range": 3.9,
      "difficulty": "Hard or Easy or Medium or null",
      "points": 239
    }
  ],
  "graph_visible": true or false,
  "graph_description": "brief description of graph shape if visible, else null"
}

Rules:
- Extract ALL rows from the table
- For state: /\\ = concentration, \\/ = relaxation, total /\\ = total_concentration, total \\/ = total_relaxation, total = total
- If a value is missing or unclear use null
- Numbers should be numeric (not strings)
- Return ONLY the JSON, nothing else"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": prompt}
            ]
        }]
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw)

def get_total_row(session):
    """Extract the 'total' row from session data."""
    for row in session.get("rows", []):
        if row.get("state") == "total":
            return row
    if session.get("rows"):
        return session["rows"][-1]
    return {}

def generate_report(patient_name, sessions):
    """Generate clinical interpretation report using Groq via Anthropic."""
    client = get_client()

    session_summaries = []
    for i, s in enumerate(sessions, 1):
        total = get_total_row(s)
        session_summaries.append(
            f"Session {i} ({s.get('date','N/A')}, {s.get('duration','N/A')}): "
            f"Mean={total.get('mean','N/A')}, Max={total.get('max','N/A')}, "
            f"Min={total.get('min','N/A')}, Range={total.get('range','N/A')}, "
            f"%Correct={total.get('percent_correct','N/A')}%, "
            f"Difficulty={total.get('difficulty','N/A')}, Points={total.get('points','N/A')}"
            + (f", Graph: {s.get('graph_description')}" if s.get('graph_description') else "")
        )

    data_text = "\n".join(session_summaries)
    n = len(sessions)

    prompt = f"""You are a senior clinical neuropsychologist writing a professional HEG neurofeedback progress report for Dr. Hany Elhennawy Psychiatric Center.

Patient: {patient_name}
Total Sessions: {n}

Session Data:
{data_text}

Write a structured clinical report with these exact sections:

1. SESSION OVERVIEW
Brief summary of the training period, number of sessions, and general pattern.

2. KEY METRICS ANALYSIS
Analyze the trend in: Mean HEG, % Correct, Points, and Difficulty Level across sessions. Be specific with numbers.

3. CORTICAL ACTIVATION PATTERN
Interpret what the data reveals about the patient's prefrontal cortex activation, self-regulation capacity, and consistency.

4. PROGRESS ASSESSMENT
Clear statement: Is the patient progressing, plateauing, or showing inconsistency? Support with data.

5. CLINICAL RECOMMENDATIONS
2-3 specific, actionable recommendations for the next training block (threshold adjustment, session length, protocol changes).

6. SUMMARY FOR PHYSICIAN
2-3 sentences maximum. Plain language. Suitable for attaching to a patient medical file.

Rules:
- Be specific and cite actual numbers from the data
- Professional clinical tone throughout
- Do NOT use bullet points, use prose paragraphs
- Each section should be 3-5 sentences
- Do not add any preamble or sign-off"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()

# ── PDF Report Generator ───────────────────────────────────────────────────────
def build_report_pdf(patient_name, sessions, report_text):
    STEEL      = HexColor("#4A6FA5")
    ICE        = HexColor("#EAF1FB")
    TEAL       = HexColor("#3A8A8F")
    TEAL_L     = HexColor("#E3F4F5")
    SILVER     = HexColor("#E0E6EF")
    TEXT       = HexColor("#1E2A3A")
    TEXT_MED   = HexColor("#5A6880")
    TEXT_LIGHT = HexColor("#8A96A8")
    WHITE      = colors.white

    def S(name, **kw): return ParagraphStyle(name, **kw)

    TITLE  = S("t",  fontName="Helvetica-Bold",    fontSize=16, textColor=WHITE,     leading=20, alignment=TA_CENTER)
    SUB    = S("s",  fontName="Helvetica",          fontSize=9,  textColor=HexColor("#A8C8E8"), leading=12, alignment=TA_CENTER)
    H2     = S("h2", fontName="Helvetica-Bold",     fontSize=10, textColor=STEEL,    leading=13, spaceBefore=10, spaceAfter=4)
    BODY   = S("b",  fontName="Helvetica",          fontSize=9,  textColor=TEXT,     leading=13, spaceAfter=6, alignment=TA_JUSTIFY)
    SMALL  = S("sm", fontName="Helvetica",          fontSize=8,  textColor=TEXT_MED, leading=10)
    SMALL_B= S("sb", fontName="Helvetica-Bold",     fontSize=8,  textColor=STEEL,    leading=10)
    FOOTER = S("ft", fontName="Helvetica-Oblique",  fontSize=7,  textColor=TEXT_LIGHT,alignment=TA_CENTER)
    TH     = S("th", fontName="Helvetica-Bold",     fontSize=8,  textColor=STEEL,    leading=9, alignment=TA_CENTER)
    TD     = S("td", fontName="Helvetica",          fontSize=7.5,textColor=TEXT,     leading=9, alignment=TA_CENTER)

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.8*cm, rightMargin=1.8*cm,
                            topMargin=1.5*cm,  bottomMargin=1.5*cm)
    story = []

    # Header banner
    banner = Table([[
        Paragraph("nIR HEG NEUROFEEDBACK", TITLE),
        Paragraph("Clinical Progress Report", SUB),
    ]], colWidths=[A4[0] - 3.6*cm])
    banner.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), HexColor("#1A2B4A")),
        ("TOPPADDING",    (0,0),(-1,-1), 10),
        ("BOTTOMPADDING", (0,0),(-1,-1), 10),
        ("LEFTPADDING",   (0,0),(-1,-1), 14),
    ]))
    story.append(banner)
    story.append(Spacer(1, 8))

    # Meta info
    meta_data = [[
        Paragraph("Patient:", SMALL_B), Paragraph(patient_name, SMALL),
        Paragraph("Sessions:", SMALL_B), Paragraph(str(len(sessions)), SMALL),
        Paragraph("Report Date:", SMALL_B), Paragraph(datetime.now().strftime("%d.%m.%Y"), SMALL),
        Paragraph("Clinic:", SMALL_B), Paragraph("Dr. Hany Elhennawy Psychiatric Center", SMALL),
    ]]
    meta_tbl = Table(meta_data, colWidths=[2.2*cm,3.8*cm,1.8*cm,1.5*cm,2.5*cm,2.5*cm,1.5*cm,4.0*cm])
    meta_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), ICE),
        ("BOX",           (0,0),(-1,-1), 0.6, SILVER),
        ("TOPPADDING",    (0,0),(-1,-1), 6),
        ("BOTTOMPADDING", (0,0),(-1,-1), 6),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 10))

    # Session data table
    story.append(Paragraph("Session Data Summary", H2))
    story.append(HRFlowable(width="100%", thickness=1, color=STEEL, spaceAfter=5))

    s_header = [Paragraph(t, TH) for t in
                ["#","Date","Duration","Mean","Max","Min","Range","%Correct","Difficulty","Points"]]
    s_rows = [s_header]
    fills = [ICE, HexColor("#F8FAFD")]
    for i, s in enumerate(sessions):
        t = get_total_row(s)
        s_rows.append([Paragraph(str(v), TD) for v in [
            i+1, s.get("date","—"), s.get("duration","—"),
            t.get("mean","—"), t.get("max","—"), t.get("min","—"),
            t.get("range","—"), f"{t.get('percent_correct','—')}%",
            t.get("difficulty","—"), t.get("points","—")
        ]])

    sw = [0.7*cm,2.2*cm,2.2*cm,1.6*cm,1.6*cm,1.5*cm,1.6*cm,1.9*cm,2.0*cm,1.9*cm]
    s_tbl = Table(s_rows, colWidths=sw, repeatRows=1)
    s_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), HexColor("#1A2B4A")),
        ("TEXTCOLOR",     (0,0),(-1,0), WHITE),
        *[("BACKGROUND",  (0,r),(-1,r), fills[r%2]) for r in range(1, len(s_rows))],
        ("BOX",           (0,0),(-1,-1), 0.8, SILVER),
        ("INNERGRID",     (0,0),(-1,-1), 0.3, SILVER),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 4),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(s_tbl)
    story.append(Spacer(1, 12))

    # Clinical report sections
    story.append(Paragraph("Clinical Interpretation", H2))
    story.append(HRFlowable(width="100%", thickness=1, color=STEEL, spaceAfter=6))

    section_titles = [
        "1. SESSION OVERVIEW", "2. KEY METRICS ANALYSIS",
        "3. CORTICAL ACTIVATION PATTERN", "4. PROGRESS ASSESSMENT",
        "5. CLINICAL RECOMMENDATIONS", "6. SUMMARY FOR PHYSICIAN"
    ]

    current_text = report_text
    for title in section_titles:
        if title in current_text:
            parts = current_text.split(title, 1)
            current_text = parts[1] if len(parts) > 1 else ""
            next_section = None
            for other in section_titles:
                if other != title and other in current_text:
                    next_section = other
                    break
            if next_section:
                section_body, current_text = current_text.split(next_section, 1)
                current_text = next_section + current_text
            else:
                section_body = current_text
                current_text = ""

            # Special styling for summary
            if "SUMMARY" in title:
                sum_tbl = Table([[Paragraph(section_body.strip(), BODY)]],
                                colWidths=[A4[0] - 3.6*cm])
                sum_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0,0),(-1,-1), TEAL_L),
                    ("BOX",        (0,0),(-1,-1), 0.8, TEAL),
                    ("TOPPADDING", (0,0),(-1,-1), 8),
                    ("BOTTOMPADDING",(0,0),(-1,-1), 8),
                    ("LEFTPADDING",(0,0),(-1,-1), 10),
                    ("RIGHTPADDING",(0,0),(-1,-1), 10),
                ]))
                story.append(Paragraph(title, H2))
                story.append(sum_tbl)
            else:
                story.append(Paragraph(title, H2))
                story.append(Paragraph(section_body.strip(), BODY))

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.5, color=SILVER, spaceAfter=4))
    story.append(Paragraph(
        f"nIR HEG Sessions · Dr. Hany Elhennawy Psychiatric Center · Generated {datetime.now().strftime('%d.%m.%Y %H:%M')} · For clinical use only",
        FOOTER))

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
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1,1.2,1])
    with col2:
        st.markdown('<div class="card"><h3>🔒 Access Required</h3>', unsafe_allow_html=True)
        code = st.text_input("Enter access code", type="password", placeholder="Access code")
        if st.button("Enter", use_container_width=True):
            valid_codes = [c.strip() for c in st.secrets.get("ACCESS_CODE", "").split(",")]
            if code in valid_codes:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Invalid access code.")
        st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# ── Main app ───────────────────────────────────────────────────────────────────
st.markdown("""
<div class="hero-banner">
    <h1>🧠 nIR HEG Session Tracker</h1>
    <p>Dr. Hany Elhennawy Psychiatric Center — Upload session screenshots · Extract data · Generate clinical reports</p>
</div>
""", unsafe_allow_html=True)

# ── Sidebar — patient management ───────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 👤 Patients")

    # Add new patient
    with st.expander("➕ Add New Patient", expanded=not st.session_state.patients):
        new_name = st.text_input("Patient name", placeholder="Full name")
        if st.button("Add Patient", use_container_width=True):
            if new_name.strip():
                name = new_name.strip()
                if name not in st.session_state.patients:
                    st.session_state.patients[name] = []
                st.session_state.active_patient = name
                st.success(f"Patient '{name}' added.")
                st.rerun()
            else:
                st.warning("Please enter a name.")

    st.divider()

    # Patient list
    if st.session_state.patients:
        for pname in list(st.session_state.patients.keys()):
            n_sessions = len(st.session_state.patients[pname])
            is_active = st.session_state.active_patient == pname
            label = f"{'▶ ' if is_active else ''}{pname}  ({n_sessions} session{'s' if n_sessions!=1 else ''})"
            if st.button(label, key=f"pat_{pname}", use_container_width=True,
                         type="primary" if is_active else "secondary"):
                st.session_state.active_patient = pname
                st.rerun()
    else:
        st.caption("No patients yet. Add one above.")

    st.divider()
    if st.button("🔒 Log Out", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()

# ── Main content ───────────────────────────────────────────────────────────────
if not st.session_state.active_patient:
    st.info("👈 Select or add a patient from the sidebar to get started.")
    st.stop()

patient = st.session_state.active_patient
sessions = st.session_state.patients[patient]

st.markdown(f"## {patient}")
st.caption(f"{len(sessions)} session{'s' if len(sessions)!=1 else ''} recorded")

tab1, tab2 = st.tabs(["📥 Upload Session", "📊 Sessions & Report"])

# ── Tab 1: Upload ──────────────────────────────────────────────────────────────
with tab1:
    st.markdown('<div class="card"><h3>📸 Upload Session Screenshot</h3>', unsafe_allow_html=True)
    st.caption("Take a screenshot of the Results screen in the Body & Mind app and upload it here.")

    uploaded = st.file_uploader(
        "Choose screenshot",
        type=["png", "jpg", "jpeg"],
        label_visibility="collapsed"
    )

    if uploaded:
        col_img, col_info = st.columns([1, 1])
        with col_img:
            st.image(uploaded, caption="Uploaded screenshot", use_container_width=True)

        with col_info:
            st.markdown("**Ready to extract data from this screenshot.**")
            st.caption("Claude Vision will read the results table and any visible graph.")

            if st.button("🔍 Extract & Save Session", type="primary", use_container_width=True):
                with st.spinner("Reading screenshot..."):
                    try:
                        uploaded.seek(0)
                        img_b64 = image_to_b64(uploaded)
                        media_type = f"image/{uploaded.type.split('/')[-1]}"
                        if media_type == "image/jpg":
                            media_type = "image/jpeg"

                        data = extract_session_data(img_b64, media_type)
                        data["uploaded_at"] = datetime.now().isoformat()
                        data["session_number"] = len(sessions) + 1

                        st.session_state.patients[patient].append(data)
                        st.success(f"✅ Session {data['session_number']} saved!")

                        # Show extracted data
                        st.markdown("**Extracted data:**")
                        total = get_total_row(data)
                        cols = st.columns(4)
                        metrics = [
                            ("Mean HEG", total.get("mean","—")),
                            ("Max HEG",  total.get("max","—")),
                            ("%Correct",  f"{total.get('percent_correct','—')}%"),
                            ("Points",    total.get("points","—")),
                        ]
                        for col, (label, val) in zip(cols, metrics):
                            col.metric(label, val)

                        if data.get("graph_description"):
                            st.info(f"📈 Graph detected: {data['graph_description']}")

                        st.rerun()

                    except json.JSONDecodeError as e:
                        st.error(f"Could not parse extracted data. Please try again. ({e})")
                    except Exception as e:
                        st.error(f"Error: {e}")

    st.markdown('</div>', unsafe_allow_html=True)

# ── Tab 2: Sessions & Report ───────────────────────────────────────────────────
with tab2:
    if not sessions:
        st.info("No sessions uploaded yet. Go to the Upload tab to add the first session.")
    else:
        # Sessions overview
        st.markdown("### Session Log")

        cols_header = st.columns([0.5, 1.5, 1.5, 1, 1, 1, 1, 1, 1.5, 0.8])
        headers = ["#", "Date", "Duration", "Mean", "Max", "%Correct", "Difficulty", "Points", "Graph", ""]
        for col, h in zip(cols_header, headers):
            col.markdown(f"**{h}**")
        st.divider()

        for i, s in enumerate(sessions):
            total = get_total_row(s)
            pct = total.get("percent_correct", 0) or 0
            tag = "good" if pct >= 60 else ("warn" if pct >= 40 else "alert")

            cols = st.columns([0.5, 1.5, 1.5, 1, 1, 1, 1, 1, 1.5, 0.8])
            cols[0].write(f"**#{i+1}**")
            cols[1].write(s.get("date", "—"))
            cols[2].write(s.get("duration", "—"))
            cols[3].write(str(total.get("mean", "—")))
            cols[4].write(str(total.get("max", "—")))
            cols[5].markdown(f'<span class="tag-{tag}">{pct}%</span>', unsafe_allow_html=True)
            cols[6].write(str(total.get("difficulty", "—")))
            cols[7].write(str(total.get("points", "—")))
            cols[8].write(s.get("graph_description", "—") or "—")
            if cols[9].button("🗑", key=f"del_{patient}_{i}", help="Delete session"):
                st.session_state.patients[patient].pop(i)
                st.rerun()

        st.divider()

        # Trend quick view
        if len(sessions) >= 2:
            st.markdown("### Quick Trend")
            trend_cols = st.columns(4)
            means   = [get_total_row(s).get("mean") for s in sessions if get_total_row(s).get("mean")]
            maxes   = [get_total_row(s).get("max")  for s in sessions if get_total_row(s).get("max")]
            pcts    = [get_total_row(s).get("percent_correct") for s in sessions if get_total_row(s).get("percent_correct")]
            pts     = [get_total_row(s).get("points") for s in sessions if get_total_row(s).get("points")]

            def trend_arrow(vals):
                if len(vals) < 2: return "—"
                return "↑" if vals[-1] > vals[0] else ("↓" if vals[-1] < vals[0] else "=")

            trend_cols[0].metric("Mean HEG Trend",    trend_arrow(means),   f"{means[-1] if means else '—'} last session")
            trend_cols[1].metric("Max HEG Trend",     trend_arrow(maxes),   f"{maxes[-1] if maxes else '—'} last session")
            trend_cols[2].metric("% Correct Trend",   trend_arrow(pcts),    f"{pcts[-1] if pcts else '—'}% last session")
            trend_cols[3].metric("Points Trend",      trend_arrow(pts),     f"{pts[-1] if pts else '—'} last session")

        st.divider()

        # Report generation
        st.markdown("### Generate Clinical Report")
        st.caption(f"Generate a full clinical interpretation report for {patient} based on all {len(sessions)} sessions.")

        if st.button("📋 Generate Report", type="primary", use_container_width=False):
            with st.spinner("Generating clinical report..."):
                try:
                    report_text = generate_report(patient, sessions)
                    pdf_buf = build_report_pdf(patient, sessions, report_text)

                    st.success("✅ Report generated successfully!")

                    # Preview
                    with st.expander("📄 Preview Report Text", expanded=True):
                        st.text(report_text)

                    # Download
                    filename = f"HEG_Report_{patient.replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                    st.download_button(
                        label="⬇️ Download PDF Report",
                        data=pdf_buf,
                        file_name=filename,
                        mime="application/pdf",
                        use_container_width=False
                    )
                except Exception as e:
                    st.error(f"Error generating report: {e}")
