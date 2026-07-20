/*
 * Oh hi Mark(down) — frontend application logic.
 *
 * State management + fetch calls to the FastAPI backend. Set API_BASE to your
 * deployed Hugging Face Space URL (e.g. "https://your-space.hf.space").
 */

// --- Configuration ----------------------------------------------------------
const API_BASE = (window.MARKDOWN_API_BASE || "http://localhost:7860").replace(/\/$/, "");

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
  output: document.getElementById("output"),
  outputMeta: document.getElementById("output-meta"),
  copyBtn: document.getElementById("copy-btn"),
  downloadBtn: document.getElementById("download-btn"),
  tabs: document.querySelectorAll(".tab"),
  panes: { url: document.getElementById("tab-url"), file: document.getElementById("tab-file") },
};

let selectedFile = null;
let lastMarkdown = "";

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
  } catch {
    setBadge("offline", "offline");
  }
}

function setBadge(state, text) {
  el.statusBadge.className = `badge badge--${state}`;
  el.statusText.textContent = text;
}

// --- Output helpers ----------------------------------------------------------
function showMarkdown(markdown, meta) {
  lastMarkdown = markdown;
  el.output.classList.remove("is-error");
  el.output.textContent = markdown || "(empty result)";
  el.outputMeta.textContent = meta || "";
  el.copyBtn.disabled = !markdown;
  el.downloadBtn.disabled = !markdown;
}

function showError(message) {
  lastMarkdown = "";
  el.output.classList.add("is-error");
  el.output.textContent = `⚠ ${message}`;
  el.outputMeta.textContent = "";
  el.copyBtn.disabled = true;
  el.downloadBtn.disabled = true;
}

function setBusy(button, busy, idleLabel) {
  button.disabled = busy;
  button.textContent = busy ? "Converting…" : idleLabel;
}

async function parseResponse(res) {
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(payload.detail || `Request failed (HTTP ${res.status})`);
  }
  return payload;
}

// --- URL conversion ----------------------------------------------------------
el.convertUrlBtn.addEventListener("click", async () => {
  const url = el.urlInput.value.trim();
  if (!url) return showError("Enter a URL first.");
  setBusy(el.convertUrlBtn, true, "Convert");
  try {
    const res = await fetch(`${API_BASE}/convert/url`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await parseResponse(res);
    showMarkdown(data.markdown, `${data.length.toLocaleString()} chars`);
  } catch (err) {
    showError(err.message);
  } finally {
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
  setBusy(el.convertFileBtn, true, "Convert");
  try {
    const form = new FormData();
    form.append("file", selectedFile);
    const res = await fetch(`${API_BASE}/convert/file`, { method: "POST", body: form });
    const data = await parseResponse(res);
    showMarkdown(data.markdown, `${data.length.toLocaleString()} chars`);
  } catch (err) {
    showError(err.message);
  } finally {
    setBusy(el.convertFileBtn, false, "Convert");
  }
});

// --- Copy + download ---------------------------------------------------------
el.copyBtn.addEventListener("click", async () => {
  if (!lastMarkdown) return;
  await navigator.clipboard.writeText(lastMarkdown);
  el.copyBtn.textContent = "Copied!";
  setTimeout(() => (el.copyBtn.textContent = "Copy"), 1500);
});

el.downloadBtn.addEventListener("click", () => {
  if (!lastMarkdown) return;
  const blob = new Blob([lastMarkdown], { type: "text/markdown" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "converted.md";
  link.click();
  URL.revokeObjectURL(link.href);
});

// --- Init --------------------------------------------------------------------
pingBackend();
