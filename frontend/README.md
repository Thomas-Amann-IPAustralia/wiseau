# Frontend — Oh hi Mark(down)

Static, dependency-free UI for the Markdown ingestion engine. Deploys to GitHub
Pages as-is.

## Files

- `index.html` — layout (URL tab, multi-file drop zone, engine picker,
  chapter-split checkbox, progress bar, output pane with Preview/Raw and the
  results list, download dialog, status badge).
- `style.css` — responsive styling, light/dark aware.
- `app.js` — state management + `fetch` calls to the backend.
- `markdown.js` — the small, dependency-free Markdown renderer behind the
  Preview mode (ADR-026). Escapes everything before rendering: converted content
  is untrusted.
- `zip.js` — a ~140-line ZIP writer, so "download every chapter" — or every
  document of a batch (ADR-031) — is one archive instead of a dozen blocked
  downloads (ADR-030). Stored entries, fixed timestamp, no dependency.
- `favicon.svg` — site icon.
- `config.js` — the one file to edit per deployment: set `MARKDOWN_API_BASE`.

## What the UI does

- **Engine picker** — *Auto*, *Fastest* (PyMuPDF/Mammoth), or *Highest fidelity*
  (docling), sent as the request's `engine`. The fast parser is the default
  (ADR-027) and answers in about a second; docling reads complex and scanned
  documents far more faithfully but takes tens of seconds to minutes on the free
  CPU tier, and falls back to the fast parser automatically if it is unavailable.
  *Auto* is labelled from `/ping`'s `default_engine`, so it says what the backend
  in front of it will actually do.
- **Progress bar** — an *approximation*. The API converts in one blocking call
  and reports no progress, so the bar is estimated from the source type, the file
  size, and the engine that will run (for *Auto*, the one `/ping` reports); it
  keeps climbing rather than parking at "done",
  and says so when a conversion runs past its estimate (ADR-026).
- **Preview / Raw** — rendered Markdown or the exact source. Raw is what Copy and
  Download return.
- **Download** — opens a dialog pre-filled with the document's first `#` heading
  (or first `##` if there is no `#`); edit the title, then *Confirm download*.
  The title becomes the filename.
- **Split into chapters** — for a long document. Tick it before converting and
  the backend also returns the document as chapters (found from its contents page
  where it has one, otherwise from its headings — ADR-030). The panel above the
  output lists them with the filename each would be saved under and says what
  found them; *View* shows one chapter, *Save* writes it, and *Download all
  (.zip)* packs the lot. A document with no chapters is simply left whole.
- **Bulk conversion** — drop or pick several documents and they go to
  `POST /convert/batch` as one request, coming back as one Markdown file each
  (ADR-031). The same panel lists them, so *Download all (.zip)* is again one
  archive. A document that fails is shown in place with the reason and offers
  nothing to save, while the rest still convert.
  Selecting more than one file **disables and unticks** *Split into chapters*:
  the two are mutually exclusive for now, and a ticked box that was quietly
  ignored would be worse than no box at all.

## Configure

Edit `config.js` and point it at your deployed backend:

```js
window.MARKDOWN_API_BASE = "https://your-user-your-space.hf.space";
```

## Deploy to GitHub Pages

`.github/workflows/deploy-frontend.yml` publishes this directory on every push
to `main` that touches it, and on demand via **Actions → Deploy frontend → Run
workflow**. A workflow is needed because Pages' branch setting can only serve a
repository root or `/docs`, and `/docs` holds the project documentation.

One-time setup: set **Settings → Pages → Source** to *GitHub Actions*, and set
the `MARKDOWN_API_BASE` **repository variable** (Settings → Secrets and variables
→ Actions → Variables) to the backend Space URL. The workflow writes that value
into `config.js` in the uploaded copy only — the committed default stays
`localhost`, so local development is unaffected and the deployment URL never
needs to be committed. Leave the variable unset and the file ships exactly as
committed. See ADR-023.

## Run locally

Any static server works:

```bash
python -m http.server 8000
# open http://localhost:8000
```

The status badge pings `MARKDOWN_API_BASE/ping` on load to confirm the backend
is reachable.
