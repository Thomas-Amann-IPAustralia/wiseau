/*
 * Oh hi Mark(down) — frontend application logic.
 *
 * State management + fetch calls to the FastAPI backend. Set API_BASE to your
 * deployed Hugging Face Space URL (e.g. "https://your-space.hf.space").
 */

// --- Configuration ----------------------------------------------------------
const API_BASE = (window.MARKDOWN_API_BASE || "http://localhost:7860").replace(/\/$/, "");

// Conversion-time estimates, in seconds, used to drive the progress bar. The API
// reports no progress (a conversion is one blocking call), so the bar is an
// *approximation* from what the client does know: the source type, the file
// size, and the chosen engine. Deliberately generous — a bar that reaches the
// end and waits reads as "stuck", one that is still climbing reads as "working".
const ESTIMATES = {
  // A headless-Chrome render dominates and barely varies with page size.
  url: { base: 9, perMb: 0 },
  // PyMuPDF/Mammoth: fast and roughly linear in document size.
  pymupdf: { base: 2, perMb: 1.5 },
  // The same path when the document is a scan: OCR at 300 DPI is much heavier.
  ocr: { base: 8, perMb: 6 },
  // docling on a free CPU Space: an ML pipeline, plus a possible cold start.
  docling: { base: 30, perMb: 20 },
};

// Keyword extraction is not a conversion, so it gets its own two estimates
// (ADR-032): the statistical methods scan text and answer in under a second,
// while KeyBERT runs a transformer on free CPU and may also be loading its
// weights for the first time.
const KEYWORD_ESTIMATES = { fast: 3, semantic: 45 };

const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"];

// --- Element references ------------------------------------------------------
const el = {
  statusBadge: document.getElementById("status-badge"),
  statusText: document.getElementById("status-text"),
  urlInput: document.getElementById("url-input"),
  convertUrlBtn: document.getElementById("convert-url-btn"),
  dropZone: document.getElementById("drop-zone"),
  fileInput: document.getElementById("file-input"),
  fileName: document.getElementById("file-name"),
  convertFileBtn: document.getElementById("convert-file-btn"),
  engineInputs: document.querySelectorAll("input[name='engine']"),
  engineAutoHint: document.getElementById("engine-auto-hint"),
  splitChapters: document.getElementById("split-chapters"),
  splitChaptersLabel: document.getElementById("split-chapters-label"),
  splitChaptersBlocked: document.getElementById("split-chapters-blocked"),
  results: document.getElementById("results"),
  resultsTitle: document.getElementById("results-title"),
  resultsList: document.getElementById("results-list"),
  resultsMeta: document.getElementById("results-meta"),
  resultsWholeBtn: document.getElementById("results-whole-btn"),
  resultsZipBtn: document.getElementById("results-zip-btn"),
  progress: document.getElementById("progress"),
  progressFill: document.getElementById("progress-fill"),
  progressLabel: document.getElementById("progress-label"),
  progressTimer: document.getElementById("progress-timer"),
  output: document.getElementById("output"),
  outputPreview: document.getElementById("output-preview"),
  viewButtons: document.querySelectorAll(".view-toggle__btn"),
  outputMeta: document.getElementById("output-meta"),
  keywordsBtn: document.getElementById("keywords-btn"),
  keywords: document.getElementById("keywords"),
  keywordsMeta: document.getElementById("keywords-meta"),
  keywordsMethods: document.getElementById("keywords-methods"),
  keywordsNote: document.getElementById("keywords-note"),
  keywordsRows: document.getElementById("keywords-rows"),
  keywordsJsonBtn: document.getElementById("keywords-json-btn"),
  keywordsApplyBtn: document.getElementById("keywords-apply-btn"),
  copyBtn: document.getElementById("copy-btn"),
  downloadBtn: document.getElementById("download-btn"),
  downloadDialog: document.getElementById("download-dialog"),
  downloadTitle: document.getElementById("download-title"),
  downloadFilename: document.getElementById("download-filename"),
  tabs: document.querySelectorAll(".tab"),
  panes: { url: document.getElementById("tab-url"), file: document.getElementById("tab-file") },
};

let selectedFiles = [];
// What the backend resolves "auto" to. Assume the standard default (the fast
// parser) until /ping says otherwise; only the estimate depends on it, so a
// deployment that never answers /ping still converts normally.
let serverDefaultEngine = "pymupdf";
let lastMarkdown = "";
let lastSource = "";
let currentView = "preview";
// The saveable files behind the results panel: a document's chapters, or a
// batch's documents. Never both — they are mutually exclusive (ADR-031).
let lastFiles = [];
// Which results row the output panel is showing, or -1 for the whole document.
// Applying keywords writes back into that row, so the row's own Save and the
// archive carry the table too.
let activeResult = -1;

// The keyword methods this deployment can actually run, and the ones it uses
// when a request names none — both from /ping, so the panel offers only choices
// that will work rather than a checkbox that reports itself skipped (ADR-032).
let keywordMethods = ["frequency"];
let keywordDefaults = ["frequency"];
// The current extraction: the API response, the annotated document it produced,
// and whether that annotation is what the output panel is showing.
let keywordState = null;
// Whether the user has touched the method chips. Until they have, the ticks
// follow the server's default — otherwise the one chip rendered before /ping
// answers becomes the whole selection, and the deployment's default never
// applies (found by driving the UI).
let keywordMethodsTouched = false;

// --- Tab switching -----------------------------------------------------------
el.tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    el.tabs.forEach((t) => t.classList.remove("tab--active"));
    tab.classList.add("tab--active");
    Object.values(el.panes).forEach((p) => p.classList.remove("tab-pane--active"));
    el.panes[tab.dataset.tab].classList.add("tab-pane--active");
  });
});

// --- Status badge ------------------------------------------------------------
async function pingBackend() {
  setBadge("unknown", "checking…");
  try {
    const res = await fetch(`${API_BASE}/ping`, { method: "GET" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    setBadge("online", "online");
    const info = await res.json();
    applyServerDefaultEngine(info);
    applyKeywordMethods(info);
  } catch {
    setBadge("offline", "offline");
  }
}

// /ping reports which engine "auto" resolves to, so the Auto option can say what
// it will actually do instead of guessing (ADR-027).
function applyServerDefaultEngine(info) {
  const reported = info && typeof info.default_engine === "string" ? info.default_engine : "";
  if (reported !== "docling" && reported !== "pymupdf") return;
  serverDefaultEngine = reported;
  if (el.engineAutoHint) {
    el.engineAutoHint.textContent =
      reported === "docling" ? "Server's default — docling" : "Server's default — fastest";
  }
}

// /ping also reports which keyword methods this build can run and which of them
// it defaults to, so the panel offers exactly the choices that will work — the
// same contract the engine picker uses (ADR-027/032). A deployment that never
// answers /ping keeps the built-in method, which needs nothing.
function applyKeywordMethods(info) {
  if (!info) return;
  if (Array.isArray(info.keyword_methods) && info.keyword_methods.length) {
    keywordMethods = info.keyword_methods.filter((name) => typeof name === "string");
  }
  if (Array.isArray(info.default_keyword_methods) && info.default_keyword_methods.length) {
    keywordDefaults = info.default_keyword_methods.filter((name) => keywordMethods.includes(name));
  }
  renderKeywordMethods();
}

function setBadge(state, text) {
  el.statusBadge.className = `badge badge--${state}`;
  el.statusText.textContent = text;
}

// --- Engine selection --------------------------------------------------------
function selectedEngine() {
  const chosen = Array.from(el.engineInputs).find((input) => input.checked);
  return chosen ? chosen.value : "auto";
}

/** Whether to ask the backend for the document split into chapters (ADR-030). */
function wantsChapters() {
  return Boolean(el.splitChapters && el.splitChapters.checked && !el.splitChapters.disabled);
}

/**
 * Keep the two "one file becomes many files" features apart (ADR-031).
 * A batch cannot be split into chapters, so selecting several documents takes
 * the option away and says why, rather than leaving a checkbox that would be
 * quietly ignored. Unchecking it as well as disabling it matters: a checkbox
 * that stays ticked while having no effect is how someone comes to believe they
 * asked for chapters and got none.
 */
function applyChapterAvailability() {
  if (!el.splitChapters) return;
  const blocked = selectedFiles.length > 1;
  if (blocked) el.splitChapters.checked = false;
  el.splitChapters.disabled = blocked;
  el.splitChaptersLabel.classList.toggle("check--blocked", blocked);
  el.splitChaptersBlocked.hidden = !blocked;
}

// --- Progress approximation --------------------------------------------------
/** The engine that will actually run: "auto" is whatever the server defaults to. */
function effectiveEngine(engine) {
  return engine === "auto" ? serverDefaultEngine : engine;
}

/**
 * Seconds this conversion is expected to take.
 * An `auto` request is estimated as the server's reported default (the fast
 * parser unless the deployment says otherwise); if the estimate turns out long
 * the job simply finishes early, which the bar handles by jumping to done.
 */
function estimateSeconds(kind, file, engine) {
  if (kind === "url") return ESTIMATES.url.base;

  const megabytes = (file ? file.size : 0) / (1024 * 1024);
  const isImage = file && IMAGE_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext));
  let profile = ESTIMATES.docling;
  if (effectiveEngine(engine) === "pymupdf") profile = isImage ? ESTIMATES.ocr : ESTIMATES.pymupdf;
  return profile.base + profile.perMb * megabytes;
}

/**
 * Seconds for the whole selection: a batch converts its documents one at a
 * time, so the estimates add up. Worth being honest about — with docling
 * selected, ten documents is a wait measured in minutes, and a bar that implied
 * otherwise would look broken rather than busy.
 */
function estimateBatchSeconds(files, engine) {
  return files.reduce((total, file) => total + estimateSeconds("file", file, engine), 0);
}

let progressTimerId = null;

function startProgress(estimate, engine) {
  const started = Date.now();
  const slowEngine = effectiveEngine(engine) === "docling";
  el.progress.hidden = false;
  el.progress.classList.remove("progress--done");
  el.progressLabel.textContent = `Converting… about ${formatSeconds(estimate)}`;
  el.progressFill.style.width = "0%";

  progressTimerId = setInterval(() => {
    const elapsed = (Date.now() - started) / 1000;
    // Asymptotic: 95% at the estimate, never quite 100% until the response is in.
    // An estimate that turns out short slows down instead of stalling at the end.
    const fraction = Math.min(0.99, 1 - Math.exp((-3 * elapsed) / Math.max(estimate, 1)));
    el.progressFill.style.width = `${(fraction * 100).toFixed(1)}%`;
    el.progressTimer.textContent = formatSeconds(elapsed);
    if (elapsed > estimate * 1.3) {
      el.progressLabel.textContent = slowEngine
        ? "Still working — docling may be starting up from cold."
        : "Still working — this document is taking longer than usual.";
    }
  }, 100);
}

function stopProgress() {
  if (progressTimerId !== null) clearInterval(progressTimerId);
  progressTimerId = null;
  el.progressFill.style.width = "100%";
  el.progress.classList.add("progress--done");
  // Let the filled bar register before it disappears.
  setTimeout(() => {
    if (progressTimerId === null) el.progress.hidden = true;
  }, 400);
}

function formatSeconds(seconds) {
  const rounded = Math.round(seconds);
  if (rounded < 60) return `${rounded}s`;
  return `${Math.floor(rounded / 60)}m ${String(rounded % 60).padStart(2, "0")}s`;
}

// --- Output helpers ----------------------------------------------------------
function showMarkdown(markdown, meta, source) {
  lastMarkdown = markdown;
  lastSource = source || "";
  el.output.classList.remove("is-error");
  el.outputPreview.classList.remove("is-error");
  el.output.textContent = markdown || "(empty result)";
  el.outputPreview.innerHTML = markdown
    ? window.wiseauMarkdown.render(markdown)
    : '<p class="placeholder">(empty result)</p>';
  el.outputMeta.textContent = meta || "";
  el.copyBtn.disabled = !markdown;
  el.downloadBtn.disabled = !markdown;
  updateKeywordsButton();
}

/** The output panel's caption: size, and what the chapter split found. */
function metaFor(data) {
  const parts = [`${(data.length || 0).toLocaleString()} chars`];
  if (data.chapters && data.chapters.length) {
    parts.push(`${data.chapters.length} chapters`);
  } else if (data.chapter_detection === "none") {
    parts.push("no chapters detected");
  }
  return parts.join(" · ");
}

/** Put a failure in the output panel, leaving the results list alone. */
function showFailure(message) {
  lastMarkdown = "";
  el.output.classList.add("is-error");
  el.outputPreview.classList.add("is-error");
  el.output.textContent = `⚠ ${message}`;
  el.outputPreview.textContent = `⚠ ${message}`;
  el.outputMeta.textContent = "";
  el.copyBtn.disabled = true;
  el.downloadBtn.disabled = true;
  clearKeywords();
}

function showError(message) {
  clearResults();
  showFailure(message);
}

function setBusy(button, busy, idleLabel) {
  button.disabled = busy;
  button.textContent = busy ? "Converting…" : idleLabel;
  el.engineInputs.forEach((input) => (input.disabled = busy));
}

async function parseResponse(res) {
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    // The API reports failures as `detail`; the rate limiter is the exception —
    // slowapi writes its own body with `error`. Worth reading both, because a
    // batch has the tightest limit on the service (5/minute) and "Request failed
    // (HTTP 429)" tells the user nothing about waiting a minute and retrying.
    throw new Error(payload.detail || payload.error || `Request failed (HTTP ${res.status})`);
  }
  return payload;
}

// --- The results panel -------------------------------------------------------
// One list serves both features that turn a conversion into several saveable
// files: the chapters of one document (ADR-030) and the documents of a batch
// (ADR-031). They are mutually exclusive, so a row is a row — a title, the
// filename it saves as, its size, and View/Save — and only the heading, the
// caption, and the archive's name differ.

// How the backend found the chapters, said plainly: a split taken from the
// document's own contents page deserves more trust than one guessed from
// headings, and the panel should let the reader judge before saving 30 files.
const DETECTION_LABEL = {
  toc: "found on the document's contents page",
  headings: "found from the document's headings",
  markers: "found from “Chapter N” lines in the text",
};

// The whole document, kept aside so that viewing one chapter is reversible.
// A batch has no such thing, which is why "Whole document" hides for one.
let documentView = { markdown: "", meta: "", source: "" };
// What "Download all (.zip)" should call the archive for the current panel.
let archiveName = "chapters.zip";
// Which feature the results panel is currently serving: "chapters", "documents"
// (a batch), or "none". It decides whether the Keywords button acts on the whole
// set or on the one document showing (ADR-033).
let resultsKind = "none";

function clearResults() {
  lastFiles = [];
  resultsKind = "none";
  el.resultsList.replaceChildren();
  el.results.hidden = true;
  // A new set of results makes any existing extraction stale, and the panel is
  // addressed by row index — so keeping it would show one document's keywords
  // against another's text.
  clearKeywords();
}

/**
 * Fill the panel.
 * @param {{title: string, filename: string, markdown: string, length: number,
 *          error?: string}[]} files rows to show; a row with `error` is a
 *        failure and offers nothing to view or save.
 */
function showResults(title, caption, files, options) {
  clearResults();
  lastFiles = files;
  resultsKind = options.kind;
  if (!files.length) {
    updateKeywordsButton();
    return;
  }
  el.resultsTitle.textContent = title;
  el.resultsMeta.textContent = caption;
  el.resultsWholeBtn.hidden = !options.wholeDocument;
  archiveName = options.archive;
  el.resultsList.append(...files.map(resultRow));
  el.resultsZipBtn.disabled = !files.some((file) => !file.error);
  el.results.hidden = false;
  updateKeywordsButton();
}

/**
 * One row of the list.
 * Built as DOM nodes rather than markup: a chapter title is text lifted out of
 * an arbitrary document and a document title is a filename someone else chose,
 * so both are only ever assigned as `textContent`.
 */
function resultRow(file, index) {
  const item = document.createElement("li");
  item.className = file.error ? "result result--error" : "result";

  const title = document.createElement("span");
  title.className = "result__title";
  title.textContent = file.title;

  const filename = document.createElement("code");
  filename.className = "result__file";
  filename.textContent = file.filename || "";

  const text = document.createElement("span");
  text.className = "result__text";
  text.append(title, filename);

  const actions = document.createElement("span");
  actions.className = "result__actions";

  if (file.error) {
    const failure = document.createElement("span");
    failure.className = "result__error";
    failure.textContent = file.error;
    actions.append(failure);
  } else {
    const size = document.createElement("span");
    size.className = "result__size";
    size.textContent = `${file.length.toLocaleString()} chars`;

    const view = document.createElement("button");
    view.className = "btn btn--ghost btn--small";
    view.textContent = "View";
    view.addEventListener("click", () => viewResult(index));

    const save = document.createElement("button");
    save.className = "btn btn--ghost btn--small";
    save.textContent = "Save";
    save.addEventListener("click", () => saveText(file.markdown, file.filename));

    actions.append(size, view, save);
  }

  item.append(text, actions);
  return item;
}

/** Show one row's Markdown in the output panel; Copy and Download follow it. */
function viewResult(index) {
  const file = lastFiles[index];
  if (!file || file.error) return;
  // A batch's keywords cover every document, so moving between them shows that
  // document's keywords instead of throwing the extraction away. Anything the
  // extraction did not cover still clears, so a stale ranking is never left
  // attached to a document it does not describe.
  if (!showKeywordsFor(index)) clearKeywords();
  markActiveResult(index);
  showMarkdown(file.markdown, `${file.length.toLocaleString()} chars · ${file.filename}`, file.title);
}

function showWholeDocument() {
  if (!showKeywordsFor(-1)) clearKeywords();
  markActiveResult(-1);
  showMarkdown(documentView.markdown, documentView.meta, documentView.source);
}

function markActiveResult(index) {
  activeResult = index;
  Array.from(el.resultsList.children).forEach((row, position) =>
    row.classList.toggle("result--active", position === index)
  );
}

el.resultsWholeBtn.addEventListener("click", showWholeDocument);

el.resultsZipBtn.addEventListener("click", () => {
  const saveable = lastFiles.filter((file) => !file.error);
  if (!saveable.length) return;
  const archive = window.wiseauZip.build(
    saveable.map((file) => ({ name: file.filename, content: file.markdown }))
  );
  saveBlob(archive, archiveName);
});

// --- Single conversions ------------------------------------------------------
/** Show a finished conversion: the document, then its chapters if there are any. */
function showConverted(data, fallbackSource) {
  clearKeywords();
  documentView = {
    markdown: data.markdown,
    meta: metaFor(data),
    source: data.source || fallbackSource,
  };
  showMarkdown(documentView.markdown, documentView.meta, documentView.source);
  markActiveResult(-1);

  const chapters = Array.isArray(data.chapters) ? data.chapters : [];
  const stem = filenameFor(titleFor(documentView.markdown, documentView.source)).replace(/\.md$/i, "");
  showResults("Chapters", DETECTION_LABEL[data.chapter_detection] || "", chapters, {
    kind: "chapters",
    wholeDocument: true,
    archive: `${stem}-chapters.zip`,
  });
}

// --- Batch conversions -------------------------------------------------------
/**
 * Show a finished batch: one row per document, then the first one that
 * converted, so the output panel holds a real document rather than the
 * placeholder while the user decides what to look at.
 */
function showBatch(data) {
  const results = Array.isArray(data.results) ? data.results : [];
  const files = results.map((item) => ({
    title: item.source,
    filename: item.filename || "",
    markdown: item.markdown || "",
    length: item.length || 0,
    error: item.error || "",
  }));

  const caption =
    data.failed > 0
      ? `${data.succeeded} of ${data.count} converted · ${data.failed} failed`
      : `${data.count} document${data.count === 1 ? "" : "s"} converted`;
  showResults("Documents", caption, files, {
    kind: "documents",
    wholeDocument: false,
    archive: "converted-markdown.zip",
  });

  const first = files.findIndex((file) => !file.error);
  if (first >= 0) {
    viewResult(first);
  } else {
    // Every document failed. The list is the useful part — it says which
    // document failed and why — so it stays, and only the output panel reports
    // that there is nothing to show.
    showFailure("Nothing converted — every document in the batch failed. See the list above for why.");
  }
}

// --- View toggle (rendered vs raw) -------------------------------------------
el.viewButtons.forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

function setView(view) {
  currentView = view;
  el.viewButtons.forEach((button) =>
    button.classList.toggle("view-toggle__btn--active", button.dataset.view === view)
  );
  el.outputPreview.hidden = view !== "preview";
  el.output.hidden = view !== "raw";
}

// --- URL conversion ----------------------------------------------------------
el.convertUrlBtn.addEventListener("click", async () => {
  const url = el.urlInput.value.trim();
  if (!url) return showError("Enter a URL first.");
  const engine = selectedEngine();
  setBusy(el.convertUrlBtn, true, "Convert");
  startProgress(estimateSeconds("url", null, engine), engine);
  try {
    const res = await fetch(`${API_BASE}/convert/url`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, engine, split_chapters: wantsChapters() }),
    });
    const data = await parseResponse(res);
    showConverted(data, url);
  } catch (err) {
    showError(err.message);
  } finally {
    stopProgress();
    setBusy(el.convertUrlBtn, false, "Convert");
  }
});

// --- File selection + drag/drop ---------------------------------------------
el.dropZone.addEventListener("click", () => el.fileInput.click());
el.fileInput.addEventListener("change", () => setFiles(el.fileInput.files));

["dragenter", "dragover"].forEach((evt) =>
  el.dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    el.dropZone.classList.add("drop-zone--over");
  })
);
["dragleave", "drop"].forEach((evt) =>
  el.dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    el.dropZone.classList.remove("drop-zone--over");
  })
);
el.dropZone.addEventListener("drop", (e) => setFiles(e.dataTransfer.files));

/** Take a `FileList` (from the picker or a drop) as the current selection. */
function setFiles(list) {
  selectedFiles = Array.from(list || []);
  el.fileName.replaceChildren(...selectionLabel(selectedFiles));
  el.convertFileBtn.disabled = selectedFiles.length === 0;
  el.convertFileBtn.textContent = selectedFiles.length > 1 ? `Convert ${selectedFiles.length} files` : "Convert";
  applyChapterAvailability();
}

/**
 * Name what is selected. Filenames are text the user's filesystem supplied, so
 * they are set as `textContent`; a long list is truncated because the drop zone
 * is not a file manager.
 */
function selectionLabel(files) {
  const shown = files.slice(0, 4).map((file) => {
    const line = document.createElement("span");
    line.textContent = file.name;
    return line;
  });
  if (files.length > shown.length) {
    const more = document.createElement("span");
    more.className = "more";
    more.textContent = `and ${files.length - shown.length} more`;
    shown.push(more);
  }
  return shown;
}

// --- File conversion ---------------------------------------------------------
// One file goes to /convert/file and may be split into chapters; several go to
// /convert/batch and come back as one Markdown file each (ADR-031). The two are
// mutually exclusive, which is what keeps this a fork rather than a matrix.
el.convertFileBtn.addEventListener("click", async () => {
  if (!selectedFiles.length) return;
  const engine = selectedEngine();
  const batch = selectedFiles.length > 1;
  const idleLabel = batch ? `Convert ${selectedFiles.length} files` : "Convert";
  setBusy(el.convertFileBtn, true, idleLabel);
  startProgress(
    batch ? estimateBatchSeconds(selectedFiles, engine) : estimateSeconds("file", selectedFiles[0], engine),
    engine
  );
  try {
    const form = new FormData();
    form.append("engine", engine);
    if (batch) {
      selectedFiles.forEach((file) => form.append("files", file));
    } else {
      form.append("file", selectedFiles[0]);
      form.append("split_chapters", String(wantsChapters()));
    }
    const res = await fetch(`${API_BASE}/convert/${batch ? "batch" : "file"}`, {
      method: "POST",
      body: form,
    });
    const data = await parseResponse(res);
    if (batch) showBatch(data);
    else showConverted(data, selectedFiles[0].name);
  } catch (err) {
    showError(err.message);
  } finally {
    stopProgress();
    setBusy(el.convertFileBtn, false, idleLabel);
  }
});

// --- Keywords (ADR-032) ------------------------------------------------------
// Extraction is a *second* call, made after the Markdown exists, on whatever the
// output panel is currently showing. That is why there is no checkbox in the
// input column: nobody knows they want keywords until they have seen the
// document, and asking afterwards costs the extraction rather than a second
// conversion — which, with docling, is a minute.

// How each method reads in the panel. The hint is the honest trade-off, because
// the choice a user makes here is a cost/quality one and nothing else in the UI
// will tell them.
const KEYWORD_METHOD_LABELS = {
  frequency: { name: "Frequency", hint: "built in, structural" },
  yake: { name: "YAKE", hint: "statistical" },
  spacy: { name: "spaCy", hint: "entities & phrases" },
  keybert: { name: "KeyBERT", hint: "semantic — slow", slow: true },
};

function renderKeywordMethods() {
  if (!el.keywordsMethods) return;
  const chosen = new Set(keywordMethodsTouched ? selectedKeywordMethods() : []);
  el.keywordsMethods.replaceChildren(
    ...keywordMethods.map((method) => {
      const meta = KEYWORD_METHOD_LABELS[method] || { name: method, hint: "" };
      const label = document.createElement("label");
      label.className = "keywords__method";
      label.title = meta.hint;

      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = method;
      input.checked = chosen.size ? chosen.has(method) : keywordDefaults.includes(method);
      input.addEventListener("change", () => {
        // Unticking the last method would ask the backend for nothing, so the
        // built-in method is what a cleared selection falls back to.
        if (!selectedKeywordMethods().length) input.checked = true;
        keywordMethodsTouched = true;
        runKeywordExtraction();
      });

      const name = document.createElement("span");
      name.textContent = meta.name;
      if (meta.slow) name.className = "slow";

      label.append(input, name);
      return label;
    })
  );
}

/** The methods currently ticked, or the deployment's default before any render. */
function selectedKeywordMethods() {
  if (!el.keywordsMethods) return [];
  const ticked = Array.from(el.keywordsMethods.querySelectorAll("input:checked")).map((i) => i.value);
  // Before the chips exist, ask for nothing and let the deployment decide —
  // sending a method set the user never chose would override its default.
  return ticked;
}

function clearKeywords() {
  keywordState = null;
  if (!el.keywords) return;
  el.keywords.hidden = true;
  el.keywordsRows.replaceChildren();
  el.keywordsNote.hidden = true;
  el.keywordsApplyBtn.textContent = "Add to Markdown";
  updateKeywordsButton();
}

/**
 * Whether the Keywords button will act on the whole batch (ADR-033).
 * A batch is the case where "and what are these about?" means every document —
 * extracting for one of twelve is the odd request, not the usual one. Chapters
 * are one document split up, so there the button keeps acting on what is shown.
 */
function keywordTargets() {
  if (resultsKind === "documents") {
    const targets = lastFiles
      .map((file, index) => ({ index, source: file.filename || file.title, markdown: file.markdown }))
      .filter((target) => target.markdown);
    if (targets.length) return targets;
  }
  return lastMarkdown ? [{ index: activeResult, source: lastSource, markdown: lastMarkdown }] : [];
}

/** Say on the button how many documents it is about to analyse. */
function updateKeywordsButton() {
  if (!el.keywordsBtn) return;
  const targets = keywordTargets();
  el.keywordsBtn.disabled = !targets.length;
  el.keywordsBtn.textContent = targets.length > 1 ? `Keywords (${targets.length})` : "Keywords";
}

/** Ask the backend for the keywords of the current target — one document or all. */
async function runKeywordExtraction() {
  // The documents as they were *before* any table was added, so re-running with
  // a different method set never analyses the previous run's own output.
  const targets = keywordState
    ? keywordState.documents.map((entry) => ({
        index: entry.index,
        source: entry.source,
        markdown: entry.original,
      }))
    : keywordTargets();
  if (!targets.length) return;

  const methods = selectedKeywordMethods();
  const wasApplied = Boolean(keywordState && keywordState.applied);
  const shown = keywordState ? keywordState.shown : 0;
  const slow = methods.includes("keybert");
  const batch = targets.length > 1;

  const idleLabel = el.keywordsBtn.textContent;
  el.keywordsBtn.disabled = true;
  el.keywordsBtn.textContent = "Extracting…";
  // Documents are analysed one at a time, so the estimate adds up — with
  // KeyBERT selected, a dozen documents is minutes, and a bar that implied
  // otherwise would look broken rather than busy.
  const each = slow ? KEYWORD_ESTIMATES.semantic : KEYWORD_ESTIMATES.fast;
  startProgress(each * targets.length, "pymupdf");
  el.progressLabel.textContent = slow
    ? "Extracting keywords — KeyBERT may be loading its model."
    : `Extracting keywords${batch ? ` from ${targets.length} documents` : ""}…`;

  try {
    const documents = batch
      ? await extractBatch(targets, methods)
      : await extractOne(targets[0], methods);
    keywordState = { documents, applied: false, shown: Math.min(shown, documents.length - 1) };
    showKeywords();
    // A re-run while the tables were showing keeps them showing, now updated.
    if (wasApplied) applyKeywords(true);
  } catch (err) {
    clearKeywords();
    el.keywords.hidden = false;
    el.keywordsMeta.textContent = "";
    setKeywordNote(`⚠ ${err.message}`);
  } finally {
    stopProgress();
    el.keywordsBtn.textContent = idleLabel;
    updateKeywordsButton();
  }
}

/** The common request options. `prepend_table` is always on: it is what "Add to
 *  Markdown" pastes in, so asking now means that button costs no round-trip. */
function keywordRequestBody(methods) {
  return { prepend_table: true, top_k: 25, methods: methods.length ? methods : undefined };
}

async function extractOne(target, methods) {
  const res = await fetch(`${API_BASE}/keywords`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...keywordRequestBody(methods),
      markdown: target.markdown,
      source: target.source || undefined,
    }),
  });
  const data = await parseResponse(res);
  return [
    {
      index: target.index,
      source: target.source,
      original: target.markdown,
      annotated: data.markdown || target.markdown,
      data,
    },
  ];
}

/**
 * Every document in one request (ADR-033) — not a loop of single calls, which
 * would spend one rate-limit token per document against a `20/minute` route and
 * leave the user with keywords for the first half of their folder.
 */
async function extractBatch(targets, methods) {
  const res = await fetch(`${API_BASE}/keywords/batch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...keywordRequestBody(methods),
      documents: targets.map((target) => ({ markdown: target.markdown, source: target.source })),
    }),
  });
  const data = await parseResponse(res);
  const results = Array.isArray(data.results) ? data.results : [];
  return targets.map((target, position) => {
    const item = results[position] || { status: "error", error: "No result returned.", keywords: [] };
    return {
      index: target.index,
      source: target.source,
      original: target.markdown,
      // A document that failed keeps its own Markdown, so "Add to Markdown"
      // cannot blank it out on the way past.
      annotated: item.markdown || target.markdown,
      data: item,
    };
  });
}

/** Show the keywords of the document currently selected in the panel. */
function showKeywords() {
  if (!keywordState) return;
  const entries = keywordState.documents;
  const entry = entries[keywordState.shown] || entries[0];
  const data = entry.data;
  const keywords = Array.isArray(data.keywords) ? data.keywords : [];
  const batch = entries.length > 1;

  const parts = [];
  if (batch) parts.push(entry.source || `document ${keywordState.shown + 1}`);
  parts.push(
    keywords.length ? `${keywords.length} keyword${keywords.length === 1 ? "" : "s"}` : "nothing ranked"
  );
  if ((data.methods_used || []).length) parts.push(data.methods_used.join(", "));
  el.keywordsMeta.textContent = parts.join(" · ");

  const notes = [];
  if (batch) {
    const failed = entries.filter((other) => other.data.status === "error").length;
    notes.push(
      `${entries.length} documents analysed${failed ? `, ${failed} failed` : ""} — ` +
        "click a document above to see its keywords."
    );
  }
  if (data.error) notes.push(`⚠ ${data.error}`);
  if (data.note) notes.push(data.note);
  // A method that was asked for and could not run has to be said out loud: the
  // request succeeds either way, so silence would read as "spaCy ran".
  const skipped = Object.keys(data.methods_skipped || {});
  if (skipped.length) {
    notes.push(`Not run: ${skipped.map((m) => `${m} (${data.methods_skipped[m]})`).join("; ")}`);
  }
  setKeywordNote(notes.join(" "));

  el.keywordsRows.replaceChildren(...keywords.map(keywordRow));
  // The buttons act on the whole set, so they stay live as long as *any*
  // document produced keywords — not only the one on screen.
  const anyKeywords = entries.some((other) => (other.data.keywords || []).length);
  el.keywordsJsonBtn.disabled = !anyKeywords;
  el.keywordsApplyBtn.disabled = !anyKeywords;
  el.keywordsJsonBtn.textContent = batch ? "Download all .json" : "Download .json";
  el.keywords.hidden = false;
}

/** Point the panel at the keywords of one results row, if they were extracted. */
function showKeywordsFor(index) {
  if (!keywordState) return false;
  const position = keywordState.documents.findIndex((entry) => entry.index === index);
  if (position < 0) return false;
  keywordState.shown = position;
  showKeywords();
  return true;
}

function setKeywordNote(text) {
  el.keywordsNote.textContent = text || "";
  el.keywordsNote.hidden = !text;
}

/**
 * One row of the table.
 * Built as DOM nodes, not markup: a keyword is a phrase lifted out of an
 * arbitrary document, so it is only ever assigned as `textContent`.
 */
function keywordRow(keyword) {
  const row = document.createElement("tr");

  const term = document.createElement("td");
  const label = document.createElement("span");
  label.className = "keywords__term";
  label.textContent = keyword.term;
  term.append(label);
  if (keyword.kind === "entity") {
    const tag = document.createElement("span");
    tag.className = "keywords__entity";
    tag.textContent = "entity";
    term.append(tag);
  }

  const weight = document.createElement("td");
  weight.className = "num";
  const wrap = document.createElement("span");
  wrap.className = "keywords__weight";
  const bar = document.createElement("span");
  bar.className = "keywords__bar";
  bar.style.width = `${Math.max(2, Math.round((keyword.score || 0) * 34))}px`;
  const value = document.createElement("span");
  value.textContent = (keyword.score || 0).toFixed(2);
  wrap.append(bar, value);
  weight.append(wrap);

  const count = document.createElement("td");
  count.className = "num";
  count.textContent = (keyword.occurrences || 0).toLocaleString();

  const found = document.createElement("td");
  found.className = "keywords__found";
  const methods = Object.keys(keyword.methods || {});
  found.textContent = methods.join(", ");
  // The agreement count is the confidence signal, so it is the row's tooltip
  // rather than another column: four methods agreeing is a different thing
  // from one method's first place.
  found.title = methods
    .map((name) => `${name}: rank ${keyword.methods[name].rank} (score ${keyword.methods[name].score})`)
    .join("\n");

  row.append(term, weight, count, found);
  return row;
}

/**
 * The keywords as a JSON sidecar — the API's own answer, minus the documents.
 * A batch saves **one** file rather than one per document: it is a single click,
 * every entry is keyed by the same filename the `.md` carries, and a folder of
 * twenty sidecars is harder to read than one list of twenty.
 */
el.keywordsJsonBtn.addEventListener("click", () => {
  if (!keywordState) return;
  const entries = keywordState.documents;
  // No timestamp: two extractions of the same document must produce identical
  // files, the same reason the ZIP writer pins its dates (ADR-030).
  const strip = (entry) => {
    const { markdown, ...rest } = entry.data;
    return rest;
  };
  if (entries.length === 1) {
    const stem = filenameFor(suggestedTitle()).replace(/\.md$/i, "");
    saveJson(strip(entries[0]), `${stem}.keywords.json`);
    return;
  }
  saveJson(
    {
      count: entries.length,
      succeeded: entries.filter((entry) => entry.data.status !== "error").length,
      failed: entries.filter((entry) => entry.data.status === "error").length,
      documents: entries.map(strip),
    },
    "keywords.json"
  );
});

function saveJson(payload, filename) {
  saveBlob(new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: "application/json" }), filename);
}

el.keywordsApplyBtn.addEventListener("click", () => applyKeywords(!keywordState || !keywordState.applied));

/**
 * Put each document's keyword table into that document, or take it back out.
 * The tables are the backend's (`prepend_table`), not a second renderer here, so
 * what a user saves is byte-for-byte what an agent asking the same question
 * receives. For a batch this rewrites every document, so the archive and each
 * row's own Save carry their own table.
 */
function applyKeywords(apply) {
  if (!keywordState) return;
  keywordState.applied = apply;
  keywordState.documents.forEach((entry) => {
    const markdown = apply ? entry.annotated : entry.original;
    if (entry.index >= 0 && lastFiles[entry.index]) {
      lastFiles[entry.index].markdown = markdown;
      lastFiles[entry.index].length = markdown.length;
    } else {
      documentView.markdown = markdown;
    }
  });
  // Re-show whatever the output panel was on, now that its Markdown has changed.
  const current =
    activeResult >= 0 && lastFiles[activeResult] ? lastFiles[activeResult].markdown : documentView.markdown;
  showMarkdown(current, el.outputMeta.textContent, lastSource);
  el.keywordsApplyBtn.textContent = apply ? "Remove from Markdown" : "Add to Markdown";
}

el.keywordsBtn.addEventListener("click", runKeywordExtraction);

// --- Copy --------------------------------------------------------------------
el.copyBtn.addEventListener("click", async () => {
  if (!lastMarkdown) return;
  await navigator.clipboard.writeText(lastMarkdown);
  el.copyBtn.textContent = "Copied!";
  setTimeout(() => (el.copyBtn.textContent = "Copy"), 1500);
});

// --- Download (title-first) --------------------------------------------------
/** A document's own title: first `#`, else first `##`, else its source name. */
function titleFor(markdown, source) {
  const heading = window.wiseauMarkdown.firstHeading(markdown);
  if (heading) return heading;
  if (!source) return "converted";
  const trimmed = source.replace(/[?#].*$/, "").replace(/\/+$/, "");
  const tail = trimmed.split("/").pop() || trimmed;
  return tail.replace(/\.[a-z0-9]{1,5}$/i, "") || "converted";
}

/** The title of whatever the output panel is currently showing. */
function suggestedTitle() {
  return titleFor(lastMarkdown, lastSource);
}

/** Turn a human title into a filename that every OS will accept. */
function filenameFor(title) {
  const stem =
    String(title || "")
      // Path separators, Windows-reserved characters and control codes out.
      .replace(/[\\/:*?"<>|]+/g, " ")
      .replace(/[\u0000-\u001f]+/g, " ")
      .trim()
      .replace(/\s+/g, "-")
      .replace(/-{2,}/g, "-")
      .replace(/^[.\-]+|[.\-]+$/g, "")
      .slice(0, 80) || "converted";
  return /\.md$/i.test(stem) ? stem : `${stem}.md`;
}

function saveBlob(blob, filename) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function saveText(text, filename) {
  saveBlob(new Blob([text], { type: "text/markdown" }), filename);
}

function saveMarkdown(filename) {
  saveText(lastMarkdown, filename);
}

el.downloadTitle.addEventListener("input", () => {
  el.downloadFilename.textContent = filenameFor(el.downloadTitle.value);
});

el.downloadDialog.addEventListener("close", () => {
  if (el.downloadDialog.returnValue !== "confirm") return;
  saveMarkdown(filenameFor(el.downloadTitle.value));
});

el.downloadBtn.addEventListener("click", () => {
  if (!lastMarkdown) return;
  const title = suggestedTitle();

  // `<dialog>` is the modern modal; if it is unavailable, still let the user
  // amend the title rather than dropping the feature.
  if (typeof el.downloadDialog.showModal !== "function") {
    const answer = window.prompt("Title for the downloaded file", title);
    if (answer !== null) saveMarkdown(filenameFor(answer));
    return;
  }

  el.downloadTitle.value = title;
  el.downloadFilename.textContent = filenameFor(title);
  el.downloadDialog.returnValue = "";
  el.downloadDialog.showModal();
  el.downloadTitle.focus();
  el.downloadTitle.select();
});

// --- Init --------------------------------------------------------------------
setView(currentView);
renderKeywordMethods();
pingBackend();
