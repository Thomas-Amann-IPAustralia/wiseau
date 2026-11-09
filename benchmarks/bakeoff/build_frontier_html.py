"""Build the side-by-side page: page image + Claude's, GPT's and Gemini's transcriptions.

Reads only committed files: `outputs/frontier-check/` (images, model answers,
manifest) and `references/` (Claude's hand transcriptions, frozen before this
check). Every text is embedded as base64 of its exact bytes. The page decodes
them for display, recomputes each SHA-256 in the browser, and shows it against
the hash recorded when the answer was received, so any change to a model's text
after the fact would show on the page.

  python benchmarks/bakeoff/build_frontier_html.py   # -> docs/benchmarks/2026-11-09-frontier-transcriptions.html
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from regions import DOCS  # noqa: E402

FC = HERE / "outputs" / "frontier-check"
OUT = ROOT / "docs" / "benchmarks" / "2026-11-09-frontier-transcriptions.html"

PAGES = {  # why each page is hard, from results.json and the set write-ups
    "V5N1_p18": ("Set D", "Two comparison tables printed side by side, a variety "
                 "pedigree list below them"),
    "V4_p19": ("Set C", "A five-variety comparison table above two columns of prose"),
    "V2_p12": ("Set C", "A wide four-variety comparison table with wrapped cells"),
    "V4N1_p7": ("Set F", "Two columns of descriptions with a data table, scanned at 200 dpi"),
    "P63a_p7": ("Set E", "A 1963 typewritten patent specification with handwritten "
                "margin marks"),
}
MODELS = [
    ("claude", "Claude Opus 5.5", "Hand transcription committed as the benchmark reference"),
    ("gpt-6-astra", "GPT-6 Astra", "OpenAI's flagship, reasoning effort high, image detail original"),
    ("gemini-4-argon", "Gemini 4 Argon", "Google's flagship, thinking level high, image resolution ultra high"),
]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_commit(path: Path) -> str:
    r = subprocess.run(["git", "log", "-1", "--format=%h %ad", "--date=short", "--", str(path)],
                       cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip()


def main() -> None:
    manifest = json.loads((FC / "manifest.json").read_text())
    results = json.loads((HERE / "results.json").read_text())
    skip = {"oracle-best-single", "consensus-order", "consensus-words", "hidden-layer", "trust-layer"}
    pages = []
    for key, (set_name, what) in PAGES.items():
        wers = sorted(v[key]["wer"] for e, v in results.items() if key in v and e not in skip)
        n = len(wers)
        median = (wers[n // 2] if n % 2 else (wers[n // 2 - 1] + wers[n // 2]) / 2)
        png = (FC / "images" / f"{key}.png").read_bytes()
        assert sha(png) == manifest["images"][key], f"{key}: image hash differs from manifest"
        doc, page = key.rsplit("_p", 1)
        entries = []
        for mid, name, how in MODELS:
            if mid == "claude":
                ref = HERE / "references" / f"{key}.txt"
                b = ref.read_bytes()
                entries.append({"id": mid, "name": name, "how": how, "b64": base64.b64encode(b).decode(),
                                "sha": sha(b), "source": f"benchmarks/bakeoff/references/{key}.txt",
                                "note": f"committed {git_commit(ref)}, before this check"})
                continue
            call = manifest["calls"].get(f"{mid}/{key}")
            if not call:
                entries.append({"id": mid, "name": name, "how": how, "missing": True})
                continue
            b = (FC / call["text"]).read_bytes()
            assert sha(b) == call["text_sha256"], f"{mid}/{key}: text hash differs from manifest"
            entries.append({"id": mid, "name": name, "how": how, "b64": base64.b64encode(b).decode(),
                            "sha": call["text_sha256"], "raw_sha": call["raw_sha256"],
                            "source": f"benchmarks/bakeoff/outputs/frontier-check/{call['text']}",
                            "raw": f"benchmarks/bakeoff/outputs/frontier-check/raw/{call['raw']}",
                            "note": (f"{call['seconds']} s, {call['tokens_in']} tokens in / "
                                     f"{call['tokens_out']} out, ${call['usd']:.3f}, finish: {call['finish']}")})
        pages.append({"key": key, "set": set_name, "what": what,
                      "pdf": DOCS[doc].split("/")[-1], "page": int(page),
                      "median": round(median, 3), "engines": n,
                      "img": base64.b64encode(png).decode(), "img_sha": sha(png)})
        pages[-1]["entries"] = entries
    spent = sum(c.get("usd", 0) for c in [*manifest["calls"].values(), *manifest.get("failed", []),
                                         *manifest.get("superseded", [])])
    data = {"pages": pages, "prompt": manifest["prompt"], "settings": manifest.get("settings", {}),
            "spent": round(spent, 2), "dpi": manifest["dpi"]}
    html = TEMPLATE.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False))
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB)")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Frontier Transcription Check</title>
<style>
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1d1b;--muted:#66665f;--line:#deded8;--accent:#2f5d8a;
--hl:#ffe28a;--ok:#2e7d4f;--bad:#b3261e;--code:#fbfbf9}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161615;--panel:#1f1f1d;--ink:#ecece8;
--muted:#a3a39b;--line:#383834;--accent:#8cb4dc;--hl:#6b5a12;--ok:#7fcf9f;--bad:#f2a49c;--code:#1a1a19}}
:root[data-theme="dark"]{--bg:#161615;--panel:#1f1f1d;--ink:#ecece8;--muted:#a3a39b;--line:#383834;
--accent:#8cb4dc;--hl:#6b5a12;--ok:#7fcf9f;--bad:#f2a49c;--code:#1a1a19}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header,main{max-width:1800px;margin:0 auto;padding:0 16px}
header{padding-top:24px}
h1{font-size:1.5rem;margin:0 0 4px}
h2{font-size:1.15rem;margin:0}
.sub{color:var(--muted);margin:0 0 16px;max-width:80ch}
details.method{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin-bottom:16px}
details.method summary{cursor:pointer;font-weight:600}
details.method pre{white-space:pre-wrap;background:var(--code);border:1px solid var(--line);padding:8px;border-radius:6px;font-size:13px}
.controls{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;flex-wrap:wrap;gap:8px 18px;
align-items:center;padding:10px 0;border-bottom:1px solid var(--line);margin-bottom:16px}
.controls label{display:flex;gap:6px;align-items:center;cursor:pointer}
nav.jump{display:flex;gap:6px;flex-wrap:wrap}
nav.jump a{color:var(--accent);text-decoration:none;border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:13px}
section.page{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:28px}
.phead{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px 16px;margin-bottom:10px}
.meta{color:var(--muted);font-size:13px}
.grid{display:grid;grid-template-columns:minmax(0,1.15fr) repeat(3,minmax(0,1fr));gap:12px;align-items:start}
@media (max-width:1300px){.grid{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
@media (max-width:760px){.grid{grid-template-columns:minmax(0,1fr)}}
.imgbox{position:sticky;top:64px;max-height:calc(100vh - 80px);overflow:auto;border:1px solid var(--line);border-radius:6px;background:#fff}
.imgbox img{display:block;width:100%;height:auto;cursor:zoom-in}
.imgbox.zoom img{width:220%;cursor:zoom-out}
.col{border:1px solid var(--line);border-radius:6px;min-width:0;display:flex;flex-direction:column}
.col .ch{padding:8px 10px;border-bottom:1px solid var(--line)}
.col .ch b{font-size:15px}
.col .how{color:var(--muted);font-size:12px}
.col .integrity{font-size:12px;font-family:ui-monospace,Menlo,Consolas,monospace;margin-top:4px;word-break:break-all}
.ok{color:var(--ok)}.bad{color:var(--bad)}
.body{padding:8px 10px;overflow:auto;max-height:calc(100vh - 80px)}
pre.raw{margin:0;white-space:pre-wrap;word-break:break-word;font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace}
.body.nowrap pre.raw{white-space:pre;word-break:normal}
mark{background:var(--hl);color:inherit;border-radius:2px}
.rendered table{border-collapse:collapse;font-size:12.5px;margin:6px 0}
.rendered td,.rendered th{border:1px solid var(--line);padding:2px 5px;vertical-align:top}
.rendered p{margin:0 0 8px}
.missing{color:var(--bad);padding:10px}
.stats{color:var(--muted);font-size:12px;padding:6px 10px;border-top:1px solid var(--line)}
button{font:inherit;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:3px 10px;cursor:pointer}
footer{max-width:1800px;margin:0 auto;padding:0 16px 32px;color:var(--muted);font-size:13px}
</style>
</head>
<body>
<header>
<h1>Frontier transcription check</h1>
<p class="sub">Five of the hardest scanned pages Claude has transcribed by hand for the wiseau
benchmark, each shown next to three transcriptions: Claude's committed reference, and fresh attempts by
GPT-6 Astra and Gemini 4 Argon given the same image and the same instructions. The model texts are shown
exactly as the APIs returned them. Judge each one against the page image.</p>
<details class="method"><summary>How this was made (selection, settings, integrity)</summary>
<p><b>Selection.</b> Of the 72 scanned pages with a hand transcription, these are the five with the highest
<i>median</i> word error rate across every engine already benchmarked on them (oracle, consensus and
hidden-layer rows left out). The rule was fixed before any model was called.</p>
<p><b>Claude's column</b> is the benchmark reference as committed before this check (git date shown), typed
from the page image by Claude Opus 5.5 in an earlier session, under the conventions in the prompt below.
It was not redone for this page.</p>
<p><b>The other two columns</b> each come from one successful request per page: <span id="settings"></span>
Both models got the same <span id="dpi"></span>-dpi PNG, the one shown here. Total spend: $<span id="spent"></span>,
including failed and superseded requests. A 30-second proxy timeout made the first request to each model fail
(HTTP 502, kept on record). The first Gemini answer for V5N1 p18 was made at the "high" image setting. It was
superseded <i>before any comparison</i>, because "ultra high" is Gemini's maximum and GPT had read the page at
full resolution. The superseded answer is kept unchanged in the repository.</p>
<p><b>Integrity.</b> Each text is embedded here as its exact bytes. Your browser recomputes each SHA-256 when the
page loads and checks it against the hash recorded when the API response arrived. The raw response bodies are in
<code>benchmarks/bakeoff/outputs/frontier-check/raw/</code>, with their own hashes in <code>manifest.json</code>.
"Rendered" and "highlight" only change how the text is displayed, never the text itself.</p>
<p><b>The prompt</b>, given to both models with the image:</p>
<pre id="prompt"></pre>
</details>
</header>
<main>
<div class="controls">
<label><input type="radio" name="view" value="raw" checked> Raw text</label>
<label><input type="radio" name="view" value="rendered"> Rendered Markdown</label>
<label><input type="checkbox" id="hl"> Highlight words the other two don't have</label>
<label><input type="checkbox" id="wrap" checked> Wrap lines</label>
<label><input type="checkbox" id="blind"> Blind (hide and shuffle model names)</label>
<nav class="jump" id="jump"></nav>
</div>
<div id="pages"></div>
</main>
<footer>Generated by <code>benchmarks/bakeoff/build_frontier_html.py</code>. Click a page image to zoom it.</footer>
<script>
const DATA = /*DATA*/null;
const $ = (s, el = document) => el.querySelector(s);
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const dec = b64 => { const bin = atob(b64); const u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; };
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");

$("#prompt").textContent = DATA.prompt;
$("#spent").textContent = DATA.spent.toFixed(2);
$("#dpi").textContent = DATA.dpi;
$("#settings").textContent = Object.entries(DATA.settings).map(([k, v]) => `${k}: ${v}.`).join(" ");

// --- tiny Markdown renderer (display only): pipe tables, headings, paragraphs ---
function md(text) {
  const lines = text.split("\n"); let out = "", i = 0;
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|\W)\*(\S.*?)\*(?=\W|$)/g, "$1<i>$2</i>");
  const cells = l => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (/^\s*\|/.test(l)) {
      let rows = []; while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      out += "<table>" + rows.filter(r => !/^\s*\|?[\s:|-]+\|?\s*$/.test(r))
        .map(r => "<tr>" + cells(r).map(c => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</table>";
      continue;
    }
    const h = l.match(/^(#{1,6})\s+(.*)/);
    if (h) { out += `<p><b>${inline(h[2])}</b></p>`; i++; continue; }
    if (!l.trim()) { i++; continue; }
    let para = []; while (i < lines.length && lines[i].trim() && !/^\s*\|/.test(lines[i]) && !/^#{1,6}\s/.test(lines[i])) para.push(inline(lines[i++]));
    out += `<p>${para.join("<br>")}</p>`;
  }
  return out;
}

// --- word highlighting (display only) ---
const norm = w => w.replace(/^[|*#_`>:\-]+$/, "").toLowerCase();
function bag(text) { const m = new Map();
  for (const w of text.split(/[\s|]+/)) { const n = norm(w); if (n) m.set(n, (m.get(n) || 0) + 1); } return m; }
function highlighted(text, others) {
  const seen = new Map();
  return text.split(/(\s+|\|)/).map(tok => {
    const n = norm(tok);
    if (!n || /^\s+$/.test(tok) || tok === "|") return esc(tok);
    const k = (seen.get(n) || 0) + 1; seen.set(n, k);
    const max = Math.max(...others.map(o => o.get(n) || 0));
    return k > max ? `<mark>${esc(tok)}</mark>` : esc(tok);
  }).join("");
}

// --- seeded shuffle for blind mode ---
function shuffled(arr, seed) { const a = arr.slice(); let s = seed;
  for (let i = a.length - 1; i > 0; i--) { s = (s * 1103515245 + 12345) & 0x7fffffff; const j = s % (i + 1); [a[i], a[j]] = [a[j], a[i]]; }
  return a; }

const td = new TextDecoder("utf-8", { fatal: true });
DATA.pages.forEach(p => p.entries.forEach(e => { if (!e.missing) { e.bytes = dec(e.b64); e.text = td.decode(e.bytes); } }));

function draw() {
  const view = document.querySelector('input[name="view"]:checked').value;
  const hl = $("#hl").checked, blind = $("#blind").checked, wrap = $("#wrap").checked;
  const root = $("#pages"); root.innerHTML = "";
  $("#jump").innerHTML = DATA.pages.map(p => `<a href="#${p.key}">${p.key.replace("_p", " p")}</a>`).join("");
  DATA.pages.forEach((p, pi) => {
    const sec = document.createElement("section"); sec.className = "page"; sec.id = p.key;
    sec.innerHTML = `<div class="phead"><h2>${p.key.replace("_p", " p")} · ${esc(p.what)}</h2>
      <div class="meta">${esc(p.pdf)}, page ${p.page} · ${p.set} · median wer ${p.median} across ${p.engines} engines</div></div>`;
    const grid = document.createElement("div"); grid.className = "grid";
    grid.innerHTML = `<div class="imgbox"><img alt="Scan of ${esc(p.pdf)} page ${p.page}" src="data:image/png;base64,${p.img}"></div>`;
    $("img", grid).onclick = ev => ev.currentTarget.parentElement.classList.toggle("zoom");
    const entries = blind ? shuffled(p.entries, 7919 * (pi + 3)) : p.entries;
    const bags = entries.map(e => e.missing ? new Map() : bag(e.text));
    entries.forEach((e, ei) => {
      const col = document.createElement("div"); col.className = "col";
      const title = blind ? `Transcription ${"ABC"[ei]}` : esc(e.name);
      if (e.missing) { col.innerHTML = `<div class="ch"><b>${title}</b></div><div class="missing">No answer recorded.</div>`; grid.append(col); return; }
      const others = bags.filter((_, j) => j !== ei);
      const body = view === "rendered" ? `<div class="rendered">${md(e.text)}</div>`
        : `<pre class="raw">${hl ? highlighted(e.text, others) : esc(e.text)}</pre>`;
      const words = e.text.split(/\s+/).filter(Boolean).length;
      col.innerHTML = `<div class="ch"><b>${title}</b>${blind ? "" : `<div class="how">${esc(e.how)}</div>
        <div class="how">${esc(e.note)}</div>`}
        <div class="integrity" data-sha="${e.sha}">sha256 checking…</div></div>
        <div class="body${wrap ? "" : " nowrap"}">${body}</div>
        <div class="stats">${words} words · ${e.text.split("\n").length} lines${blind ? "" : ` · <code>${esc(e.source)}</code>`}</div>`;
      grid.append(col);
      crypto.subtle.digest("SHA-256", e.bytes).then(d => {
        const got = hex(d), el = $(".integrity", col);
        el.innerHTML = got === e.sha ? `<span class="ok">✓ sha256 ${got.slice(0, 16)}… matches the recorded hash</span>`
          : `<span class="bad">✗ sha256 ${got.slice(0, 16)}… does NOT match ${e.sha.slice(0, 16)}…</span>`;
      }).catch(() => { $(".integrity", col).textContent = "sha256 " + e.sha.slice(0, 16) + "… (browser cannot verify here)"; });
    });
    sec.append(grid); root.append(sec);
  });
}
document.querySelectorAll(".controls input").forEach(i => i.addEventListener("change", draw));
draw();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
