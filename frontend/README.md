# Frontend — Oh hi Mark(down)

Static, dependency-free UI for the Markdown ingestion engine. Deploys to GitHub
Pages as-is.

## Files

- `index.html` — layout (URL tab, file drop zone, engine picker, progress bar,
  output pane with Preview/Raw, download dialog, status badge).
- `style.css` — responsive styling, light/dark aware.
- `app.js` — state management + `fetch` calls to the backend.
- `markdown.js` — the small, dependency-free Markdown renderer behind the
  Preview mode (ADR-026). Escapes everything before rendering: converted content
  is untrusted.
- `favicon.svg` — site icon.
- `config.js` — the one file to edit per deployment: set `MARKDOWN_API_BASE`.

## What the UI does

- **Engine picker** — *Auto*, *Highest fidelity* (docling), or *Fastest*
  (PyMuPDF/Mammoth), sent as the request's `engine`. docling reads complex and
  scanned documents far more faithfully but takes tens of seconds to minutes on
  the free CPU tier; it falls back to the fast parser automatically if it is
  unavailable.
- **Progress bar** — an *approximation*. The API converts in one blocking call
  and reports no progress, so the bar is estimated from the source type, the file
  size, and the chosen engine; it keeps climbing rather than parking at "done",
  and says so when a conversion runs past its estimate (ADR-026).
- **Preview / Raw** — rendered Markdown or the exact source. Raw is what Copy and
  Download return.
- **Download** — opens a dialog pre-filled with the document's first `#` heading
  (or first `##` if there is no `#`); edit the title, then *Confirm download*.
  The title becomes the filename.

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
