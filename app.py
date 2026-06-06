import streamlit as st
from groq import Groq
import re, zipfile
from datetime import datetime
from io import BytesIO
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
  .hero-banner { background: linear-gradient(135deg, #4A6FA5 0%, #3A8A8F 100%);
    border-radius:16px; padding:24px 32px; color:white; margin-bottom:20px; }
  .hero-banner h1 { margin:0; font-size:1.7rem; font-weight:700; }
  .hero-banner p  { margin:5px 0 0; opacity:.85; font-size:.9rem; }
  .card { background:white; border-radius:12px; padding:18px 22px;
    box-shadow:0 2px 12px rgba(74,111,165,.08); margin-bottom:14px; }
  .card h3 { color:#4A6FA5; font-size:.95rem; font-weight:600; margin:0 0 10px; }
  .tag-good  { background:#E8F8F0; color:#1E8449; border-radius:6px; padding:2px 8px; font-size:.75rem; font-weight:600; }
  .tag-warn  { background:#FEF9E7; color:#B7950B; border-radius:6px; padding:2px 8px; font-size:.75rem; font-weight:600; }
  .tag-alert { background:#FDEDEC; color:#C0392B; border-radius:6px; padding:2px 8px; font-size:.75rem; font-weight:600; }
  .tag-flex  { background:#F0E8FB; color:#6B2FA0; border-radius:6px; padding:2px 8px; font-size:.75rem; font-weight:600; }
  .tag-conc  { background:#EAF1FB; color:#4A6FA5; border-radius:6px; padding:2px 8px; font-size:.75rem; font-weight:600; }
  .tag-relax { background:#E3F4F5; color:#3A8A8F; border-radius:6px; padding:2px 8px; font-size:.75rem; font-weight:600; }
  div[data-testid="stButton"] > button { border-radius:8px; font-weight:600; }
</style>""", unsafe_allow_html=True)

for k, v in [("authenticated",False),("patients",{}),("active_patient",None)]:
    if k not in st.session_state: st.session_state[k] = v

def get_groq(): return Groq(api_key=st.secrets["GROQ_API_KEY"])

# ── CSV parser ──────────────────────────────────────────────────────────────
def parse_csv(content):
    meta, stats_rows, in_stats, headers = {}, [], False, []
    for line in content.splitlines():
        line = line.strip()
        if not line: continue
        if line.startswith("[Statistics HEG-Ratio]"): in_stats=True; continue
        if line.startswith("[") and in_stats: in_stats=False; continue
        if not in_stats:
            if "=" in line and not line.startswith("["):
                p = line.split(";",2)
                if len(p)>=2: meta[p[0].replace("=","").strip()] = p[1].strip()
        else:
            p = [x.strip() for x in line.split(";")]
            if not headers: headers=p
            elif len(p)>=len(headers): stats_rows.append(dict(zip(headers,p)))

    def flt(v):
        try: return round(float(v),2)
        except: return None
    def it(v):
        try: return int(float(v))
        except: return None

    rows = []
    for r in stats_rows:
        rows.append({
            "state":           r.get("State","").strip().lower(),
            "percent_correct": it(r.get("percentCorrect")),
            "percent_false":   it(r.get("percentFalse")),
            "min":  flt(r.get("min")),  "max":  flt(r.get("max")),
            "mean": flt(r.get("mean")), "range":flt(r.get("range")),
            "points":        it(r.get("points")),
            "difficulty":    r.get("Difficulty 1:super easy - 5:super hard","").strip(),
            "threshold_max": flt(r.get("ThresholdMax")),
            "threshold_min": flt(r.get("ThresholdMin")),
        })

    states    = [r["state"] for r in rows]
    has_conc  = any("concentration" in s and "total" not in s for s in states)
    has_relax = any("relaxation"    in s and "total" not in s for s in states)
    if has_conc and has_relax:
        mode,sym,label = "flexibility","⇅","Flexibility"
    elif has_relax:
        mode,sym,label = "relaxation","∨","Relaxation"
    else:
        mode,sym,label = "concentration","∧","Concentration"

    total     = next((r for r in rows if r["state"]=="total"), rows[-1] if rows else {})
    conc_row  = next((r for r in rows if "concentration" in r["state"] and "total" in r["state"]), None)
    relax_row = next((r for r in rows if "relaxation"    in r["state"] and "total" in r["state"]), None)

    return {
        "patient_name": meta.get("Client","Unknown").strip(),
        "date":         meta.get("MeasurementDate","").replace(".","/"),
        "time":         meta.get("MeasurementTime","").rsplit(":",1)[0],
        "duration":     meta.get("TotalDuration","").strip(),
        "rows": rows, "total": total,
        "mode": mode, "mode_symbol": sym, "mode_label": label,
        "conc_row": conc_row, "relax_row": relax_row,
    }

def parse_zip(zip_bytes):
    sessions = []
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        for fname in sorted(f for f in zf.namelist() if f.lower().endswith(".csv")):
            try:
                raw = zf.read(fname).decode("utf-8",errors="replace")
                p = parse_csv(raw); p["filename"]=fname; sessions.append(p)
            except Exception as e: st.warning(f"Could not parse {fname}: {e}")
    return sessions

# ── Groq ────────────────────────────────────────────────────────────────────
def generate_report(patient_name, sessions):
    client = get_groq()
    lines  = []
    for i,s in enumerate(sessions,1):
        t = s.get("total",{})
        line = (f"Session {i} | Mode: {s.get('mode_label','?')} | Date: {s.get('date','?')} | "
                f"Duration: {s.get('duration','?')} | Difficulty: {t.get('difficulty','?')} | "
                f"Mean: {t.get('mean','?')} | Max: {t.get('max','?')} | Min: {t.get('min','?')} | "
                f"Range: {t.get('range','?')} | %Correct: {t.get('percent_correct','?')}% | "
                f"Points: {t.get('points','?')} | "
                f"Threshold Min: {t.get('threshold_min','?')} | Threshold Max: {t.get('threshold_max','?')}")
        if s.get("mode")=="flexibility":
            cr=s.get("conc_row") or {}; rr=s.get("relax_row") or {}
            line += (f" | [Conc sub: Mean={cr.get('mean','?')} %Correct={cr.get('percent_correct','?')}%]"
                     f" | [Relax sub: Mean={rr.get('mean','?')} Range={rr.get('range','?')}]")
        lines.append(line)

    n_c = sum(1 for s in sessions if s.get("mode")=="concentration")
    n_r = sum(1 for s in sessions if s.get("mode")=="relaxation")
    n_f = sum(1 for s in sessions if s.get("mode")=="flexibility")
    ms  = f"Concentration: {n_c} | Relaxation: {n_r} | Flexibility: {n_f}"

    # ── Patient copy prompt ──
    p1 = f"""Write a plain-language session summary for a patient receiving HEG neurofeedback. Simple, warm, non-technical. Factual only.

Patient: {patient_name} | Sessions: {len(sessions)} | {ms}
Data:
{chr(10).join(lines)}

Write EXACTLY this section. 4-5 sentences. No bullets. No markdown. No interpretation or judgment.

SESSION OVERVIEW
First 1-2 sentences: briefly explain in plain language what the training mode(s) used actually involve — what the brain is doing during this type of session, what the sensor measures, and what the patient is training. Keep this educational and simple, not clinical.
Remaining sentences: state only facts — total sessions, date range, session durations, mode(s) used (use plain words: focus training / relaxation training / flexibility training), and difficulty level used.
Do NOT evaluate results, do NOT mention absent modes, do NOT use words like improving/better/progress/declining."""

    r1 = client.chat.completions.create(model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":p1}], max_tokens=320, temperature=0.3)
    overview = re.sub(r'^SESSION OVERVIEW\s*\n?','',
                      r1.choices[0].message.content.strip(), flags=re.IGNORECASE).strip()

    # ── Clinical copy prompt ──
    p2 = f"""You are preparing a preliminary HEG neurofeedback signal observation summary to support Dr. Hany Elhennawy's clinical review. Your role is to describe what the recorded data shows — not to evaluate the protocol, not to make recommendations to the physician, and not to draw clinical conclusions. Dr. Hany holds full clinical authority. Present observations only.

IMPORTANT RULES:
- Describe signal behaviour, do not evaluate or judge it
- Do not use: recommend, should, inadequate, concerning, failed, missed, needs, must, verdict, suggests a problem
- Do not comment on what modes were absent or what should have been done
- Range values may reflect normal calibration variation — describe them neutrally, do not pathologise them
- Present all observations as data points for the physician's review, not as findings or conclusions
- Tone: a careful technician presenting recorded measurements to a senior clinician

Patient: {patient_name} | Sessions: {len(sessions)} | {ms}
Data:
{chr(10).join(lines)}

Write EXACTLY these 3 sections. 3 sentences each. Cite numbers. No bullets. No markdown.

METRICS OVERVIEW
Describe the session metrics across the training block by mode. For concentration: state Mean values across sessions, Threshold Min/Max range, %Correct values, Points. For relaxation: state Range values, %Correct, Mean. For flexibility: describe both dimensions. Present the numbers as observed values only.

SIGNAL BEHAVIOUR
Describe the pattern of the HEG signal across sessions — how Mean, Range, and Threshold values varied from session to session. Note any variation in Range values across sessions briefly and neutrally. Describe how %Correct related to Threshold changes across the block. Present as observed signal behaviour only.

SESSION PATTERN NOTES
Describe the overall pattern of the recorded sessions — difficulty levels used, session durations, and how the key metrics moved across the training block. Present as a factual description of what the data shows, for Dr. Hany's clinical review."""

    r2 = client.chat.completions.create(model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":p2}], max_tokens=600, temperature=0.25)

    return {
        "overview": overview,
        "clinical": r2.choices[0].message.content.strip(),
        "n_conc": n_c, "n_relax": n_r, "n_flex": n_f,
    }

# ── PDF ─────────────────────────────────────────────────────────────────────
def build_pdf(patient_name, sessions, report):
    STEEL  = HexColor("#4A6FA5"); ICE    = HexColor("#EAF1FB"); ICE2   = HexColor("#F4F8FE")
    TEAL   = HexColor("#3A8A8F"); TEAL_L = HexColor("#E3F4F5"); SILVER = HexColor("#E0E6EF")
    WARM   = HexColor("#F8FAFD"); NAVY   = HexColor("#1A2B4A"); TEXT   = HexColor("#1E2A3A")
    TEXT_M = HexColor("#5A6880"); TEXT_L = HexColor("#8A96A8"); STEEL_L= HexColor("#6B8FC2")
    PURPLE = HexColor("#6B2FA0"); PURP_L = HexColor("#F0E8FB")
    CONC_L = HexColor("#EAF1FB"); RELX_L = HexColor("#E3F4F5"); FLEX_L = HexColor("#F0E8FB")
    AMBER  = HexColor("#FEF3CD"); AMBER_B= HexColor("#D4A017")
    GREEN  = HexColor("#1E8449"); RED    = HexColor("#C0392B"); WHITE  = colors.white

    def S(n,**k): return ParagraphStyle(n,**k)
    TITLE  = S("T", fontName="Helvetica-Bold",  fontSize=12,textColor=WHITE, leading=15,alignment=TA_CENTER)
    TSUB   = S("TS",fontName="Helvetica",        fontSize=7.5,textColor=HexColor("#A8C8E8"),leading=9,alignment=TA_CENTER)
    META_B = S("MB",fontName="Helvetica-Bold",   fontSize=8, textColor=STEEL, leading=10)
    META   = S("M", fontName="Helvetica",        fontSize=8, textColor=TEXT_M,leading=10)
    SH     = S("SH",fontName="Helvetica-Bold",   fontSize=9, textColor=STEEL, leading=11,spaceBefore=4,spaceAfter=3)
    SH_T   = S("ST",fontName="Helvetica-Bold",   fontSize=9, textColor=TEAL,  leading=11,spaceBefore=4,spaceAfter=3)
    TH     = S("TH",fontName="Helvetica-Bold",   fontSize=6, textColor=STEEL, leading=7.5,alignment=TA_CENTER)
    TD     = S("TD",fontName="Helvetica",        fontSize=6, textColor=TEXT,  leading=7.5,alignment=TA_CENTER)
    BODY   = S("B", fontName="Helvetica",        fontSize=8.5,textColor=TEXT, leading=12.5,spaceAfter=4,alignment=TA_JUSTIFY)
    BODY_S = S("BS",fontName="Helvetica",        fontSize=8, textColor=TEXT,  leading=11.5,spaceAfter=3,alignment=TA_JUSTIFY)
    SECLBL = S("SL",fontName="Helvetica-Bold",   fontSize=7.5,textColor=STEEL,leading=10)
    SECT   = S("SLT",fontName="Helvetica-Bold",  fontSize=7.5,textColor=TEAL, leading=10)
    DISC   = S("DC",fontName="Helvetica-Oblique",fontSize=7.5,textColor=HexColor("#7A5800"),leading=10,alignment=TA_CENTER)
    FOOT   = S("F", fontName="Helvetica-Oblique",fontSize=6.5,textColor=TEXT_L,alignment=TA_CENTER)

    LM=RM=1.5*cm; TM=BM=1.2*cm
    W_page,H_page = A4; W = W_page-LM-RM
    buf = BytesIO()
    doc = SimpleDocTemplate(buf,pagesize=A4,leftMargin=LM,rightMargin=RM,topMargin=TM,bottomMargin=BM)
    story = []

    DISCLAIMER_TEXT = (
        "This document presents a preliminary data summary generated from recorded session metrics. "
        "It does not constitute a clinical assessment or final interpretation. "
        "The patient should receive the final clinical interpretation directly from Dr. Hany Elhennawy."
    )

    def banner(badge_txt, badge_col):
        bw=2.4*cm; tw=W-bw
        t=Table([[
            [Paragraph("nIR HEG NEUROFEEDBACK — Progress Report",TITLE),
             Paragraph("Dr. Hany Elhennawy Psychiatric Center",TSUB)],
            Paragraph(badge_txt,S("BG",fontName="Helvetica-Bold",fontSize=7,textColor=WHITE,leading=9,alignment=TA_CENTER))
        ]],colWidths=[tw,bw])
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(0,-1),NAVY),("BACKGROUND",(1,0),(1,-1),badge_col),
            ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
            ("LEFTPADDING",(0,0),(0,-1),12),("LEFTPADDING",(1,0),(1,-1),4),
            ("RIGHTPADDING",(0,0),(-1,-1),6),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("ALIGN",(1,0),(1,-1),"CENTER"),
        ]))
        return t

    def meta_strip():
        mc=[1.5*cm,4.0*cm,1.7*cm,1.5*cm,1.8*cm,W-10.5*cm]
        t=Table([[Paragraph("Patient:",META_B),Paragraph(patient_name,META),
                  Paragraph("Sessions:",META_B),Paragraph(str(len(sessions)),META),
                  Paragraph("Date:",META_B),Paragraph(datetime.now().strftime("%d.%m.%Y"),META)]],
                colWidths=mc,rowHeights=[0.55*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),ICE),("BOX",(0,0),(-1,-1),0.5,SILVER),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("LEFTPADDING",(0,0),(-1,-1),7),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ]))
        return t

    def disclaimer_box():
        t=Table([[Paragraph(DISCLAIMER_TEXT,DISC)]],colWidths=[W])
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),AMBER),
            ("BOX",(0,0),(-1,-1),0.7,AMBER_B),
            ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
            ("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),
        ]))
        return t

    def mode_fill(m):
        return FLEX_L if m=="flexibility" else (RELX_L if m=="relaxation" else CONC_L)

    def footer(label):
        story.append(Spacer(1,4))
        story.append(HRFlowable(width="100%",thickness=0.4,color=SILVER,spaceAfter=3))
        story.append(Paragraph(
            f"nIR HEG Sessions · Dr. Hany Elhennawy Psychiatric Center · "
            f"Generated {datetime.now().strftime('%d.%m.%Y %H:%M')} · {label}",FOOT))

    # ═══════════════════════════════════════════
    # PAGE 1 — PATIENT COPY
    # ═══════════════════════════════════════════
    story.append(banner("PATIENT COPY",TEAL))
    story.append(Spacer(1,4))
    story.append(meta_strip())
    story.append(Spacer(1,5))

    # Mode summary strip
    nc,nr,nf = report["n_conc"],report["n_relax"],report["n_flex"]
    mode_items=[]
    if nc: mode_items.append((f"∧  Focus Training — {nc} session{'s' if nc>1 else ''}",STEEL,CONC_L))
    if nr: mode_items.append((f"∨  Relaxation Training — {nr} session{'s' if nr>1 else ''}",TEAL,RELX_L))
    if nf: mode_items.append((f"⇅  Flexibility Training — {nf} session{'s' if nf>1 else ''}",PURPLE,PURP_L))
    if mode_items:
        sw2=W/len(mode_items)
        cells=[Table([[Paragraph(txt,S("mc",fontName="Helvetica-Bold",fontSize=8.5,
                                        textColor=col,leading=11,alignment=TA_CENTER))]],colWidths=[sw2])
               for txt,col,fill in mode_items]
        st2=Table([cells],colWidths=[sw2]*len(cells))
        st2.setStyle(TableStyle([
            ("BOX",(0,0),(-1,-1),0.5,SILVER),
            ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
            *[("BACKGROUND",(i,0),(i,0),mode_items[i][2]) for i in range(len(mode_items))],
        ]))
        story.append(st2); story.append(Spacer(1,5))

    # Session table
    story.append(Paragraph("Session Data",SH))
    story.append(HRFlowable(width="100%",thickness=0.8,color=STEEL,spaceAfter=3))

    hdr=["#","Date","Dur.","Mode","Difficulty","Mean","Max","Min","Range","%Corr","Thr\nMin","Thr\nMax","Points"]
    scm=[0.5,1.7,1.55,1.5,1.6,1.1,1.1,1.1,1.1,1.2,1.2,1.2,1.2]
    sw=[x*cm for x in scm]; sw[-1]=W-sum(sw[:-1])
    s_rows=[[Paragraph(t,TH) for t in hdr]]
    row_fills=[]
    for i,s in enumerate(sessions):
        t2=s.get("total",{}); pct=t2.get("percent_correct")
        row_fills.append(mode_fill(s.get("mode","concentration")))
        s_rows.append([Paragraph(str(v) if v is not None else "—",TD) for v in [
            i+1, s.get("date","—"), s.get("duration","—").strip(),
            s.get("mode_symbol","∧"), t2.get("difficulty","—"),
            t2.get("mean","—"),t2.get("max","—"),t2.get("min","—"),t2.get("range","—"),
            f"{pct}%" if pct is not None else "—",
            t2.get("threshold_min","—"),t2.get("threshold_max","—"),t2.get("points","—"),
        ]])
    n_s=max(len(sessions),1); HDR_H=0.85*cm
    DATA_H=max(0.58*cm,min(1.0*cm,(11.0*cm-HDR_H)/n_s))
    st3=Table(s_rows,colWidths=sw,repeatRows=1,rowHeights=[HDR_H]+[DATA_H]*len(sessions))
    alt=[("BACKGROUND",(0,r+1),(-1,r+1),row_fills[r]) for r in range(len(sessions))]
    st3.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),NAVY),("TEXTCOLOR",(0,0),(-1,0),WHITE),*alt,
        ("BOX",(0,0),(-1,-1),0.7,STEEL_L),("INNERGRID",(0,0),(-1,-1),0.3,SILVER),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),3),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    story.append(st3); story.append(Spacer(1,4))

    # Colour legend
    if mode_items:
        lw2=W/len(mode_items)
        lcells=[Table([[Paragraph(txt,S("lc",fontName="Helvetica-Bold",fontSize=7.5,
                                         textColor=col,leading=9,alignment=TA_CENTER))]],colWidths=[lw2])
                for txt,col,fill in mode_items]
        lt=Table([lcells],colWidths=[lw2]*len(lcells))
        lt.setStyle(TableStyle([
            ("BOX",(0,0),(-1,-1),0.4,SILVER),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
            *[("BACKGROUND",(i,0),(i,0),mode_items[i][2]) for i in range(len(mode_items))],
        ]))
        story.append(lt); story.append(Spacer(1,6))

    # Session overview box
    story.append(Paragraph("Session Overview",SH_T))
    story.append(HRFlowable(width="100%",thickness=0.8,color=TEAL,spaceAfter=4))
    ob=Table([[Paragraph(report["overview"],BODY)]],colWidths=[W])
    ob.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),TEAL_L),("BOX",(0,0),(-1,-1),0.8,TEAL),
        ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10),
        ("LEFTPADDING",(0,0),(-1,-1),14),("RIGHTPADDING",(0,0),(-1,-1),14),
    ]))
    story.append(ob); story.append(Spacer(1,6))

    # Disclaimer
    story.append(disclaimer_box())
    footer("Patient Copy")

    # ═══════════════════════════════════════════
    # PAGE 2 — CLINICAL STAFF COPY
    # ═══════════════════════════════════════════
    story.append(PageBreak())
    story.append(banner("CLINICAL STAFF",STEEL))
    story.append(Spacer(1,4)); story.append(meta_strip()); story.append(Spacer(1,6))

    # Disclaimer on staff page too
    story.append(disclaimer_box()); story.append(Spacer(1,6))

    story.append(Paragraph("Preliminary Signal Observations — For Dr. Hany Elhennawy's Review",SH))
    story.append(HRFlowable(width="100%",thickness=0.8,color=STEEL,spaceAfter=5))

    clinical_sections=[
        ("METRICS OVERVIEW",      ICE2,  STEEL, False),
        ("SIGNAL BEHAVIOUR",      WARM,  STEEL, False),
        ("SESSION PATTERN NOTES", ICE2,  STEEL, False),
    ]
    remaining=report["clinical"]
    for title,fill,bcol,is_sum in clinical_sections:
        if title not in remaining: body_text=""
        else:
            parts=remaining.split(title,1); remaining=parts[1] if len(parts)>1 else ""
            nxt=len(remaining)
            for o,_,_,_ in clinical_sections:
                if o!=title and o in remaining:
                    idx=remaining.index(o)
                    if idx<nxt: nxt=idx
            body_text=remaining[:nxt].strip(); remaining=remaining[nxt:]
        dtitle=title.title()
        sec=Table([[Paragraph(dtitle,SECLBL)],[Paragraph(body_text,BODY_S)]],colWidths=[W])
        sec.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),fill),("BACKGROUND",(0,0),(0,0),HexColor("#DDEEF9")),
            ("BOX",(0,0),(-1,-1),0.6,bcol),("LINEBELOW",(0,0),(0,0),0.4,SILVER),
            ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
            ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
            ("VALIGN",(0,0),(-1,-1),"TOP"),
        ]))
        story.append(sec); story.append(Spacer(1,4))

    story.append(Spacer(1,4))

    # Trend table
    story.append(Paragraph("Metrics Trend Summary — by Mode",SH))
    story.append(HRFlowable(width="100%",thickness=0.8,color=STEEL,spaceAfter=4))

    def get_trend(key,mfilter=None):
        src=sessions if not mfilter else [s for s in sessions if s.get("mode")==mfilter]
        vals=[s.get("total",{}).get(key) for s in src if s.get("total",{}).get(key) is not None]
        if len(vals)<2: return str(vals[0]) if vals else "—","—","—"
        f2,l2=vals[0],vals[-1]
        if key=="range": arr="↓ Narrowing" if l2<f2 else ("↑ Widening" if l2>f2 else "= Stable")
        else:            arr="↑ Rising"    if l2>f2 else ("↓ Declining" if l2<f2 else "= Stable")
        return str(f2),str(l2),arr

    def tcol(arr,key):
        if key=="range": return GREEN if "↓" in arr else (RED if "↑" in arr else TEXT_M)
        return GREEN if "↑" in arr else (RED if "↓" in arr else TEXT_M)

    tw2=[3.0*cm,2.8*cm,2.4*cm,2.4*cm,W-10.6*cm]
    def TS(n,**k): return ParagraphStyle(n,**k)
    trows=[[Paragraph(x,TH) for x in ["Metric","Mode","First","Last","Trend"]]]
    mconfigs=[]
    if nc: mconfigs.append(("concentration","∧ Concentration",
                             [("Mean HEG","mean"),("% Correct","percent_correct"),
                              ("Threshold Max","threshold_max"),("Points","points")]))
    if nr: mconfigs.append(("relaxation","∨ Relaxation",
                             [("Range","range"),("% Correct","percent_correct"),
                              ("Mean HEG","mean"),("Points","points")]))
    if nf: mconfigs.append(("flexibility","⇅ Flexibility",
                             [("Mean HEG","mean"),("Range","range"),
                              ("% Correct","percent_correct"),("Points","points")]))
    for mk,mn,metrics in mconfigs:
        for lbl,key in metrics:
            f2,l2,arr=get_trend(key,mk); col=tcol(arr,key)
            trows.append([
                Paragraph(lbl, TS("tl",fontName="Helvetica-Bold",fontSize=7.5,textColor=NAVY,  leading=9)),
                Paragraph(mn,  TS("tm",fontName="Helvetica",      fontSize=7.5,textColor=TEXT_M,leading=9,alignment=TA_CENTER)),
                Paragraph(f2,  TS("tv",fontName="Helvetica",      fontSize=7.5,textColor=TEXT_M,leading=9,alignment=TA_CENTER)),
                Paragraph(l2,  TS("tv2",fontName="Helvetica",     fontSize=7.5,textColor=TEXT_M,leading=9,alignment=TA_CENTER)),
                Paragraph(arr, TS("ta",fontName="Helvetica-Bold", fontSize=7.5,textColor=col,   leading=9,alignment=TA_CENTER)),
            ])
    tt=Table(trows,colWidths=tw2)
    alt3=[("BACKGROUND",(0,r),(-1,r),WARM if r%2==0 else ICE2) for r in range(1,len(trows))]
    tt.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),NAVY),("TEXTCOLOR",(0,0),(-1,0),WHITE),*alt3,
        ("BOX",(0,0),(-1,-1),0.7,STEEL_L),("INNERGRID",(0,0),(-1,-1),0.3,SILVER),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),5),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    story.append(tt)
    footer("Clinical Staff Copy — For Dr. Hany Elhennawy's Review")

    doc.build(story); buf.seek(0); return buf

# ── UI ───────────────────────────────────────────────────────────────────────
if not st.session_state.authenticated:
    st.markdown("""<div class="hero-banner"><h1>🧠 nIR HEG Session Tracker</h1>
    <p>Dr. Hany Elhennawy Psychiatric Center — Clinical Neurofeedback Suite</p></div>""",
    unsafe_allow_html=True)
    c1,c2,c3=st.columns([1,1.2,1])
    with c2:
        st.markdown('<div class="card"><h3>🔒 Access Required</h3>',unsafe_allow_html=True)
        code=st.text_input("Enter access code",type="password",placeholder="Access code")
        if st.button("Enter",use_container_width=True):
            valid=[c.strip() for c in st.secrets.get("ACCESS_CODE","").split(",")]
            if code in valid: st.session_state.authenticated=True; st.rerun()
            else: st.error("Invalid access code.")
        st.markdown('</div>',unsafe_allow_html=True)
    st.stop()

st.markdown("""<div class="hero-banner"><h1>🧠 nIR HEG Session Tracker</h1>
<p>Dr. Hany Elhennawy Psychiatric Center — Upload ZIP · Parse sessions · Generate reports</p>
</div>""",unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### 👤 Patients")
    with st.expander("➕ Add New Patient",expanded=not st.session_state.patients):
        new_name=st.text_input("Patient name",placeholder="Full name")
        if st.button("Add Patient",use_container_width=True):
            name=new_name.strip()
            if name:
                if name not in st.session_state.patients: st.session_state.patients[name]=[]
                st.session_state.active_patient=name; st.rerun()
            else: st.warning("Please enter a name.")
    st.divider()
    for pname in list(st.session_state.patients.keys()):
        n=len(st.session_state.patients[pname]); is_active=st.session_state.active_patient==pname
        if st.button(f"{'▶ ' if is_active else ''}{pname}  ({n} session{'s' if n!=1 else ''})",
                     key=f"p_{pname}",use_container_width=True,
                     type="primary" if is_active else "secondary"):
            st.session_state.active_patient=pname; st.rerun()
    st.divider()
    if st.button("🔒 Log Out",use_container_width=True):
        st.session_state.authenticated=False; st.rerun()

if not st.session_state.active_patient:
    st.info("👈 Select or add a patient from the sidebar to get started.")
    st.stop()

patient=st.session_state.active_patient; sessions=st.session_state.patients[patient]
st.markdown(f"## {patient}")
st.caption(f"{len(sessions)} session{'s' if len(sessions)!=1 else ''} recorded")

tab1,tab2=st.tabs(["📥 Upload ZIP","📊 Sessions & Report"])

with tab1:
    st.markdown('<div class="card"><h3>📦 Upload Session ZIP File</h3>',unsafe_allow_html=True)
    st.caption("Export the results ZIP from the Body & Mind app (via email) and upload here.")
    uploaded=st.file_uploader("Choose ZIP file",type=["zip"],label_visibility="collapsed")
    if uploaded:
        st.info(f"**{uploaded.name}** · {uploaded.size/1024:.1f} KB")
        if st.button("📂 Parse & Import All Sessions",type="primary"):
            with st.spinner("Parsing sessions..."):
                try:
                    parsed=parse_zip(uploaded.read())
                    if not parsed: st.error("No valid CSV files found.")
                    else:
                        st.session_state.patients[patient]=parsed
                        st.success(f"✅ {len(parsed)} session{'s' if len(parsed)!=1 else ''} imported!")
                        for i,s in enumerate(parsed,1):
                            t2=s.get("total",{}); pct=t2.get("percent_correct",0) or 0
                            tag="good" if pct>=60 else ("warn" if pct>=40 else "alert")
                            sym=s.get("mode_symbol","∧")
                            mode_css={"concentration":"conc","relaxation":"relax","flexibility":"flex"}.get(s.get("mode",""),"conc")
                            st.markdown(
                                f"**#{i}** · {s.get('date','?')} · "
                                f'<span class="tag-{mode_css}">{sym} {s.get("mode_label","?")}</span> · '
                                f"{s.get('duration','?').strip()} · Mean: **{t2.get('mean','?')}** · "
                                f'<span class="tag-{tag}">{pct}% correct</span> · '
                                f"Points: **{t2.get('points','?')}**",unsafe_allow_html=True)
                        st.rerun()
                except Exception as e: st.error(f"Error: {e}")
    st.markdown('</div>',unsafe_allow_html=True)

with tab2:
    if not sessions: st.info("No sessions yet. Upload a ZIP in the Upload tab.")
    else:
        st.markdown("### Session Log")
        cols_h=st.columns([0.4,1.1,1.3,0.65,1.3,0.85,0.85,0.85,0.85,0.95,1.0,1.0,0.75])
        for col,h in zip(cols_h,["#","Date","Duration","Mode","Difficulty","Mean","Max","Min",
                                   "Range","%Correct","Thresh Min","Thresh Max","Points"]):
            col.markdown(f"**{h}**")
        st.divider()
        for i,s in enumerate(sessions):
            t2=s.get("total",{}); pct=t2.get("percent_correct",0) or 0
            tag="good" if pct>=60 else ("warn" if pct>=40 else "alert")
            mode=s.get("mode","concentration"); sym=s.get("mode_symbol","∧")
            mode_css={"concentration":"conc","relaxation":"relax","flexibility":"flex"}.get(mode,"conc")
            cols=st.columns([0.4,1.1,1.3,0.65,1.3,0.85,0.85,0.85,0.85,0.95,1.0,1.0,0.75])
            cols[0].write(f"**#{i+1}**"); cols[1].write(s.get("date","—")); cols[2].write(s.get("duration","—").strip())
            cols[3].markdown(f'<span class="tag-{mode_css}">{sym}</span>',unsafe_allow_html=True)
            cols[4].write(str(t2.get("difficulty","—"))); cols[5].write(str(t2.get("mean","—")))
            cols[6].write(str(t2.get("max","—"))); cols[7].write(str(t2.get("min","—")))
            cols[8].write(str(t2.get("range","—")))
            cols[9].markdown(f'<span class="tag-{tag}">{pct}%</span>',unsafe_allow_html=True)
            cols[10].write(str(t2.get("threshold_min","—"))); cols[11].write(str(t2.get("threshold_max","—")))
            cols[12].write(str(t2.get("points","—")))
        st.divider()
        if len(sessions)>=2:
            st.markdown("### Trend Overview")
            tc=st.columns(6)
            def trend(key,mf=None):
                src=sessions if not mf else [s for s in sessions if s.get("mode")==mf]
                vals=[s.get("total",{}).get(key) for s in src if s.get("total",{}).get(key) is not None]
                if len(vals)<2: return "—","—"
                return ("↑" if vals[-1]>vals[0] else ("↓" if vals[-1]<vals[0] else "=")),str(vals[-1])
            for col,(lbl,key) in zip(tc,[("Mean","mean"),("%Correct","percent_correct"),
                                          ("Points","points"),("Range","range"),
                                          ("Thresh Max","threshold_max"),("Difficulty","difficulty")]):
                arr,last=trend(key); col.metric(lbl,arr,f"Last: {last}")
        st.divider()
        st.markdown("### Generate Report")
        st.caption("2-page PDF — Page 1 (patient copy) · Page 2 (clinical staff copy for Dr. Hany's review)")
        if st.button("📋 Generate Report",type="primary"):
            with st.spinner("Generating report..."):
                try:
                    rep=generate_report(patient,sessions)
                    pdf=build_pdf(patient,sessions,rep)
                    st.success("✅ Report ready!")
                    with st.expander("Preview content"):
                        st.markdown("**Page 1 — Patient Copy:**"); st.text(rep["overview"])
                        st.markdown("**Page 2 — Clinical Staff:**"); st.text(rep["clinical"])
                    fname=f"HEG_Report_{patient.replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                    st.download_button("⬇️ Download Report",data=pdf,file_name=fname,mime="application/pdf")
                except Exception as e: st.error(f"Error: {e}")
