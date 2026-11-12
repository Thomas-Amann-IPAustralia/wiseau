/*
 * Wiseau ("Oh hi Mark(down)") — frontend application logic.
 *
 * State management + fetch calls to the FastAPI backend. Set API_BASE to your
 * deployed Hugging Face Space URL (e.g. "https://your-space.hf.space").
 */

// --- Configuration ----------------------------------------------------------
const API_BASE = (window.MARKDOWN_API_BASE || "http://localhost:7860").replace(/\/$/, "");

// Conversion-time model, in seconds (ADR-057). The API reports no progress (a
// conversion is one blocking call), so the bar is an *estimate* — but from what
// actually drives the time: how many pages a PDF has and whether they are
// scans (pdfprobe.js reads that in the browser before upload), not file size.
// A 40-page scan OCRs for minutes; a 40-page report converts in a second; the
// old size-based estimate could not tell them apart, so on exactly the long
// documents the bar raced to 99% and then looked hung.
//
// Per-page costs are starting points from the benchmarks (docs/benchmarks/
// 2026-11-11-gpu-e2e.md; 22 s a warm scanned page on the live service before
// ADR-056's overlap). Every finished conversion then corrects them for the
// deployment actually in use — see `calibration` below.
const MODEL = {
  url: { base: 8 },
  // A PDF link: the render and the download, then a document of unknown length.
  urlPdf: { base: 14 },
  pymupdf: { base: 2, textPage: 0.25, scanPage: 16, docxPerMb: 1, unknownPerMb: 6 },
  // docling on a free CPU: an ML pipeline, plus a possible cold start.
  docling: { base: 25, textPage: 4, scanPage: 40, docxPerMb: 6, unknownPerMb: 25 },
  // GLM-OCR on CPU: minutes a scanned page (ADR-034); text pages skip it (ADR-036).
  "glm-ocr": { base: 60, textPage: 0.25, scanPage: 150, docxPerMb: 1, unknownPerMb: 300 },
};

// The longest a single request may run before the hosting platform cuts it off
// (Cloud Run's request timeout, docs/hosting.md: 300 s). A deployment with a
// different limit can say so in config.js: `window.WISEAU_REQUEST_TIMEOUT_S`.
const SERVER_TIMEOUT_S = Number(window.WISEAU_REQUEST_TIMEOUT_S) || 300;

// Upload speed assumed until this browser has measured one.
const DEFAULT_UPLOAD_BYTES_PER_S = 1.5 * 1024 * 1024;

// Engines that run as a separate service and may not exist on a deployment.
const REMOTE_ENGINES = ["docling", "glm-ocr"];

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
  readerMode: document.getElementById("reader-mode"),
  bundleBtn: document.getElementById("bundle-btn"),
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
  progressEta: document.getElementById("progress-eta"),
  progressWarning: document.getElementById("progress-warning"),
  pipelineTrack: document.getElementById("pipeline-track"),
  pipelineDetail: document.getElementById("pipeline-detail"),
  pipelineSummary: document.getElementById("pipeline-summary"),
  apiDocsLink: document.getElementById("api-docs-link"),
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
// The last single upload that converted, and how: what "Save with figures"
// sends again to /convert/bundle (ADR-052). Null after a batch, a URL or a
// failure, which hides the button.
let lastUpload = null;
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
let activeTab = "url";
el.tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    el.tabs.forEach((t) => t.classList.remove("tab--active"));
    tab.classList.add("tab--active");
    Object.values(el.panes).forEach((p) => p.classList.remove("tab-pane--active"));
    el.panes[tab.dataset.tab].classList.add("tab-pane--active");
    activeTab = tab.dataset.tab;
    updateRoute();
  });
});

// --- The pipeline view (pipeline.js) -----------------------------------------
// Redrawn whenever what the conversion would do changes: the source, the files
// and what their probes found, the engine, or an option.
let routeSignature = "";

function currentRoute() {
  return window.wiseauPipeline.buildRoute({
    source: activeTab,
    url: el.urlInput.value.trim(),
    files: selectedFiles.map((file) => ({ name: file.name, type: fileType(file), probe: probes.get(file) })),
    engine: effectiveEngine(selectedEngine()),
    chapters: wantsChapters(),
    reader: activeTab === "file" && wantsReader(),
  });
}

function updateRoute() {
  if (!window.wiseauPipeline) return;
  const route = currentRoute();
  const signature = JSON.stringify([route.label, route.engine, route.notes, route.stages.map((s) => s.id + s.sub)]);
  if (signature === routeSignature) return;
  routeSignature = signature;
  window.wiseauPipeline.show(route);
}

// --- Status badge ------------------------------------------------------------
async function pingBackend() {
  setBadge("unknown", "checking…");
  try {
    const res = await fetch(`${API_BASE}/ping`, { method: "GET" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const info = await res.json();
    setBadge("online", info && info.version ? `online · v${info.version}` : "online");
    applyAvailableEngines(info);
    applyServerDefaultEngine(info);
    applyKeywordMethods(info);
    updateRoute();
  } catch {
    setBadge("offline", "offline");
  }
}

// /ping reports which engine "auto" resolves to, so the Auto option can say what
// it will actually do instead of guessing (ADR-027).
function applyServerDefaultEngine(info) {
  const reported = info && typeof info.default_engine === "string" ? info.default_engine : "";
  if (reported !== "pymupdf" && !REMOTE_ENGINES.includes(reported)) return;
  serverDefaultEngine = reported;
  if (el.engineAutoHint) {
    el.engineAutoHint.textContent =
      reported === "pymupdf" ? "Server's default — fastest" : `Server's default — ${reported}`;
  }
}

// /ping lists the engines this deployment can actually run (ADR-034). A remote
// engine whose service is not deployed would be accepted and then silently fall
// back, so its option is hidden rather than offered. A server too old to send
// the list keeps the original choices: docling shown, GLM-OCR hidden.
function applyAvailableEngines(info) {
  const listed = info && Array.isArray(info.available_engines) ? info.available_engines : null;
  el.engineInputs.forEach((input) => {
    if (!REMOTE_ENGINES.includes(input.value)) return;
    const available = listed ? listed.includes(input.value) : input.value === "docling";
    const option = input.closest(".engine__option");
    if (option) option.hidden = !available;
    if (!available && input.checked) {
      input.checked = false;
      const auto = Array.from(el.engineInputs).find((other) => other.value === "auto");
      if (auto) auto.checked = true;
    }
  });
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

/** Reader mode (ADR-055): leave page furniture out. Sent on every upload, so the
 * box means what it shows whatever the deployment's default is. */
function wantsReader() {
  return Boolean(el.readerMode && el.readerMode.checked);
}

/** Show "Save with figures" only after a single PDF converted. */
function setLastUpload(upload) {
  lastUpload = upload;
  if (el.bundleBtn) el.bundleBtn.hidden = !upload;
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

// --- What is selected, and what it will cost -----------------------------------
/** The engine that will actually run: "auto" is whatever the server defaults to. */
function effectiveEngine(engine) {
  return engine === "auto" ? serverDefaultEngine : engine;
}

function fileType(file) {
  const name = file.name.toLowerCase();
  if (name.endsWith(".pdf")) return "pdf";
  if (name.endsWith(".docx")) return "docx";
  if (IMAGE_EXTENSIONS.some((ext) => name.endsWith(ext))) return "image";
  return "other";
}

// What pdfprobe.js found in each selected PDF: { pages, kind, confidence } or null.
const probes = new WeakMap();
const probing = new WeakMap();

function probeFile(file) {
  if (fileType(file) !== "pdf" || !window.wiseauPdfProbe) return Promise.resolve(null);
  if (!probing.has(file)) {
    probing.set(
      file,
      window.wiseauPdfProbe.probe(file).then((probe) => {
        probes.set(file, probe);
        return probe;
      })
    );
  }
  return probing.get(file);
}

/** Wait for the selection's probes, but never hold a conversion up for long. */
function probesReady(files) {
  const all = Promise.all(files.map(probeFile));
  const cap = new Promise((resolve) => setTimeout(resolve, 2500));
  return Promise.race([all, cap]);
}

/**
 * Seconds one document is expected to take, as raw model seconds split by
 * calibration profile (`engine:kind`), so a finished conversion can correct the
 * part of the model it exercised. `seconds` already includes the correction.
 */
function estimateFile(file, engine) {
  const effective = effectiveEngine(engine);
  const model = MODEL[effective] || MODEL.docling;
  const type = fileType(file);
  const megabytes = file.size / (1024 * 1024);
  const parts = {};
  const add = (kind, seconds) => {
    const key = `${effective}:${kind}`;
    parts[key] = (parts[key] || 0) + seconds;
  };

  if (type === "docx") {
    add("docx", model.base + model.docxPerMb * megabytes);
  } else if (type === "image") {
    add("scan", model.base + model.scanPage);
  } else {
    const probe = probes.get(file);
    if (!probe) add("unknown", model.base + model.unknownPerMb * megabytes);
    else if (probe.kind === "scan") add("scan", model.base + model.scanPage * probe.pages);
    else add("text", model.base + model.textPage * probe.pages);
  }
  return corrected(parts);
}

function estimateUrl(url) {
  const pdf = /\.pdf($|[?#])/i.test(url);
  return corrected(pdf ? { "url:pdf": MODEL.urlPdf.base } : { "url:html": MODEL.url.base });
}

/**
 * The whole selection: a batch converts its documents one at a time, so the
 * estimates add up — with docling, ten documents is minutes, and a bar that
 * implied otherwise would look broken rather than busy.
 */
function estimateFiles(files, engine) {
  const parts = {};
  files.forEach((file) => {
    const each = estimateFile(file, engine).parts;
    Object.keys(each).forEach((key) => (parts[key] = (parts[key] || 0) + each[key]));
  });
  return corrected(parts);
}

function corrected(parts) {
  const seconds = Object.keys(parts).reduce((sum, key) => sum + parts[key] * calibration.factor(key), 0);
  return { seconds, parts };
}

/**
 * What this browser has learned about the deployment in front of it (ADR-057):
 * one correction factor per profile and the upload speed, kept per API base in
 * localStorage. The model's starting numbers come from benchmark machines; the
 * live service may have fewer CPUs, a cold start, or a faster network, and only
 * finished conversions can say so. Storage can be unavailable (private
 * windows), so every access is guarded and the model works without it.
 */
const calibration = (() => {
  const KEY = "wiseau.calibration.v1";
  const read = () => {
    try {
      return JSON.parse(localStorage.getItem(KEY)) || {};
    } catch {
      return {};
    }
  };
  const write = (data) => {
    try {
      localStorage.setItem(KEY, JSON.stringify(data));
    } catch {
      /* storage unavailable: estimates simply stay uncalibrated */
    }
  };
  const scoped = (data) => (data[API_BASE] = data[API_BASE] || {});

  return {
    factor(profile) {
      const entry = scoped(read())[profile];
      return entry && Number.isFinite(entry.f) ? entry.f : 1;
    },
    /** A conversion finished: move each profile it used toward what happened. */
    record(parts, observedSeconds) {
      const data = read();
      const scope = scoped(data);
      const factor = (key) => (scope[key] && Number.isFinite(scope[key].f) ? scope[key].f : 1);
      const predicted = Object.keys(parts).reduce((sum, key) => sum + parts[key] * factor(key), 0);
      if (!(predicted > 0) || !(observedSeconds > 0)) return;
      if (predicted < 2 && observedSeconds < 2) return; // noise, not signal
      const ratio = observedSeconds / predicted;
      Object.keys(parts).forEach((key) => {
        const share = (parts[key] * factor(key)) / predicted;
        // Move part of the way, by the profile's share of the job, and at most
        // halve or double per conversion: one cold start or one unusual file
        // must not throw every later estimate off.
        const step = Math.min(2, Math.max(0.5, Math.pow(ratio, 0.5 * share)));
        const next = factor(key) * step;
        scope[key] = { f: Math.min(8, Math.max(0.15, next)), n: ((scope[key] && scope[key].n) || 0) + 1 };
      });
      write(data);
    },
    uploadRate() {
      const rate = scoped(read()).upload;
      return rate && rate.bps > 0 ? rate.bps : DEFAULT_UPLOAD_BYTES_PER_S;
    },
    recordUpload(bytes, seconds) {
      if (bytes < 256 * 1024 || !(seconds > 0.05)) return;
      const data = read();
      const scope = scoped(data);
      const previous = scope.upload && scope.upload.bps > 0 ? scope.upload.bps : bytes / seconds;
      scope.upload = { bps: previous * 0.5 + (bytes / seconds) * 0.5 };
      write(data);
    },
  };
})();

// --- The progress view -------------------------------------------------------------
// Two phases, honestly labelled. The *upload* is measured (XHR upload events);
// the *processing* advances on the estimate — linearly, so the bar's position
// means "this share of the expected time has passed" — then slows near the end
// instead of parking, switches to "over the estimate" when it runs long, and a
// shimmer keeps moving the whole time so a long wait never looks frozen.
const progressState = { timer: null };

function startProgress(options) {
  const opts = options || {};
  const estimate = Math.max(1, opts.estimate || 1);
  const uploadBytes = opts.uploadBytes || 0;
  const uploadEstimate = uploadBytes ? uploadBytes / calibration.uploadRate() : 0;
  stopTimer();
  Object.assign(progressState, {
    started: Date.now(),
    estimate,
    uploadBytes,
    uploadLoaded: 0,
    uploadEstimate,
    // The upload's share of the bar is its share of the expected wait.
    uploadShare: uploadBytes ? Math.min(0.6, uploadEstimate / (uploadEstimate + estimate)) : 0,
    uploading: uploadBytes > 0,
    processStarted: uploadBytes > 0 ? null : Date.now(),
    label: opts.label || "Converting",
    slowEngine: opts.slowEngine || null,
    pipeline: Boolean(opts.pipeline),
    fill: 0,
  });

  el.progress.hidden = false;
  el.progress.classList.remove("progress--done", "progress--error", "progress--over");
  el.progressFill.style.width = "0%";
  el.progressEta.textContent = "";
  el.progressTimer.textContent = "0s";
  setProgressWarning(opts.warning || "");
  if (progressState.pipeline) {
    window.wiseauPipeline.start();
    // The pipeline is the progress view: bring it into sight if the Convert
    // button left it scrolled away.
    const card = el.progress.closest(".pipeline");
    if (card && card.getBoundingClientRect().top < 0) card.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  tickProgress();
  progressState.timer = setInterval(tickProgress, 200);
}

function setProgressWarning(text) {
  if (!el.progressWarning) return;
  el.progressWarning.textContent = text;
  el.progressWarning.hidden = !text;
}

/** Bytes sent so far (from XHR upload progress events). */
function progressUpload(loaded, total) {
  progressState.uploadLoaded = loaded;
  if (total) progressState.uploadBytes = total;
}

/** The request body is all sent: the server is working now. */
function progressUploaded() {
  if (!progressState.uploading) return;
  progressState.uploading = false;
  progressState.processStarted = Date.now();
  calibration.recordUpload(progressState.uploadBytes, (Date.now() - progressState.started) / 1000);
}

/** Seconds the server spent on the job, counted from the end of the upload. */
function processingSeconds() {
  return progressState.processStarted ? (Date.now() - progressState.processStarted) / 1000 : 0;
}

/** Share of the processing bar for a share of the estimated time. */
function processCurve(x) {
  if (x <= 0.9) return x;
  return 0.9 + 0.08 * (1 - Math.exp(-(x - 0.9) * 2.5));
}

function tickProgress() {
  const s = progressState;
  const elapsed = (Date.now() - s.started) / 1000;
  el.progressTimer.textContent = formatSeconds(elapsed);
  let fill;
  let stage = null;

  if (s.uploading) {
    const fraction = s.uploadBytes ? Math.min(1, s.uploadLoaded / s.uploadBytes) : 0;
    fill = s.uploadShare * fraction;
    const rate = elapsed > 0.5 && s.uploadLoaded > 0 ? s.uploadLoaded / elapsed : calibration.uploadRate();
    const uploadLeft = Math.max(0, (s.uploadBytes - s.uploadLoaded) / rate);
    el.progressLabel.textContent = `Uploading — ${formatBytes(s.uploadLoaded)} of ${formatBytes(s.uploadBytes)}`;
    el.progressEta.textContent = `≈ ${formatSeconds(uploadLeft + s.estimate)} left`;
    if (s.pipeline) window.wiseauPipeline.update({ upload: fraction });
  } else {
    const processed = processingSeconds();
    const x = processed / s.estimate;
    fill = s.uploadShare + (1 - s.uploadShare) * processCurve(x);
    if (s.pipeline) stage = window.wiseauPipeline.update({ upload: null, process: Math.min(x, 0.999) });
    const over = processed - s.estimate;
    el.progress.classList.toggle("progress--over", over > 0);
    if (over <= 0) {
      el.progressEta.textContent = `≈ ${formatSeconds(-over)} left`;
      el.progressLabel.textContent = stage ? `${s.label} — ${stage.title.toLowerCase()} (estimated stage)` : `${s.label}…`;
    } else {
      el.progressEta.textContent = `${formatSeconds(over)} over estimate`;
      el.progressLabel.textContent = s.slowEngine
        ? `Still working — ${s.slowEngine} may be starting up from cold.`
        : "Still working — taking longer than estimated. The server is busy, not stuck.";
    }
    if (elapsed > SERVER_TIMEOUT_S * 0.95 && el.progressWarning.hidden) {
      setProgressWarning(
        `This request has passed ${formatSeconds(SERVER_TIMEOUT_S)}, the hosting platform's usual limit for one request. ` +
          "If no result arrives, the document is too long to convert in one go here — split it into smaller files."
      );
    }
  }

  // The bar never moves backwards, whatever the estimate does.
  s.fill = Math.max(s.fill, Math.min(fill, 0.985));
  el.progressFill.style.width = `${(s.fill * 100).toFixed(1)}%`;
}

function stopTimer() {
  if (progressState.timer !== null) clearInterval(progressState.timer);
  progressState.timer = null;
}

/** The response is in (or the request failed). Returns the processing seconds. */
function stopProgress(ok = true) {
  const processed = progressState.uploading ? 0 : processingSeconds();
  stopTimer();
  el.progressFill.style.width = "100%";
  el.progress.classList.remove("progress--over");
  el.progress.classList.add(ok ? "progress--done" : "progress--error");
  el.progressEta.textContent = "";
  if (progressState.pipeline) {
    window.wiseauPipeline.finish(ok);
    scheduleRouteReset();
  }
  const finishedAt = Date.now();
  progressState.finishedAt = finishedAt;
  // Let the filled bar register before it disappears.
  setTimeout(() => {
    if (progressState.timer === null && progressState.finishedAt === finishedAt) {
      el.progress.hidden = true;
      setProgressWarning("");
    }
  }, ok ? 900 : 5000);
  return processed;
}

let routeResetTimer = null;
function scheduleRouteReset() {
  clearTimeout(routeResetTimer);
  routeResetTimer = setTimeout(() => window.wiseauPipeline.reset(), 9000);
}

/** A warning shown up front when the estimate is past the platform's limit. */
function timeoutWarning(estimateSeconds) {
  if (estimateSeconds < SERVER_TIMEOUT_S * 0.85) return "";
  return (
    `Estimated ${formatSeconds(estimateSeconds)} — longer than the ${formatSeconds(SERVER_TIMEOUT_S)} ` +
    "a single request may usually run on this service, so it may be cut off before it finishes. " +
    "Splitting the document into smaller files, or choosing the Fastest engine, avoids that."
  );
}

function formatSeconds(seconds) {
  const rounded = Math.max(0, Math.round(seconds));
  if (rounded < 60) return `${rounded}s`;
  return `${Math.floor(rounded / 60)}m ${String(rounded % 60).padStart(2, "0")}s`;
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(0, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// --- Requests -------------------------------------------------------------------
/**
 * POST a form with XMLHttpRequest — the one browser API that reports upload
 * progress — and hand back a standard `Response`, so the rest of the code reads
 * it exactly as it reads a `fetch`.
 */
function postWithUpload(url, form) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.responseType = "blob";
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) progressUpload(event.loaded, event.total);
    });
    xhr.upload.addEventListener("load", progressUploaded);
    xhr.addEventListener("load", () => {
      progressUploaded();
      if (!xhr.status) return reject(new Error(networkFailure(started)));
      try {
        const headers = { "Content-Type": xhr.getResponseHeader("Content-Type") || "" };
        resolve(new Response(xhr.response, { status: xhr.status, headers }));
      } catch {
        reject(new Error(`Request failed (HTTP ${xhr.status})`));
      }
    });
    xhr.addEventListener("error", () => reject(new Error(networkFailure(started))));
    xhr.addEventListener("abort", () => reject(new Error("The request was cancelled.")));
    xhr.send(form);
  });
}

/** Say why a request died without an answer, as specifically as we can. */
function networkFailure(started) {
  const elapsed = (Date.now() - started) / 1000;
  if (elapsed >= SERVER_TIMEOUT_S * 0.9) return timeoutMessage();
  return (
    "The connection to the backend closed before a response arrived — it may be offline, " +
    "restarting, or out of memory on a very large document. Try again, or try a smaller file."
  );
}

function timeoutMessage() {
  return (
    `The server stopped this request at its time limit (about ${formatSeconds(SERVER_TIMEOUT_S)}). ` +
    "The document takes longer than that to convert here — split it into smaller files, or use the Fastest engine."
  );
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
  setLastUpload(null);
  showFailure(message);
}

function setBusy(button, busy, idleLabel) {
  button.disabled = busy;
  button.textContent = busy ? "Converting…" : idleLabel;
  button.classList.toggle("btn--busy", busy);
  el.engineInputs.forEach((input) => (input.disabled = busy));
  if (el.readerMode) el.readerMode.disabled = busy;
  if (el.splitChapters) {
    if (busy) el.splitChapters.disabled = true;
    else applyChapterAvailability();
  }
}

async function parseResponse(res) {
  const payload = await res.json().catch(() => ({}));
  if (res.status === 504 || res.status === 408) throw new Error(timeoutMessage());
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
  const estimate = estimateUrl(url);
  updateRoute();
  setBusy(el.convertUrlBtn, true, "Convert");
  startProgress({ estimate: estimate.seconds, label: "Converting", pipeline: true });
  const started = Date.now();
  let ok = false;
  try {
    let res;
    try {
      res = await fetch(`${API_BASE}/convert/url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, engine, split_chapters: wantsChapters() }),
      });
    } catch {
      throw new Error(networkFailure(started));
    }
    const data = await parseResponse(res);
    ok = true;
    showConverted(data, url);
    setLastUpload(null);
  } catch (err) {
    showError(err.message);
  } finally {
    const seconds = stopProgress(ok);
    if (ok) calibration.record(estimate.parts, seconds);
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
  updateRoute();
  // Look inside each PDF (pages, scanned or not) for the route and the estimate.
  const current = selectedFiles;
  Promise.all(current.map(probeFile)).then(() => {
    if (current !== selectedFiles) return;
    el.fileName.replaceChildren(...selectionLabel(selectedFiles));
    updateRoute();
  });
}

/**
 * Name what is selected. Filenames are text the user's filesystem supplied, so
 * they are set as `textContent`; a long list is truncated because the drop zone
 * is not a file manager.
 */
function selectionLabel(files) {
  const shown = files.slice(0, 4).map((file) => {
    const line = document.createElement("span");
    line.className = "file-line";
    const name = document.createElement("span");
    name.className = "file-name";
    name.textContent = file.name;
    const facts = document.createElement("span");
    facts.className = "file-probe";
    facts.textContent = describeFile(file);
    line.append(name, facts);
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

/** "36 pages · likely scanned · 4.2 MB" — what the estimate is based on. */
function describeFile(file) {
  const parts = [];
  const probe = probes.get(file);
  if (probe) {
    parts.push(`${probe.pages} page${probe.pages === 1 ? "" : "s"}`);
    const scanned = probe.kind === "scan";
    parts.push(probe.confidence === "high" ? (scanned ? "scanned" : "digital") : scanned ? "likely scanned" : "likely digital");
  } else if (fileType(file) === "image") {
    parts.push("image · OCR");
  }
  parts.push(formatBytes(file.size));
  return parts.join(" · ");
}

// --- File conversion ---------------------------------------------------------
// One file goes to /convert/file and may be split into chapters; several go to
// /convert/batch and come back as one Markdown file each (ADR-031). The two are
// mutually exclusive, which is what keeps this a fork rather than a matrix.
el.convertFileBtn.addEventListener("click", async () => {
  if (!selectedFiles.length) return;
  const engine = selectedEngine();
  const files = selectedFiles;
  const batch = files.length > 1;
  const idleLabel = batch ? `Convert ${files.length} files` : "Convert";
  setBusy(el.convertFileBtn, true, idleLabel);
  await probesReady(files);
  updateRoute();
  const estimate = estimateFiles(files, engine);
  const effective = effectiveEngine(engine);
  startProgress({
    estimate: estimate.seconds,
    uploadBytes: files.reduce((sum, file) => sum + file.size, 0),
    label: batch ? `Converting ${files.length} files` : "Converting",
    slowEngine: REMOTE_ENGINES.includes(effective) ? effective : null,
    warning: timeoutWarning(estimate.seconds),
    pipeline: true,
  });
  let ok = false;
  try {
    const form = new FormData();
    form.append("engine", engine);
    form.append("reader", String(wantsReader()));
    if (batch) {
      selectedFiles.forEach((file) => form.append("files", file));
    } else {
      form.append("file", selectedFiles[0]);
      form.append("split_chapters", String(wantsChapters()));
    }
    const res = await postWithUpload(`${API_BASE}/convert/${batch ? "batch" : "file"}`, form);
    const data = await parseResponse(res);
    ok = true;
    if (batch) showBatch(data);
    else showConverted(data, selectedFiles[0].name);
    const single = !batch && /\.pdf$/i.test(selectedFiles[0].name);
    setLastUpload(single ? { file: selectedFiles[0], engine, reader: wantsReader() } : null);
  } catch (err) {
    showError(err.message);
  } finally {
    const seconds = stopProgress(ok);
    if (ok) calibration.record(estimate.parts, seconds);
    setBusy(el.convertFileBtn, false, idleLabel);
  }
});

// --- Bundle: the Markdown with its figures (ADR-052) ---------------------------
// The bundle is a ZIP the page does not display, so the Markdown shown stays the
// one /convert/file returned; this converts the same file again, the same way,
// and saves what /convert/bundle sends back.
if (el.bundleBtn) {
  el.bundleBtn.addEventListener("click", async () => {
    if (!lastUpload) return;
    const { file, engine, reader } = lastUpload;
    const idle = el.bundleBtn.textContent;
    el.bundleBtn.disabled = true;
    el.bundleBtn.textContent = "Preparing…";
    const estimate = estimateFile(file, engine);
    startProgress({
      estimate: estimate.seconds,
      uploadBytes: file.size,
      label: "Preparing the bundle",
      warning: timeoutWarning(estimate.seconds),
      pipeline: true,
    });
    let ok = false;
    try {
      const form = new FormData();
      form.append("engine", engine);
      form.append("reader", String(reader));
      form.append("file", file);
      const res = await postWithUpload(`${API_BASE}/convert/bundle`, form);
      if (!res.ok) await parseResponse(res); // throws with the API's message
      const name = filenameFor(file.name.replace(/\.pdf$/i, "")).replace(/\.md$/i, "") + ".zip";
      saveBlob(await res.blob(), name);
      ok = true;
    } catch (err) {
      el.outputMeta.textContent = `⚠ ${err.message}`;
    } finally {
      stopProgress(ok);
      el.bundleBtn.disabled = false;
      el.bundleBtn.textContent = idle;
    }
  });
}

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
  startProgress({
    estimate: each * targets.length,
    label: slow
      ? "Extracting keywords — KeyBERT may be loading its model"
      : `Extracting keywords${batch ? ` from ${targets.length} documents` : ""}`,
  });

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
// Inline icons for the static markup (drawn by pipeline.js, so there is one set).
document.querySelectorAll("[data-icon]").forEach((node) => {
  node.innerHTML = window.wiseauPipeline.icon(node.dataset.icon);
});
if (el.apiDocsLink) el.apiDocsLink.href = `${API_BASE}/docs`;

// The drop zone is a button to the keyboard too.
el.dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    el.fileInput.click();
  }
});
el.urlInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !el.convertUrlBtn.disabled) el.convertUrlBtn.click();
});

// Anything that changes the route redraws the pipeline.
el.urlInput.addEventListener("input", updateRoute);
el.engineInputs.forEach((input) => input.addEventListener("change", updateRoute));
[el.splitChapters, el.readerMode].forEach((input) => input && input.addEventListener("change", updateRoute));

window.wiseauPipeline.mount({
  track: el.pipelineTrack,
  detail: el.pipelineDetail,
  summary: el.pipelineSummary,
});
updateRoute();
setView(currentView);
renderKeywordMethods();
pingBackend();
