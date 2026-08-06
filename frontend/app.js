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
  chapters: document.getElementById("chapters"),
  chaptersList: document.getElementById("chapters-list"),
  chaptersMeta: document.getElementById("chapters-meta"),
  chaptersAllBtn: document.getElementById("chapters-all-btn"),
  chaptersZipBtn: document.getElementById("chapters-zip-btn"),
  progress: document.getElementById("progress"),
  progressFill: document.getElementById("progress-fill"),
  progressLabel: document.getElementById("progress-label"),
  progressTimer: document.getElementById("progress-timer"),
  output: document.getElementById("output"),
  outputPreview: document.getElementById("output-preview"),
  viewButtons: document.querySelectorAll(".view-toggle__btn"),
  outputMeta: document.getElementById("output-meta"),
  copyBtn: document.getElementById("copy-btn"),
  downloadBtn: document.getElementById("download-btn"),
  downloadDialog: document.getElementById("download-dialog"),
  downloadTitle: document.getElementById("download-title"),
  downloadFilename: document.getElementById("download-filename"),
  tabs: document.querySelectorAll(".tab"),
  panes: { url: document.getElementById("tab-url"), file: document.getElementById("tab-file") },
};

let selectedFile = null;
// What the backend resolves "auto" to. Assume the standard default (the fast
// parser) until /ping says otherwise; only the estimate depends on it, so a
// deployment that never answers /ping still converts normally.
let serverDefaultEngine = "pymupdf";
let lastMarkdown = "";
let lastSource = "";
let currentView = "preview";
let lastChapters = [];

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
    applyServerDefaultEngine(await res.json());
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
  return Boolean(el.splitChapters && el.splitChapters.checked);
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

function showError(message) {
  lastMarkdown = "";
  clearChapters();
  el.output.classList.add("is-error");
  el.outputPreview.classList.add("is-error");
  el.output.textContent = `⚠ ${message}`;
  el.outputPreview.textContent = `⚠ ${message}`;
  el.outputMeta.textContent = "";
  el.copyBtn.disabled = true;
  el.downloadBtn.disabled = true;
}

function setBusy(button, busy, idleLabel) {
  button.disabled = busy;
  button.textContent = busy ? "Converting…" : idleLabel;
  el.engineInputs.forEach((input) => (input.disabled = busy));
}

async function parseResponse(res) {
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(payload.detail || `Request failed (HTTP ${res.status})`);
  }
  return payload;
}

// --- Chapters ----------------------------------------------------------------
// How the backend found the chapters, said plainly: a split taken from the
// document's own contents page deserves more trust than one guessed from
// headings, and the panel should let the reader judge before saving 30 files.
const DETECTION_LABEL = {
  toc: "found on the document's contents page",
  headings: "found from the document's headings",
  markers: "found from “Chapter N” lines in the text",
};

// The whole document, kept aside so that viewing one chapter is reversible.
let documentView = { markdown: "", meta: "", source: "" };

function clearChapters() {
  lastChapters = [];
  el.chaptersList.replaceChildren();
  el.chapters.hidden = true;
}

function showChapters(data) {
  clearChapters();
  lastChapters = Array.isArray(data.chapters) ? data.chapters : [];
  if (!lastChapters.length) return;

  el.chaptersMeta.textContent = DETECTION_LABEL[data.chapter_detection] || "";
  el.chaptersList.append(...lastChapters.map(chapterRow));
  el.chapters.hidden = false;
}

/**
 * One row of the chapter list.
 * Built as DOM nodes rather than markup: a chapter title is text lifted out of
 * an arbitrary document, so it is only ever assigned as `textContent`.
 */
function chapterRow(chapter, index) {
  const item = document.createElement("li");
  item.className = "chapter";

  const title = document.createElement("span");
  title.className = "chapter__title";
  title.textContent = chapter.title;

  const filename = document.createElement("code");
  filename.className = "chapter__file";
  filename.textContent = chapter.filename;

  const size = document.createElement("span");
  size.className = "chapter__size";
  size.textContent = `${chapter.length.toLocaleString()} chars`;

  const view = document.createElement("button");
  view.className = "btn btn--ghost btn--small";
  view.textContent = "View";
  view.addEventListener("click", () => viewChapter(index));

  const save = document.createElement("button");
  save.className = "btn btn--ghost btn--small";
  save.textContent = "Save";
  save.addEventListener("click", () => saveText(chapter.markdown, chapter.filename));

  const text = document.createElement("span");
  text.className = "chapter__text";
  text.append(title, filename);

  const actions = document.createElement("span");
  actions.className = "chapter__actions";
  actions.append(size, view, save);
  item.append(text, actions);
  return item;
}

/** Show a finished conversion: the document, then its chapters if there are any. */
function showConverted(data, fallbackSource) {
  documentView = {
    markdown: data.markdown,
    meta: metaFor(data),
    source: data.source || fallbackSource,
  };
  showMarkdown(documentView.markdown, documentView.meta, documentView.source);
  showChapters(data);
}

/** Show one chapter in the output panel; Copy and Download then act on it. */
function viewChapter(index) {
  const chapter = lastChapters[index];
  if (!chapter) return;
  showMarkdown(chapter.markdown, `${chapter.length.toLocaleString()} chars · ${chapter.filename}`, chapter.title);
  markActiveChapter(index);
}

function showWholeDocument() {
  showMarkdown(documentView.markdown, documentView.meta, documentView.source);
  markActiveChapter(-1);
}

function markActiveChapter(index) {
  Array.from(el.chaptersList.children).forEach((row, position) =>
    row.classList.toggle("chapter--active", position === index)
  );
}

el.chaptersAllBtn.addEventListener("click", showWholeDocument);

el.chaptersZipBtn.addEventListener("click", () => {
  if (!lastChapters.length) return;
  const archive = window.wiseauZip.build(
    lastChapters.map((chapter) => ({ name: chapter.filename, content: chapter.markdown }))
  );
  const stem = filenameFor(titleFor(documentView.markdown, documentView.source)).replace(/\.md$/i, "");
  saveBlob(archive, `${stem}-chapters.zip`);
});

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
el.fileInput.addEventListener("change", () => setFile(el.fileInput.files[0]));

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
el.dropZone.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

function setFile(file) {
  selectedFile = file || null;
  el.fileName.textContent = file ? file.name : "";
  el.convertFileBtn.disabled = !file;
}

// --- File conversion ---------------------------------------------------------
el.convertFileBtn.addEventListener("click", async () => {
  if (!selectedFile) return;
  const engine = selectedEngine();
  setBusy(el.convertFileBtn, true, "Convert");
  startProgress(estimateSeconds("file", selectedFile, engine), engine);
  try {
    const form = new FormData();
    form.append("file", selectedFile);
    form.append("engine", engine);
    form.append("split_chapters", String(wantsChapters()));
    const res = await fetch(`${API_BASE}/convert/file`, { method: "POST", body: form });
    const data = await parseResponse(res);
    showConverted(data, selectedFile.name);
  } catch (err) {
    showError(err.message);
  } finally {
    stopProgress();
    setBusy(el.convertFileBtn, false, "Convert");
  }
});

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
pingBackend();
