from __future__ import annotations

import argparse
import html
import io
import json
import re
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "data" / "catalog.json"
SOURCE_LINKS_PATH = BASE_DIR / "data" / "source_links.json"
USER_AGENT = "Mozilla/5.0 (compatible; KurdistanAcademicJournals/1.0; OCR full-text indexer)"
SCRIPT_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+|[A-Za-z0-9]+")


def slugless(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def journal_source_id(journal_id: str) -> str:
    return journal_id[4:] if journal_id.startswith("ojs-") else journal_id


def allowed_journal_ids(catalog: dict[str, Any], sources: list[dict[str, Any]]) -> set[str]:
    source_ids = {source["id"] for source in sources}
    allowed = {f"ojs-{source_id}" for source_id in source_ids} | source_ids
    source_titles = {slugless(source.get("title", "")) for source in sources}
    for journal in catalog["journals"]:
        if journal_source_id(journal.get("id", "")) in source_ids:
            allowed.add(journal["id"])
        elif slugless(journal.get("title", "")) in source_titles:
            allowed.add(journal["id"])
    return allowed


def token_count(text: str) -> int:
    return len(SCRIPT_RE.findall(text or ""))


def clean_text(text: str, max_chars: int) -> str:
    text = html.unescape(text or "").replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_chars]


def fetch_url_bytes(url: str) -> tuple[bytes, str, str]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/pdf,*/*",
        },
    )
    context = ssl.create_default_context()
    with urlopen(request, timeout=30, context=context) as response:
        return response.read(), response.headers.get("Content-Type", ""), response.geturl()


def discover_pdf_link(markup: str, base_url: str) -> str:
    links = re.findall(r"""href=["']([^"']+)["']""", markup, flags=re.I)
    for link in links:
        label_match = re.search(rf"""href=["']{re.escape(link)}["'][^>]*>(.*?)</a>""", markup, flags=re.I | re.S)
        label = re.sub(r"<[^>]+>", " ", label_match.group(1)).lower() if label_match else ""
        href = html.unescape(link)
        if ".pdf" in href.lower() or "download" in href.lower() or "viewFile" in href or "pdf" in label:
            return urljoin(base_url, href)
    return ""


def extract_pdf_text(pdf_bytes: bytes, max_pages: int) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[str] = []
    for page in reader.pages[:max_pages]:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages)


def ocr_pdf_text(pdf_bytes: bytes, max_pages: int, languages: str) -> str:
    from pdf2image import convert_from_bytes
    import pytesseract

    images = convert_from_bytes(pdf_bytes, first_page=1, last_page=max_pages, dpi=220)
    pages: list[str] = []
    for image in images:
        pages.append(pytesseract.image_to_string(image, lang=languages))
    return "\n\n".join(pages)


def pdf_bytes_for_article(article: dict[str, Any]) -> tuple[bytes, str]:
    for candidate in [article.get("pdf_url", ""), article.get("url", "")]:
        if not candidate:
            continue
        try:
            body, content_type, final_url = fetch_url_bytes(candidate)
            is_pdf = "application/pdf" in content_type.lower() or final_url.lower().split("?")[0].endswith(".pdf")
            if is_pdf:
                return body, final_url
            markup = body.decode("utf-8", errors="replace")
            pdf_url = discover_pdf_link(markup, final_url)
            if pdf_url:
                pdf_body, pdf_type, pdf_final_url = fetch_url_bytes(pdf_url)
                if "application/pdf" in pdf_type.lower() or pdf_final_url.lower().split("?")[0].endswith(".pdf"):
                    return pdf_body, pdf_final_url
        except Exception:
            continue
    return b"", ""


def index_article(article: dict[str, Any], max_pages: int, max_chars: int, min_tokens: int, languages: str) -> bool:
    pdf_bytes, pdf_url = pdf_bytes_for_article(article)
    if not pdf_bytes:
        return False

    extracted = clean_text(extract_pdf_text(pdf_bytes, max_pages), max_chars)
    source = "pdf text extraction"
    used_ocr = False

    if token_count(extracted) < min_tokens:
        try:
            extracted = clean_text(ocr_pdf_text(pdf_bytes, max_pages, languages), max_chars)
            source = "OCR full text"
            used_ocr = True
        except Exception as exc:
            article["full_text_error"] = f"OCR unavailable: {exc}"

    if token_count(extracted) < min_tokens:
        return False

    article["full_text"] = extracted
    article["full_text_source"] = source
    article["full_text_indexed_at"] = datetime.now(timezone.utc).isoformat()
    article["full_text_pdf_url"] = pdf_url
    article["ocr_used"] = used_ocr
    article.pop("full_text_error", None)
    return True


def index_catalog(args: argparse.Namespace) -> int:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCE_LINKS_PATH.read_text(encoding="utf-8"))
    allowed_ids = allowed_journal_ids(catalog, sources)

    changed = 0
    checked = 0
    for article in catalog["articles"]:
        if article.get("journal_id") not in allowed_ids:
            continue
        if article.get("full_text") and not args.force:
            continue
        if args.max_articles and checked >= args.max_articles:
            break
        checked += 1
        title = article.get("title", "Untitled")[:90]
        try:
            if index_article(article, args.max_pages, args.max_chars, args.min_tokens, args.languages):
                changed += 1
                print(f"Indexed: {title}")
            else:
                print(f"No readable full text: {title}")
        except Exception as exc:
            article["full_text_error"] = str(exc)
            print(f"Failed: {title}: {exc}")

    if changed and not args.dry_run:
        CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OCR-backed full-text search fields for indexed articles.")
    parser.add_argument("--max-articles", type=int, default=80)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--max-chars", type=int, default=20000)
    parser.add_argument("--min-tokens", type=int, default=80)
    parser.add_argument("--languages", default="eng+ara")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    changed = index_catalog(args)
    print(f"Full-text indexed {changed} article(s).")


if __name__ == "__main__":
    main()
