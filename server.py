from __future__ import annotations

import argparse
import html
import io
import json
import os
import re
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from email.parser import BytesParser
from email.policy import default


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DATA_PATH = BASE_DIR / "data" / "catalog.json"
SOURCE_LINKS_PATH = BASE_DIR / "data" / "source_links.json"

SCRIPT_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+|[A-Za-z0-9]+")
STOP_WORDS = {
    "the",
    "and",
    "or",
    "of",
    "in",
    "for",
    "to",
    "a",
    "an",
    "with",
    "from",
    "لە",
    "و",
    "بۆ",
    "فی",
    "في",
    "من",
    "إلى",
    "على",
    "ئەم",
    "هذا",
    "هذه",
}

EXPANSIONS = {
    "kurdish": {"کوردی", "كردي", "badini", "sorani", "بادینی", "سۆرانی"},
    "badini": {"kurmanji", "کرمانجی", "بادینی", "duhok", "دهۆک", "دهوك"},
    "sorani": {"سۆرانی", "سلێمانی", "هەولێر", "kurdish"},
    "water": {"ئاو", "مياه", "climate", "environment", "ژینگە"},
    "education": {"خوێندن", "تعليم", "learning", "university", "زانکۆ"},
    "medical": {"medicine", "health", "پزیشکی", "صحة", "nursing"},
    "engineering": {"technology", "polytechnic", "ئەندازیاری", "هندسة"},
    "electron": {"electronic", "electronics", "electrical", "engineering", "technology", "polytechnic"},
    "electronic": {"electron", "electronics", "electrical", "engineering", "technology", "polytechnic"},
    "electronics": {"electron", "electronic", "electrical", "engineering", "technology", "polytechnic"},
    "electrical": {"electron", "electronic", "electronics", "engineering", "technology", "polytechnic"},
    "digital": {"e-learning", "online", "رقمنة", "دیجیتاڵ"},
    "computer": {"computing", "computer science", "software", "programming", "it", "ai", "artificial intelligence", "کۆمپیوتەر", "حاسوب"},
    "computers": {"computer", "computing", "software", "programming", "it"},
    "ai": {"artificial intelligence", "machine learning", "computer", "software"},
    "law": {"legal", "human rights", "یاسا", "قانون"},
    "agriculture": {"farming", "soil", "crop", "کشتوکاڵ", "زراعة"},
    "erbil": {"hawler", "هەولێر", "أربيل"},
    "sulaimani": {"slemani", "سلێمانی", "السليمانية"},
    "duhok": {"دهۆک", "دهوك", "badini"},
}


def fuzzy_token_matches(query_terms: set[str], indexed_tokens: set[str]) -> set[str]:
    matches: set[str] = set()
    for term in query_terms:
        if len(term) < 4:
            continue
        for token in indexed_tokens:
            if len(token) < 4:
                continue
            if token.startswith(term) or term.startswith(token):
                matches.add(term)
                break
    return matches


@dataclass(frozen=True)
class SearchHit:
    score: int
    article: dict[str, Any]
    reasons: list[str]


@dataclass(frozen=True)
class SourceHit:
    score: int
    source: dict[str, Any]
    reasons: list[str]


def load_catalog() -> dict[str, Any]:
    with DATA_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_source_links() -> list[dict[str, Any]]:
    with SOURCE_LINKS_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


CATALOG = load_catalog()
SOURCE_LINKS = load_source_links()
INSTITUTIONS = {item["id"]: item for item in CATALOG["institutions"]}
JOURNALS = {item["id"]: item for item in CATALOG["journals"]}


def tokens(text: str) -> list[str]:
    raw_tokens = [match.group(0).lower() for match in SCRIPT_RE.finditer(text)]
    return [token for token in raw_tokens if token not in STOP_WORDS and len(token) > 1]


def expanded_query_terms(query: str) -> set[str]:
    terms = set(tokens(query))
    for term in list(terms):
        terms.update(value.lower() for value in EXPANSIONS.get(term, set()))
    return terms


def searchable_text(article: dict[str, Any], journal: dict[str, Any], institution: dict[str, Any]) -> str:
    parts: list[str] = [
        article.get("title", ""),
        article.get("title_ku", ""),
        article.get("title_ar", ""),
        article.get("abstract", ""),
        article.get("summary", ""),
        article.get("language", ""),
        journal.get("title", ""),
        journal.get("issn", ""),
        institution.get("name_en", ""),
        institution.get("name_ku", ""),
        institution.get("name_ar", ""),
        institution.get("city", ""),
        " ".join(article.get("keywords", [])),
        " ".join(journal.get("subjects", [])),
        " ".join(article.get("authors", [])),
    ]
    return " ".join(parts).lower()


def searchable_source_text(source: dict[str, Any]) -> str:
    parts = [
        source.get("title", ""),
        source.get("url", ""),
        source.get("institution", ""),
        source.get("summary", ""),
        " ".join(source.get("subjects", [])),
    ]
    return " ".join(parts).lower()


def citation(article: dict[str, Any], journal: dict[str, Any], style: str = "apa") -> str:
    authors = article.get("authors") or ["Unknown author"]
    if len(authors) == 1:
        author_text = authors[0]
    elif len(authors) == 2:
        author_text = f"{authors[0]} & {authors[1]}"
    else:
        author_text = f"{authors[0]} et al."

    year = article.get("year", "n.d.")
    title = article.get("title", "Untitled")
    journal_title = journal.get("title", "Unknown journal")
    doi = article.get("doi") or ""
    link = article.get("url") or article.get("pdf_url") or ""

    if style == "mla":
        tail = f" {doi or link}" if doi or link else ""
        return f'{author_text}. "{title}." {journal_title}, {year}.{tail}'
    if style == "chicago":
        tail = f" {doi or link}." if doi or link else ""
        return f'{author_text}. "{title}." {journal_title} ({year}).{tail}'
    if style == "bibtex":
        key = re.sub(r"[^A-Za-z0-9]+", "", f"{authors[0]}{year}") or "record"
        return (
            f"@article{{{key},\n"
            f"  author = {{{' and '.join(authors)}}},\n"
            f"  title = {{{title}}},\n"
            f"  journal = {{{journal_title}}},\n"
            f"  year = {{{year}}},\n"
            f"  doi = {{{doi}}},\n"
            f"  url = {{{link}}}\n"
            f"}}"
        )
    tail = f" https://doi.org/{doi}" if doi else (f" {link}" if link else "")
    return f"{author_text}. ({year}). {title}. {journal_title}.{tail}"


def pdf_search_url(article: dict[str, Any], journal: dict[str, Any]) -> str:
    title = article.get("title", "")
    journal_title = journal.get("title", "")
    doi = article.get("doi", "")
    query = " ".join(part for part in [title, journal_title, doi, "PDF"] if part)
    return f"https://www.google.com/search?q={quote_plus(query)}"


def article_summary(article: dict[str, Any], journal: dict[str, Any], institution: dict[str, Any]) -> str:
    summary = " ".join((article.get("summary") or "").split())
    abstract = " ".join((article.get("abstract") or "").split())
    if len(summary) >= 140:
        return summary

    title = article.get("title", "This record")
    year = article.get("year") or "n.d."
    keywords = ", ".join(article.get("keywords", [])[:5])
    parts = [
        summary or abstract,
        f"It is indexed under {journal.get('title', 'an academic journal')} at {institution.get('name_en', 'a Kurdistan Region institution')}.",
        f"The record is useful for searches around {keywords}." if keywords else "",
        f"Publication year: {year}. Verify the full article metadata and PDF from the journal source before formal citation.",
    ]
    return " ".join(part for part in parts if part)


def enrich_article(article: dict[str, Any], score: int = 0, reasons: list[str] | None = None) -> dict[str, Any]:
    journal = JOURNALS.get(article["journal_id"], {})
    institution = INSTITUTIONS.get(journal.get("institution_id", ""), {})
    result = dict(article)
    result["score"] = score
    result["reasons"] = reasons or []
    result["journal"] = journal
    result["institution"] = institution
    result["display_summary"] = article_summary(article, journal, institution)
    result["citations"] = {
        "apa": citation(article, journal, "apa"),
        "mla": citation(article, journal, "mla"),
        "chicago": citation(article, journal, "chicago"),
        "bibtex": citation(article, journal, "bibtex"),
    }
    result["pdf_search_url"] = pdf_search_url(article, journal)
    return result


def search_articles(query: str, institution_type: str = "all", subject: str = "all") -> list[dict[str, Any]]:
    query_terms = expanded_query_terms(query)
    hits: list[SearchHit] = []

    for article in CATALOG["articles"]:
        journal = JOURNALS[article["journal_id"]]
        institution = INSTITUTIONS[journal["institution_id"]]
        if institution_type != "all" and institution["type"] != institution_type:
            continue
        if subject != "all" and subject not in journal.get("subjects", []):
            continue

        text = searchable_text(article, journal, institution)
        article_tokens = set(tokens(text))
        score = 0
        reasons: list[str] = []

        if not query_terms:
            score = 1
            reasons.append("recent seeded record")
        else:
            overlap = query_terms & article_tokens
            if overlap:
                score += len(overlap) * 10
                reasons.append("keyword match: " + ", ".join(sorted(overlap)[:5]))
            fuzzy_matches = fuzzy_token_matches(query_terms, article_tokens)
            if fuzzy_matches:
                score += len(fuzzy_matches) * 8
                reasons.append("related word match: " + ", ".join(sorted(fuzzy_matches)[:5]))
            title_text = f"{article.get('title', '')} {article.get('title_ku', '')} {article.get('title_ar', '')}".lower()
            title_matches = [term for term in query_terms if term in title_text]
            if title_matches:
                score += len(title_matches) * 15
                reasons.append("title match")
            keyword_text = " ".join(article.get("keywords", [])).lower()
            keyword_matches = [term for term in query_terms if term in keyword_text]
            if keyword_matches:
                score += len(keyword_matches) * 12
                reasons.append("indexed keyword match")
            loose_matches = [term for term in query_terms if term in text and term not in overlap]
            if loose_matches:
                score += len(loose_matches) * 5
                reasons.append("partial text match")

        if score > 0:
            hits.append(SearchHit(score=score, article=article, reasons=reasons))

    hits.sort(key=lambda hit: (hit.score, hit.article.get("year", 0)), reverse=True)
    return [enrich_article(hit.article, hit.score, hit.reasons) for hit in hits]


def enrich_source(source: dict[str, Any], score: int = 0, reasons: list[str] | None = None) -> dict[str, Any]:
    result = dict(source)
    result["kind"] = "source"
    result["score"] = score
    result["reasons"] = reasons or []
    if len(result.get("summary", "")) < 120:
        subjects = ", ".join(result.get("subjects", [])[:5])
        result["summary"] = (
            f"{result.get('summary', 'Northern/Kurdistan journal source.')} "
            f"This source belongs to {result.get('institution', 'a Kurdistan Region institution')} "
            f"and is relevant for {subjects or 'academic journal'} searches. "
            "Use the official source link when available to verify issues, article metadata, and PDFs."
        )
    return result


def search_sources(query: str, subject: str = "all") -> list[dict[str, Any]]:
    query_terms = expanded_query_terms(query)
    hits: list[SourceHit] = []

    for source in SOURCE_LINKS:
        if subject != "all" and subject not in source.get("subjects", []):
            continue

        text = searchable_source_text(source)
        source_tokens = set(tokens(text))
        score = 0
        reasons: list[str] = []

        if not query_terms:
            score = 1
            reasons.append("source directory")
        else:
            overlap = query_terms & source_tokens
            if overlap:
                score += len(overlap) * 10
                reasons.append("source keyword match: " + ", ".join(sorted(overlap)[:5]))
            fuzzy_matches = fuzzy_token_matches(query_terms, source_tokens)
            if fuzzy_matches:
                score += len(fuzzy_matches) * 8
                reasons.append("source related word match: " + ", ".join(sorted(fuzzy_matches)[:5]))
            title_text = source.get("title", "").lower()
            title_matches = [term for term in query_terms if term in title_text]
            if title_matches:
                score += len(title_matches) * 15
                reasons.append("source title match")
            loose_matches = [term for term in query_terms if term in text and term not in overlap]
            if loose_matches:
                score += len(loose_matches) * 5
                reasons.append("source partial match")

        if score > 0:
            hits.append(SourceHit(score=score, source=source, reasons=reasons))

    hits.sort(key=lambda hit: (hit.score, hit.source.get("title", "")), reverse=True)
    return [enrich_source(hit.source, hit.score, hit.reasons) for hit in hits]


def search_all(query: str, institution_type: str = "all", subject: str = "all") -> list[dict[str, Any]]:
    article_results = search_articles(query, institution_type, subject)
    for article in article_results:
        article["kind"] = "article"
    source_results = search_sources(query, subject)
    return sorted(
        article_results + source_results,
        key=lambda item: item.get("score", 0),
        reverse=True,
    )


def paraphrase_text(text: str, tone: str = "academic") -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return "Paste a paragraph first, then click Paraphrase."

    academic_replacements = [
        (r"\bdemo\b", "sample"),
        (r"\brecord\b", "entry"),
        (r"\btesting\b", "evaluating"),
        (r"\btechnology-related\b", "technology-focused"),
        (r"\bsearches\b", "queries"),
        (r"\bstudy\b", "research"),
        (r"\bpaper\b", "article"),
        (r"\bshows\b", "demonstrates"),
        (r"\bfound\b", "identified"),
        (r"\bfinds\b", "identifies"),
        (r"\breviews\b", "critically examines"),
        (r"\bfocuses on\b", "centres on"),
        (r"\bcovers\b", "addresses"),
        (r"\bimportant\b", "significant"),
        (r"\bbig\b", "substantial"),
        (r"\bsmall\b", "limited"),
        (r"\buses\b", "employs"),
        (r"\bhelps\b", "contributes to"),
        (r"\babout\b", "concerning"),
        (r"\bbecause\b", "because of the fact that"),
        (r"\bpeople\b", "individuals"),
        (r"\bstudents\b", "learners"),
        (r"\bteachers\b", "educators"),
        (r"\bresults\b", "findings"),
        (r"\bproblem\b", "issue"),
        (r"\bproblems\b", "issues"),
    ]
    simple_replacements = [
        (r"\bdemonstrates\b", "shows"),
        (r"\bidentified\b", "found"),
        (r"\bcritically examines\b", "looks at"),
        (r"\bcentres on\b", "focuses on"),
        (r"\baddresses\b", "covers"),
        (r"\bsignificant\b", "important"),
        (r"\bsubstantial\b", "large"),
        (r"\bemploys\b", "uses"),
        (r"\bindividuals\b", "people"),
        (r"\blearners\b", "students"),
        (r"\beducators\b", "teachers"),
        (r"\bfindings\b", "results"),
    ]

    replacements = simple_replacements if tone == "simple" else academic_replacements
    sentences = sentence_split(cleaned) or [cleaned]
    paraphrased: list[str] = []

    for sentence in sentences:
        output = sentence
        for pattern, target in replacements:
            output = re.sub(pattern, target, output, flags=re.IGNORECASE)

        if tone == "academic":
            output = re.sub(
                r"^This research (demonstrates|identifies|addresses|examines)",
                r"The present research \1",
                output,
                flags=re.IGNORECASE,
            )
            output = re.sub(
                r"^This article (demonstrates|identifies|addresses|examines)",
                r"This article \1",
                output,
                flags=re.IGNORECASE,
            )
        paraphrased.append(output)

    final = " ".join(paraphrased)
    if tone == "academic":
        if final == cleaned:
            final = f"From an academic perspective, {cleaned[0].lower() + cleaned[1:] if len(cleaned) > 1 else cleaned.lower()}"
        if not re.search(r"\b(research|article|findings|analysis|evidence|study)\b", final, re.IGNORECASE):
            final = f"This academic revision indicates that {final[0].lower() + final[1:] if len(final) > 1 else final.lower()}"
    else:
        final = re.sub(r"\bbecause of the fact that\b", "because", final, flags=re.IGNORECASE)

    return final


def sentence_split(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?؟])\s+", compact) if len(sentence.strip()) > 30]


def summarize_text(text: str, keyword: str = "") -> str:
    sentences = sentence_split(text)
    if not sentences:
        return "No readable academic text was found in this PDF. If the PDF is scanned images, OCR is required before summarising."

    query_terms = set(tokens(keyword))
    scored: list[tuple[int, int, str]] = []
    for index, sentence in enumerate(sentences[:250]):
        sentence_terms = set(tokens(sentence))
        score = len(query_terms & sentence_terms) * 5 if query_terms else 0
        score += 2 if any(term in sentence.lower() for term in ["result", "method", "study", "research", "conclusion", "aim", "objective"]) else 0
        score += max(0, 4 - index // 8)
        scored.append((score, index, sentence))

    selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:7]
    selected = sorted(selected, key=lambda item: item[1])
    body = " ".join(sentence for _, _, sentence in selected)

    lead = "PDF summary: "
    if keyword.strip():
        lead = f'PDF summary focused on "{keyword.strip()}": '
    return lead + body


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF extraction requires pypdf. Run: pip install -r requirements.txt") from exc

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[str] = []
    for page in reader.pages[:40]:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def parse_multipart_form(body: bytes, content_type: str) -> tuple[dict[str, str], dict[str, bytes]]:
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    fields: dict[str, str] = {}
    files: dict[str, bytes] = {}
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        if "form-data" not in disposition:
            continue
        name = part.get_param("name", header="Content-Disposition")
        filename = part.get_param("filename", header="Content-Disposition")
        payload = part.get_payload(decode=True) or b""
        if not name:
            continue
        if filename:
            files[name] = payload
        else:
            fields[name] = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return fields, files


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/catalog":
            self.send_json(
                {
                    "metadata": CATALOG["metadata"],
                    "institutions": CATALOG["institutions"],
                    "journals": CATALOG["journals"],
                    "source_count": len(SOURCE_LINKS),
                    "article_count": len(CATALOG["articles"]),
                    "subjects": sorted(
                        {subject for journal in CATALOG["journals"] for subject in journal["subjects"]}
                        | {subject for source in SOURCE_LINKS for subject in source.get("subjects", [])}
                    ),
                }
            )
            return

        if parsed.path == "/api/search":
            params = parse_qs(parsed.query)
            query = unquote(params.get("q", [""])[0]).strip()
            institution_type = params.get("type", ["all"])[0]
            subject = params.get("subject", ["all"])[0]
            results = search_all(query, institution_type, subject)
            self.send_json({"query": query, "count": len(results), "results": results})
            return

        if parsed.path == "/api/health":
            self.send_json({"ok": True, "catalog_records": len(CATALOG["articles"]), "source_links": len(SOURCE_LINKS)})
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/summarize-pdf":
            length = int(self.headers.get("Content-Length", "0"))
            content_type = self.headers.get("Content-Type", "")
            fields, files = parse_multipart_form(self.rfile.read(length), content_type)
            keyword = fields.get("keyword", "")
            pdf_bytes = files.get("pdf", b"")
            if not pdf_bytes:
                self.send_json({"error": "No PDF uploaded."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                extracted = extract_pdf_text(pdf_bytes)
                self.send_json(
                    {
                        "characters": len(extracted),
                        "summary": summarize_text(extracted, keyword),
                        "extracted_preview": extracted[:1200],
                    }
                )
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path != "/api/paraphrase":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        text = html.unescape(str(payload.get("text", "")))
        tone = str(payload.get("tone", "academic"))
        self.send_json({"paraphrased": paraphrase_text(text, tone)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Northern Iraq Kurdish journals search app.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Research search app running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
