# Kurdistan Academic Journals

Python web app for a multilingual academic journal search platform focused on recognised Kurdistan Region universities, institutes, and journals.

The app includes a starter catalog of public/private universities and known journals from the project brief, plus demonstration article records so the search UI works immediately. Before public launch, replace demo records with verified metadata from journal exports, OJS feeds, DOI/Crossref records, or curated CSV files.

## Features

- English, Kurdish Sorani/Badini script support, and Arabic interface.
- Keyword search by topic, author, journal, university, city, abstract, and indexed keywords.
- Query expansion for common Kurdish/Arabic/English equivalents.
- Filters for public/private institutions and subject area.
- Clean article cards with summary, citation text, journal metrics, and PDF links when available.
- One-click copy for citations and summaries.
- Citation formats: APA, MLA, Chicago, and BibTeX.
- Journal fields for impact factor, ISSN, and ranking. Unknown values show as `Not verified` until imported from trusted sources.
- Simple paraphrasing helper endpoint for drafting support.
- CSV importer for adding real article metadata.
- No external Python packages required.

## Run Locally

From this folder:

```powershell
python server.py --port 8000
```

If you want to use the existing virtual environment from the parent folder:

```powershell
..\.venv\Scripts\python.exe server.py
```

Open:

```text
http://127.0.0.1:8000
```

## Import Articles

Create a UTF-8 CSV with these required columns:

```text
title,authors,year,journal_id,abstract
```

Optional columns:

```text
id,title_ku,title_ar,doi,pdf_url,url,keywords,summary,language
```

Authors and keywords can be separated by commas or semicolons.

Example:

```powershell
python import_articles.py articles.csv
```

## Harvest OJS Article Metadata

Most Kurdistan Region journal sites run OJS. Many OJS journals expose article metadata through OAI-PMH. Use the harvester to import real article records into `data/catalog.json`:

```powershell
python harvest_ojs.py --dry-run --max-records-per-source 50
```

If the dry run finds records, import them:

```powershell
python harvest_ojs.py --max-records-per-source 500
```

Then commit and redeploy:

```powershell
git add data/catalog.json
git commit -m "Import OJS article metadata"
git push
```

Some journals may block automated access or may not expose OAI-PMH. For those, use CSV exports or manual metadata files with `import_articles.py`.

## Data Model

Main data lives in `data/catalog.json`:

- `institutions`: recognised universities and institutes.
- `journals`: journal title, owning institution, subjects, ISSN, impact factor, and ranking.
- `source_links`: the curated northern/Kurdistan journal source list.
- `articles`: searchable article metadata, summaries, PDF links, DOI/URL, multilingual titles, authors, and keywords.

For impact factor and ranking, only add values that are verified from the journal site, recognised indexing databases, Scopus/SJR/Web of Science where applicable, or ministry/university documentation. Many regional journals may not have an official impact factor; keeping `Not verified` is better than publishing guessed metrics.

## Suggested GitHub Structure

```text
north_journals_app/
  data/catalog.json
  public/
    index.html
    styles.css
    app.js
  import_articles.py
  server.py
  README.md
```

## Future Upgrades

- Add scheduled harvesters for each journal OJS site.
- Add a review screen for approving new imported records before publication.
- Store article data in SQLite or PostgreSQL when the catalog grows.
- Add full-text PDF indexing with permission from journal publishers.
- Connect a proper AI paraphrasing/summarisation service with citation safeguards and plagiarism warnings.
