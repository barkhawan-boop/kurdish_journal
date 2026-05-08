from __future__ import annotations

import argparse
import html
import json
import os
import re
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse


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


def enrich_article(article: dict[str, Any], score: int = 0, reasons: list[str] | None = None) -> dict[str, Any]:
    journal = JOURNALS.get(article["journal_id"], {})
    institution = INSTITUTIONS.get(journal.get("institution_id", ""), {})
    result = dict(article)
    result["score"] = score
    result["reasons"] = reasons or []
    result["journal"] = journal
    result["institution"] = institution
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


def external_search_cards(query: str) -> list[dict[str, Any]]:
    if not query.strip():
        return []

    encoded = quote_plus(query.strip())
    return [
        {
            "kind": "source",
            "id": "live-iasj-search",
            "title": f'Search IASJ for "{query.strip()}"',
            "url": "https://iasj.rdd.edu.iq/journals/",
            "institution": "Iraqi Academic Scientific Journals",
            "subjects": ["live search", "iasj", "articles"],
            "summary": "IASJ has its own large article index. Open this source and run the keyword there until records are imported into this app.",
            "score": 999,
            "reasons": ["live external search source"],
        },
        {
            "kind": "source",
            "id": "google-iasj-site-search",
            "title": f'Google search inside IASJ for "{query.strip()}"',
            "url": f"https://www.google.com/search?q=site%3Aiasj.rdd.edu.iq%2Fjournals+{encoded}",
            "institution": "Google site search",
            "subjects": ["live search", "iasj", "articles"],
            "summary": "Search indexed IASJ pages through Google when direct IASJ article data is not yet imported locally.",
            "score": 998,
            "reasons": ["external site search"],
        },
    ]


def search_all(query: str, institution_type: str = "all", subject: str = "all") -> list[dict[str, Any]]:
    article_results = search_articles(query, institution_type, subject)
    for article in article_results:
        article["kind"] = "article"
    source_results = search_sources(query, subject)
    return sorted(
        external_search_cards(query) + article_results + source_results,
        key=lambda item: item.get("score", 0),
        reverse=True,
    )


def paraphrase_text(text: str, tone: str = "academic") -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""

    replacements = {
        "academic": [
            ("This study", "This research"),
            ("shows", "indicates"),
            ("reviews", "examines"),
            ("focuses on", "concentrates on"),
            ("covers", "addresses"),
            ("important", "significant"),
            ("uses", "employs"),
        ],
        "simple": [
            ("This research", "This study"),
            ("indicates", "shows"),
            ("examines", "looks at"),
            ("concentrates on", "focuses on"),
            ("addresses", "covers"),
            ("significant", "important"),
            ("employs", "uses"),
        ],
    }
    output = cleaned
    for source, target in replacements.get(tone, replacements["academic"]):
        output = output.replace(source, target)
    return output


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
