from __future__ import annotations

import argparse
import http.client
import json
import re
import ssl
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "data" / "catalog.json"
SOURCE_LINKS_PATH = BASE_DIR / "data" / "source_links.json"

OAI_NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/"}
USER_AGENT = "Mozilla/5.0 (compatible; KurdishJournalSearch/1.0; OAI metadata harvester)"


def slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return clean or "record"


def fetch_xml(url: str) -> ET.Element:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(parsed.netloc, timeout=40, context=ssl.create_default_context())
    else:
        connection = http.client.HTTPConnection(parsed.netloc, timeout=40)
    try:
        connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,*/*"})
        response = connection.getresponse()
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status} for {url}")
        data = response.read()
    finally:
        connection.close()
    return ET.fromstring(data)


def oai_candidates(source_url: str) -> list[str]:
    parsed = urllib.parse.urlparse(source_url)
    if not parsed.scheme or not parsed.netloc:
        return []

    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    candidates: list[str] = []

    match = re.search(r"(/index\.php/[^/]+)", path)
    if match:
        candidates.append(base + match.group(1) + "/oai")

    if path:
        candidates.append(base + path + "/oai")

    candidates.extend(
        [
            base + "/index.php/index/oai",
            base + "/index.php/oai",
            base + "/oai",
        ]
    )

    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def find_working_oai_endpoint(source_url: str) -> str | None:
    for endpoint in oai_candidates(source_url):
        url = endpoint + "?verb=Identify"
        try:
            root = fetch_xml(url)
            if root.find(".//oai:repositoryName", OAI_NS) is not None:
                return endpoint
        except Exception:
            continue
    return None


def text_values(record: ET.Element, name: str) -> list[str]:
    return [
        " ".join((item.text or "").split())
        for item in record.findall(f".//dc:{name}", OAI_NS)
        if (item.text or "").strip()
    ]


def record_to_article(record: ET.Element, source: dict[str, Any], journal_id: str) -> dict[str, Any] | None:
    title_values = text_values(record, "title")
    if not title_values:
        return None

    identifiers = text_values(record, "identifier")
    pdf_url = next((value for value in identifiers if value.lower().endswith(".pdf")), "")
    url = next((value for value in identifiers if value.startswith("http") and value != pdf_url), "")
    dates = text_values(record, "date")
    year_match = re.search(r"\d{4}", dates[0] if dates else "")

    title = title_values[0]
    article_id = "ojs-" + slug(source["id"]) + "-" + slug(title)[:80]
    subjects = text_values(record, "subject")
    description = " ".join(text_values(record, "description"))

    return {
        "id": article_id,
        "title": title,
        "title_ku": "",
        "title_ar": "",
        "authors": text_values(record, "creator") or ["Unknown"],
        "year": int(year_match.group(0)) if year_match else "",
        "journal_id": journal_id,
        "doi": next((value for value in identifiers if "doi.org/" in value.lower()), ""),
        "pdf_url": pdf_url,
        "url": url or source.get("url", ""),
        "keywords": subjects[:12] + [source.get("institution", ""), source.get("title", "")],
        "abstract": description or f"Metadata imported from {source.get('title', 'journal source')}.",
        "summary": description[:700] if description else f"Imported metadata record from {source.get('title', 'journal source')}.",
        "language": (text_values(record, "language") or ["Unknown"])[0],
    }


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def institution_id_for_source(catalog: dict[str, Any], source: dict[str, Any]) -> str:
    title_key = normalize_name(source.get("title", ""))
    for journal in catalog["journals"]:
        if not journal["id"].startswith("ojs-") and normalize_name(journal["title"]) == title_key:
            return journal["institution_id"]

    institution_key = normalize_name(source.get("institution", ""))
    for institution in catalog["institutions"]:
        if normalize_name(institution["name_en"]) == institution_key:
            return institution["id"]

    aliases = {
        "universityofkurdistanhewler": "ukh",
        "sulaimanipolytechnicuniversity": "sulaimani-polytechnic",
        "erbilpolytechnicuniversity": "erbil-polytechnic",
        "duhokpolytechnicuniversity": "duhok-polytechnic",
        "cihanuniversitysulaimaniya": "cihan-sulaimaniya",
        "cihanuniversityerbil": "cihan-erbil",
        "kurdistanregionuniversity": "sulaimani",
        "kurdistanregionresearchcommunity": "sulaimani",
        "mesopotamianacademicpress": "sulaimani",
        "universityofcharmo": "charmo",
    }
    return aliases.get(institution_key, "sulaimani")


def ensure_journal(catalog: dict[str, Any], source: dict[str, Any], journal_id: str) -> None:
    if any(journal["id"] == journal_id for journal in catalog["journals"]):
        return
    institution_id = institution_id_for_source(catalog, source)
    catalog["journals"].append(
        {
            "id": journal_id,
            "title": source["title"],
            "institution_id": institution_id,
            "subjects": source.get("subjects", ["imported"]),
            "issn": "",
            "impact_factor": None,
            "ranking": "Not verified",
            "indexing": source.get("indexing", []),
            "scopus_source_id": source.get("scopus_source_id", ""),
            "indexing_url": source.get("indexing_url", ""),
        }
    )


def harvest_endpoint(endpoint: str, source: dict[str, Any], journal_id: str, max_records: int) -> list[dict[str, Any]]:
    imported: list[dict[str, Any]] = []
    token = ""

    while True:
        if token:
            query = urllib.parse.urlencode({"verb": "ListRecords", "resumptionToken": token})
        else:
            query = urllib.parse.urlencode({"verb": "ListRecords", "metadataPrefix": "oai_dc"})
        root = fetch_xml(endpoint + "?" + query)

        for record in root.findall(".//oai:record", OAI_NS):
            article = record_to_article(record, source, journal_id)
            if article:
                imported.append(article)
                if len(imported) >= max_records:
                    return imported

        token_node = root.find(".//oai:resumptionToken", OAI_NS)
        token = (token_node.text or "").strip() if token_node is not None else ""
        if not token:
            return imported
        time.sleep(0.4)


def harvest_all(max_records_per_source: int, dry_run: bool) -> tuple[int, list[str]]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCE_LINKS_PATH.read_text(encoding="utf-8"))
    existing_ids = {article["id"] for article in catalog["articles"]}
    notes: list[str] = []
    imported_count = 0

    for source in sources:
        if not source.get("url"):
            continue
        endpoint = find_working_oai_endpoint(source["url"])
        if not endpoint:
            notes.append(f"No OAI endpoint found: {source['title']}")
            continue

        journal_id = "ojs-" + slug(source["id"])
        ensure_journal(catalog, source, journal_id)
        try:
            articles = harvest_endpoint(endpoint, source, journal_id, max_records_per_source)
        except (OSError, ET.ParseError, TimeoutError, RuntimeError) as exc:
            notes.append(f"Failed {source['title']}: {exc}")
            continue

        new_articles = [article for article in articles if article["id"] not in existing_ids]
        for article in new_articles:
            catalog["articles"].append(article)
            existing_ids.add(article["id"])
        imported_count += len(new_articles)
        notes.append(f"{source['title']}: {len(new_articles)} imported from {endpoint}")

    if not dry_run:
        CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return imported_count, notes


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest article metadata from OJS/OAI-PMH journal sources.")
    parser.add_argument("--max-records-per-source", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    count, notes = harvest_all(args.max_records_per_source, args.dry_run)
    print(("Would import" if args.dry_run else "Imported") + f" {count} article record(s).")
    for note in notes:
        print("- " + note)


if __name__ == "__main__":
    main()
