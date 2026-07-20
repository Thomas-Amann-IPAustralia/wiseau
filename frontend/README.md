# Frontend — Oh hi Mark(down)

Static, dependency-free UI for the Markdown ingestion engine. Deploys to GitHub
Pages as-is.

## Files

- `index.html` — layout (URL tab, file drop zone, output pane, status badge).
- `style.css` — responsive styling, light/dark aware.
- `app.js` — state management + `fetch` calls to the backend.
- `config.js` — the one file to edit per deployment: set `MARKDOWN_API_BASE`.

## Configure

Edit `config.js` and point it at your deployed backend:

```js
window.MARKDOWN_API_BASE = "https://your-user-your-space.hf.space";
```

## Run locally

Any static server works:

```bash
python -m http.server 8000
# open http://localhost:8000
```

The status badge pings `MARKDOWN_API_BASE/ping` on load to confirm the backend
is reachable.
