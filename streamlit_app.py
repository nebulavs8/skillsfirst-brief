import re
import io
from datetime import datetime
from dateutil import parser as dateparser

import streamlit as st
from pypdf import PdfReader

APP_TITLE = "SchoolDoc AI — 1-Page Action Brief"
BRAND_NOTE = "Skills-First Blueprint • Families • Teachers • Orgs"

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

# ------------------ Simple extractive summarizer ------------------
STOPWORDS = set("""
a about above after again against all am an and any are aren't as at be because been before being below between
both but by can't cannot could couldn't did didn't do does doesn't doing don't down during each few for from further
had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his how how's i
i'd i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't my myself no nor not of off on
once only or other ought our ours ourselves out over own same shan't she she'd she'll she's should shouldn't so some
such than that that's the their theirs them themselves then there there's these they they'd they'll they're they've this those
through to too under until up very was wasn't we we'd we'll we're we've were weren't what what's when when's where where's
which while who who's whom why why's with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves
""".split())

def sentence_split(text: str):
    # Crude but effective sentence splitter
    # Split on . ! ? followed by whitespace/newline; keep abbreviations together reasonably well
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    # Clean
    sentences = [s.strip() for s in sentences if s.strip()]
    return sentences

def word_tokens(text: str):
    return [w.lower() for w in re.findall(r"[A-Za-z']+", text)]

def summarize_text(text: str, max_sentences: int = 5) -> str:
    """
    Frequency-based extractive summary:
    - Split into sentences
    - Build word frequencies (ignore stopwords)
    - Score sentences by normalized word freq sum, penalize very short/very long
    - Return top-N sentences in original order
    """
    sents = sentence_split(text)
    if not sents:
        return ""

    words = word_tokens(text)
    if not words:
        return " "

    # term frequencies
    freq = {}
    for w in words:
        if w in STOPWORDS or len(w) <= 2:
            continue
        freq[w] = freq.get(w, 0) + 1

    if not freq:
        # fallback: just take first N sentences
        return " ".join(sents[:max_sentences])

    max_f = max(freq.values())
    for w in list(freq.keys()):
        freq[w] = freq[w] / max_f

    # sentence scores
    scored = []
    for idx, s in enumerate(sents):
        tokens = word_tokens(s)
        if not tokens:
            continue
        # base score: sum of token frequencies
        score = sum(freq.get(t, 0) for t in tokens)
        # length regularization (prefer ~12-35 words)
        length = max(1, len(tokens))
        ideal = 24
        length_penalty = 1.0 - min(0.6, abs(length - ideal) / float(ideal + 1))
        score *= (0.4 + 0.6 * length_penalty)
        scored.append((idx, s, score))

    # pick top sentences by score
    top = sorted(scored, key=lambda x: x[2], reverse=True)[:max_sentences]
    top_sorted = [t[1] for t in sorted(top, key=lambda x: x[0])]
    return " ".join(top_sorted).strip()

# ------------------ Info extraction ------------------
def find_deadlines(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    hit_lines = [l for l in lines if re.search(r"\b(deadline|due|submit by|no later than)\b", l, re.I)]
    date_hits = re.findall(
        r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b",
        text,
    )
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
    req_lines = []
    for l in text.splitlines():
        l_clean = l.strip()
        if not l_clean:
            continue
        if re.match(r"^(\*|-|•|\d+\.)\s+", l_clean) or len(l_clean) < 220:
            if re.search(r"\b(must|required?|need to|provide|eligib|documentation|proof|return)\b", l_clean, re.I):
                req_lines.append(re.sub(r"^(\*|-|•|\d+\.)\s*", "", l_clean))
    seen = set()
    out = []
    for x in req_lines:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out[:10]

def find_key_points(text: str, top_n: int = 6):
    candidates = []
    for l in text.splitlines():
        l = l.strip(" -•*\t")
        if 40 <= len(l) <= 220 and not l.endswith(":"):
            candidates.append(l)
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
        "Email teacher/admin with any clarifying questions (limit to 3 bullets).",
        "Confirm submission method (portal, email, or printed copy) and save the confirmation.",
        "Schedule a 15-minute review with your child/team to align on what success looks like."
    ])
    seen = set()
    final = []
    for s in steps:
        if s not in seen:
            seen.add(s)
            final.append(s)
    return final[:5]

def make_brief(text: str):
    exec_summary = summarize_text(text, max_sentences=5)
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
            if not val:
                md.append("_None found._")
            else:
                for v in val:
                    md.append(f"- {v}")
        else:
            md.append(val if val else "_None found._")
        md.append("")
    md.append(f"---\n{BRAND_NOTE}")
    return "\n".join(md)

# ------------------ UI ------------------
st.set_page_config(page_title=APP_TITLE, page_icon="📝", layout="centered")
st.title(APP_TITLE)
st.caption(BRAND_NOTE)
st.write("Upload a school/work **PDF** or **TXT**. You’ll get a 1-page brief with the main points, deadlines, requirements, and clear next steps.")

uploaded = st.file_uploader("Upload document", type=["pdf", "txt"])
with_styling = st.toggle("Use condensed layout", value=True)

if uploaded:
    # Read file
    if uploaded.type == "application/pdf" or uploaded.name.lower().endswith(".pdf"):
        raw = uploaded.read()
        text = extract_text_from_pdf(raw)
    else:
        text = uploaded.read().decode("utf-8", errors="ignore")
    text = clean_text(text)

    # Friendly warning for scanned/short docs
    if not text or len(text.strip()) < 200:
        st.warning("This file looks very short or may be a scanned PDF with no selectable text. Try a text-based PDF or TXT file for best results.")

    if st.button("Generate 1-Page Brief"):
        with st.spinner("Summarizing…"):
            brief = make_brief(text)
            md = brief_to_markdown(brief, uploaded.name)

        # Styled render
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

        # Downloads
        st.download_button(
            "⬇️ Download Brief (Markdown)",
            data=md.encode("utf-8"),
            file_name=f"{uploaded.name.rsplit('.',1)[0]}_brief.md",
            mime="text/markdown"
        )

        receipt = f"""Receipt: Family-Receipt-AI-Summarization
Source: {uploaded.name}
Skills: Extractive summarization (freq-based), Heuristic extraction (deadlines/requirements), Stakeholder translation
Timestamp: {datetime.now().isoformat()}
"""
        st.download_button(
            "🧾 Download Skills-First Receipt",
            data=receipt.encode("utf-8"),
            file_name=f"{uploaded.name.rsplit('.',1)[0]}_receipt.txt",
            mime="text/plain"
        )
else:
    st.info("Tip: Try a school newsletter, IEP update, district announcement, or work RFP to see how it condenses.")

