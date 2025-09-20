import re
import io
from datetime import datetime
from dateutil import parser as dateparser

import streamlit as st
from pypdf import PdfReader

# --- Sumy (lightweight summarization)
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lex_rank import LexRankSummarizer

APP_TITLE = "SchoolDoc AI — 1-Page Action Brief"
BRAND_NOTE = "Skills-First Blueprint • Families • Teachers • Orgs"

# ---------- Helpers
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
    # Collapse excessive whitespace
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    return txt

def summarize_lexrank(text: str, sentences: int = 6) -> str:
    # Sumy prefers <= ~8k-10k chars; chunk if very long
    text = text.strip()
    if not text:
        return ""
    max_chars = 8000
    chunks = [text[i:i+max_chars] for i in range(0, len(text), max_chars)]
    summarizer = LexRankSummarizer()
    summaries = []
    for chunk in chunks:
        parser = PlaintextParser.from_string(chunk, Tokenizer("english"))
        sents = summarizer(parser.document, sentences)
        summaries.append(" ".join([str(s) for s in sents]))
    # second-pass compress
    merged = " ".join(summaries)
    if len(chunks) > 1:
        parser2 = PlaintextParser.from_string(merged, Tokenizer("english"))
        sents2 = summarizer(parser2.document, min(sentences, 7))
        return " ".join([str(s) for s in sents2])
    return merged

def find_deadlines(text: str):
    # Grab lines with deadline-ish words + parse dates if present
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    hit_lines = [l for l in lines if re.search(r"\b(deadline|due|submit by|no later than)\b", l, re.I)]
    # Also find date patterns
    date_hits = re.findall(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b", text)
    parsed_dates = []
    for d in date_hits:
        try:
            parsed_dates.append(dateparser.parse(d, fuzzy=True))
        except Exception:
            pass
    parsed_dates = sorted(set(parsed_dates))
    pretty = [dt.strftime("%b %d, %Y") for dt in parsed_dates]
    return hit_lines, pretty

def find_requirements(text: str):
    # Heuristics: capture bullet/numbered lines with must/require/need/provide/eligibility
    req_lines = []
    for l in text.splitlines():
        l_clean = l.strip()
        if not l_clean:
            continue
        if re.match(r"^(\*|-|•|\d+\.)\s+", l_clean) or len(l_clean) < 220:
            if re.search(r"\b(must|required?|need to|provide|eligib|documentation|proof)\b", l_clean, re.I):
                req_lines.append(re.sub(r"^(\*|-|•|\d+\.)\s*", "", l_clean))
    # Deduplicate while preserving order
    seen = set()
    out = []
    for x in req_lines:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out[:10]

def find_key_points(text: str, top_n: int = 6):
    # Shallow: take short, informative lines (titles/subheads/bullets)
    candidates = []
    for l in text.splitlines():
        l = l.strip(" -•*\t")
        if 40 <= len(l) <= 220:
            if not l.endswith(":"):
                candidates.append(l)
    # Deduplicate
    seen = set()
    out = []
    for c in candidates:
        k = c.lower()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out[:top_n]

def propose_next_steps(reqs, deadlines):
    steps = []
    if deadlines[1]:
        steps.append(f"Add key date(s) to calendar: {', '.join(deadlines[1][:3])}.")
    if reqs:
        steps.append("Gather required documents/items listed above and upload 48 hours before the deadline.")
    steps.extend([
        "Email teacher/admin with any clarifying questions (keep it to 3 bullets).",
        "Confirm submission method (portal, email, or printed copy) and save the confirmation.",
        "Schedule a 15-minute review with your child/team to align on what success looks like."
    ])
    # Deduplicate
    seen = set()
    final = []
    for s in steps:
        if s not in seen:
            seen.add(s)
            final.append(s)
    return final[:5]

def make_brief(text: str):
    exec_summary = summarize_lexrank(text, sentences=5)
    key_points = find_key_points(text, top_n=6)
    deadline_lines, parsed_dates = find_deadlines(text)
    requirements = find_requirements(text)
    next_steps = propose_next_steps(requirements, (deadline_lines, parsed_dates))
    return {
        "Executive Summary": exec_summary or "Not enough content to summarize.",
        "Key Points": key_points,
        "Deadlines": parsed_dates if parsed_dates else deadline_lines[:3],
        "Requirements": requirements,
        "Next Steps": next_steps
    }

def brief_to_markdown(brief: dict, source_name: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    md = [f"# 1-Page Action Brief\n*Source:* **{source_name}**  \n*Generated:* {ts}\n"]
    for section in ["Executive Summary", "Key Points", "Deadlines", "Requirements", "Next Steps"]:
        md.append(f"## {section}")
        val = brief.get(section, "")
        if isinstance(val, list):
            if not val: md.append("_None found._")
            for v in val:
                md.append(f"- {v}")
        else:
            md.append(val if val else "_None found._")
        md.append("")
    md.append(f"---\n{BRAND_NOTE}")
    return "\n".join(md)

# ---------- UI
st.set_page_config(page_title=APP_TITLE, page_icon="📝", layout="centered")
st.title(APP_TITLE)
st.caption(BRAND_NOTE)

st.write("Upload a school/work **PDF** or **TXT**. You’ll get a 1-page brief with the main points, deadlines, requirements, and clear next steps.")

uploaded = st.file_uploader("Upload document", type=["pdf", "txt"])
with_styling = st.toggle("Use condensed layout", value=True)

if uploaded:
    if uploaded.type == "application/pdf" or uploaded.name.lower().endswith(".pdf"):
        raw = uploaded.read()
        text = extract_text_from_pdf(raw)
    else:
        text = uploaded.read().decode("utf-8", errors="ignore")
    text = clean_text(text)

    if not text or len(text) < 400:
        st.warning("The document looks very short. You can still generate a brief, but results may be minimal.")
    if st.button("Generate 1-Page Brief"):
        with st.spinner("Summarizing…"):
            brief = make_brief(text)
            md = brief_to_markdown(brief, uploaded.name)

        if with_styling:
            st.markdown(
                """
                <style>
                .brief-box {border:1px solid #eee;border-radius:12px;padding:18px;background:#fafbff;}
                .brief-box h2 {margin-top:1.2rem;}
                </style>
                """,
                unsafe_allow_html=True
            )
            st.markdown('<div class="brief-box">', unsafe_allow_html=True)
            st.markdown(md)
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.markdown(md)

        st.download_button(
            "⬇️ Download Brief (Markdown)",
            data=md.encode("utf-8"),
            file_name=f"{uploaded.name.rsplit('.',1)[0]}_brief.md",
            mime="text/markdown"
        )

        # Mini “receipt” for your Skills-First system
        receipt = f"""Receipt: Family-Receipt-AI-Summarization
Source: {uploaded.name}
Skills: Summarization (LexRank), Heuristic extraction (deadlines/requirements), Stakeholder translation
Timestamp: {datetime.now().isoformat()}
"""
        st.download_button(
            "🧾 Download Skills-First Receipt",
            data=receipt.encode("utf-8"),
            file_name=f"{uploaded.name.rsplit('.',1)[0]}_receipt.txt",
            mime="text/plain"
        )

else:
    st.info("Tip: Try with a school newsletter, IEP update, district announcement, or work RFP to see how it condenses.")
streamlit==1.38.0
pypdf==5.0.1
sumy==0.11.0
python-dateutil==2.9.0.post0
nltk==3.9.1
