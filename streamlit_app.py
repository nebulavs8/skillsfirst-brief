import re
import io
import zipfile
from datetime import datetime
from dateutil import parser as dateparser

import streamlit as st
import pandas as pd
from pypdf import PdfReader

# Google Sheets
import gspread
from google.oauth2.service_account import Credentials

# ------------------ App Branding ------------------
APP_TITLE = "Skills-First Brief — 1-Page Action Brief"
BRAND_NOTE = "Skills-First Brief • Skills-First Blueprint"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ------------------ Google Sheets Helpers ------------------
@st.cache_resource(show_spinner=False)
def get_worksheet():
    creds_info = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet_id = st.secrets["sheets"]["sheet_id"]
    ws_name  = st.secrets["sheets"]["worksheet"]
    sh = client.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(ws_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=ws_name, rows=1000, cols=20)
        ws.update("A1:H1", [["timestamp","document","name","role","email","skills","proof_note_or_link","source_app"]])
    return ws

def append_receipt_row_to_sheets(row: dict):
    try:
        ws = get_worksheet()
        ws.append_row(
            [
                row.get("timestamp",""),
                row.get("document",""),
                row.get("name",""),
                row.get("role",""),
                row.get("email",""),
                row.get("skills",""),
                row.get("proof_note_or_link",""),
                "skills-first-brief"
            ],
            value_input_option="USER_ENTERED"
        )
        return True
    except Exception as e:
        st.toast(f"Sheets log skipped: {e}", icon="⚠️")
        return False

# ------------------ PDF/Text helpers ------------------
def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages)

def clean_text(txt: str) -> str:
    txt = txt.replace("\x00", " ").strip()
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    return txt

# ------------------ Simple Summarizer ------------------
STOPWORDS = set("""a about above after again against all am an and any are aren't as at be because been before being below between both but by can't cannot could couldn't did didn't do does doesn't doing don't down during each few for from further had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't my myself no nor not of off on once only or other ought our ours ourselves out over own same shan't she she'd she'll she's should shouldn't so some such than that that's the their theirs them themselves then there there's these they they'd they'll they're they've this those through to too under until up very was wasn't we we'd we'll we're we've were weren't what what's when when's where where's which while who who's whom why why's with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves""".split())

def sentence_split(text: str):
    sents = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sents if s.strip()]

def word_tokens(text: str):
    return [w.lower() for w in re.findall(r"[A-Za-z']+", text)]

def summarize_text(text: str, max_sentences: int = 5) -> str:
    sents = sentence_split(text)
    if not sents:
        return ""
    words = word_tokens(text)
    if not words:
        return " "
    freq = {}
    for w in words:
        if w in STOPWORDS or len(w) <= 2:
            continue
        freq[w] = freq.get(w, 0) + 1
    if not freq:
        return " ".join(sents[:max_sentences])
    m = max(freq.values())
    for w in list(freq.keys()):
        freq[w] = freq[w] / m
    scored = []
    for idx, s in enumerate(sents):
        toks = word_tokens(s)
        if not toks: continue
        length = max(1, len(toks))
        ideal = 24
        length_penalty = 1.0 - min(0.6, abs(length - ideal) / float(ideal + 1))
        score = sum(freq.get(t, 0) for t in toks) * (0.4 + 0.6 * length_penalty)
        scored.append((idx, s, score))
    top = sorted(scored, key=lambda x: x[2], reverse=True)[:max_sentences]
    top_sorted = [t[1] for t in sorted(top, key=lambda x: x[0])]
    return " ".join(top_sorted).strip()

# ------------------ Info extraction ------------------
def find_deadlines(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    hit_lines = [l for l in lines if re.search(r"\b(deadline|due|submit by|no later than)\b", l, re.I)]
    date_hits = re.findall(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b", text)
    parsed_dates = []
    for d in date_hits:
        try:
            parsed_dates.append(dateparser.parse(d, fuzzy=True))
        except Exception: pass
    parsed_dates = sorted(set(parsed_dates))
    pretty = [dt.strftime("%b %d, %Y") for dt in parsed_dates]
    return hit_lines, pretty

def find_requirements(text: str):
    req_lines = []
    for l in text.splitlines():
        l_clean = l.strip()
        if not l_clean: continue
        if re.match(r"^(\*|-|•|\d+\.)\s+", l_clean) or len(l_clean) < 220:
            if re.search(r"\b(must|required?|need to|provide|eligib|documentation|proof|return|submit)\b", l_clean, re.I):
                req_lines.append(re.sub(r"^(\*|-|•|\d+\.)\s*", "", l_clean))
    seen = set(); out = []
    for x in req_lines:
        k = x.lower()
        if k not in seen:
            seen.add(k); out.append(x)
    return out[:12]

def find_key_points(text: str, top_n: int = 6):
    candidates = []
    for l in text.splitlines():
        l = l.strip(" -•*\t")
        if 40 <= len(l) <= 220 and not l.endswith(":"):
            candidates.append(l)
    seen = set(); out = []
    for c in candidates:
        k = c.lower()
        if k not in seen:
            seen.add(k); out.append(c)
    return out[:top_n]

# ------------------ Skills mapping ------------------
SKILL_DICTIONARY = {
    r"\bimmuni[sz]ation|vaccine\b": "Health Documentation",
    r"\bconsent form|permission slip\b": "Consent & Forms",
    r"\btransportation request|bus route\b": "Logistics Coordination",
    r"\bdevice return|chromebook|laptop\b": "Device Management",
    r"\bparent[- ]teacher conference|ptc\b": "Scheduling & Coordination",
    r"\bworkshop|training session\b": "Workshop Participation",
    r"\bapplication\b": "Application Submission",
    r"\bdeadline|submit by|due\b": "Deadline Management",
    r"\brfp|proposal|brief\b": "RFP Review",
    r"\brequirements?|eligib(ility|le)\b": "Requirements Compliance",
    r"\bpolicy|guideline\b": "Policy Comprehension",
    r"\bdocumentation|records\b": "Documentation Management",
    r"\bdata\b": "Data Handling",
    r"\bsalesforce\b": "Salesforce Literacy",
    r"\bai|nlp|summari[sz]e?\b": "AI/NLP Literacy",
}

def extract_skills(text: str, requirements: list[str]) -> list[str]:
    pool = text + "\n" + "\n".join(requirements or [])
    found = []
    for pattern, skill in SKILL_DICTIONARY.items():
        if re.search(pattern, pool, re.I): found.append(skill)
    seen = set(); out = []
    for s in found:
        if s not in seen:
            seen.add(s); out.append(s)
    if not out:
        out = ["Deadline Management", "Documentation Management", "Policy Comprehension"]
    return out[:15]

# ------------------ Brief + receipts ------------------
def make_brief(text: str):
    exec_summary = summarize_text(text, max_sentences=5)
    key_points = find_key_points(text, top_n=6)
    deadline_lines, parsed_dates = find_deadlines(text)
    requirements = find_requirements(text)
    next_steps = propose_next_steps(requirements, (deadline_lines, parsed_dates))
    return {"Executive Summary": exec_summary or "Not enough content to summarize.",
            "Key Points": key_points,
            "Deadlines": parsed_dates if parsed_dates else deadline_lines[:3],
            "Requirements": requirements,
            "Next Steps": next_steps}

def propose_next_steps(reqs, deadlines):
    steps = []
    if deadlines[1]: steps.append(f"Add key date(s) to calendar: {', '.join(deadlines[1][:3])}.")
    if reqs: steps.append("Gather required documents/items listed above and upload 48 hours before the deadline.")
    steps.extend(["Email teacher/admin with questions.","Confirm submission method and save the confirmation.","Schedule a 15-minute review with your child/team."])
    seen=set(); final=[]
    for s in steps:
        if s not in seen: seen.add(s); final.append(s)
    return final[:5]

def make_skills_receipt_row(doc_name, user_name, user_role, user_email, skills_selected, proof_note):
    return {"timestamp": datetime.now().isoformat(),
            "document": doc_name,
            "name": user_name,
            "role": user_role,
            "email": user_email,
            "skills": "; ".join(skills_selected) if skills_selected else "",
            "proof_note_or_link": proof_note}

def pack_zip(csv_bytes: bytes, md_bytes: bytes, proof_file) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("skills_receipt.csv", csv_bytes)
        z.writestr("skills_receipt.md", md_bytes)
        if proof_file is not None:
            z.writestr(f"proof/{proof_file.name}", proof_file.getvalue())
    buf.seek(0); return buf.read()

# ------------------ UI ------------------
st.set_page_config(page_title=APP_TITLE, page_icon="📝", layout="centered")
st.title(APP_TITLE); st.caption(BRAND_NOTE)

st.write("Upload a school/work **PDF** or **TXT**. You’ll get a 1-page brief with the main points, deadlines, requirements, and clear next steps.")
st.markdown("[📅 Book a discovery call](https://calendly.com/bmceachin/30min) • [💼 Connect on LinkedIn](https://www.linkedin.com/in/brittanymceachin2010/)")

uploaded = st.file_uploader("Upload document", type=["pdf", "txt"])
with_styling = st.toggle("Use condensed layout", value=True)

if uploaded:
    if uploaded.type == "application/pdf" or uploaded.name.lower().endswith(".pdf"):
        raw = uploaded.read(); text = extract_text_from_pdf(raw)
    else: text = uploaded.read().decode("utf-8", errors="ignore")
    text = clean_text(text)

    if not text or len(text.strip()) < 200:
        st.warning("This file looks very short or may be a scanned PDF.")

    if st.button("Generate 1-Page Brief"):
        with st.spinner("Summarizing…"):
            brief = make_brief(text)

        st.markdown("## 1-Page Action Brief")
        for sec, val in brief.items():
            st.markdown(f"### {sec}")
            if isinstance(val, list):
                if val: [st.markdown(f"- {v}") for v in val]
                else: st.markdown("_None found._")
            else: st.markdown(val)

        st.markdown("---"); st.subheader("Map Required Skills & Log Proof")

        inferred_skills = extract_skills(text, brief.get("Requirements", []))
        skills_selected = st.multiselect("Select applicable skills", options=sorted(set(inferred_skills)), default=inferred_skills)

        user_name = st.text_input("Your name")
        user_role = st.selectbox("Your role", ["Parent","Teacher","Student","Org/Admin","Other"])
        user_email = st.text_input("Email (optional)")
        proof_file = st.file_uploader("Attach proof (optional)", type=["png","jpg","jpeg","pdf","txt","md"])
        proof_note = st.text_input("Proof note or link (optional)")

        if st.button("Create Skills Receipt"):
            if not user_name: st.error("Please enter your name.")
            else:
                row = make_skills_receipt_row(uploaded.name, user_name, user_role, user_email, skills_selected, proof_note)
                df = pd.DataFrame([row]); csv_bytes = df.to_csv(index=False).encode("utf-8")
                md_receipt = f"""# Skills Receipt
- **Timestamp:** {row['timestamp']}
- **Document:** {row['document']}
- **Name:** {row['name']}
- **Role:** {row['role']}
- **Email:** {row['email'] or '—'}
- **Skills Mapped:** {row['skills'] or '—'}
- **Proof Note/Link:** {row['proof_note_or_link'] or '—'}

---
{BRAND_NOTE}"""
                md_bytes = md_receipt.encode("utf-8")

                logged = append_receipt_row_to_sheets(row)
                if logged: st.success("Logged to Google Sheets ✅")

                c1,c2,c3=st.columns(3)
                with c1: st.download_button("⬇️ CSV", data=csv_bytes, file_name="skills_receipt.csv", mime="text/csv")
                with c2: st.download_button("⬇️ Markdown", data=md_bytes, file_name="skills_receipt.md", mime="text/markdown")
                with c3: st.download_button("⬇️ ZIP", data=pack_zip(csv_bytes, md_bytes, proof_file), file_name="skills_receipt_bundle.zip", mime="application/zip")


