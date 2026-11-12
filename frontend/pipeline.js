/*
 * Wiseau — the pipeline view.
 *
 * Draws the route a conversion takes for what the user has chosen — a web page,
 * a born-digital or scanned PDF, a Word file, an image, a batch — and which
 * engine and options apply, then animates it: an idle demonstration that walks
 * through each stage and explains it, and, while a conversion runs, the stages
 * lighting up in turn.
 *
 * Honesty matters more than polish here (ADR-026). The API reports no progress,
 * so during a conversion only the *upload* stage is measured; every later stage
 * advances on the estimate, and the view says so. A route describes what the
 * backend will do *if* it behaves as configured: docling, when chosen, can still
 * fall back to the fast parser (ADR-014), and the route draws that branch.
 *
 * Exposes `window.wiseauPipeline`:
 *   buildRoute(context) -> route        (pure: what the conversion will do)
 *   mount({ track, detail, summary })   (once)
 *   show(route)                         (redraw while idle)
 *   start() / update(state) / finish(ok, message)
 */
(function () {
  "use strict";

  // --- Icons: 24×24 strokes, drawn here so there is nothing to fetch. ----------
  const ICONS = {
    upload: '<path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.6 3.6 5.6 3.6 8.5s-1 5.9-3.6 8.5c-2.6-2.6-3.6-5.6-3.6-8.5S9.4 6.1 12 3.5z"/>',
    extract: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 12.5h8M8 16h5"/><path d="M4 7h16" opacity=".5"/>',
    download: '<path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5"/><path d="M5 20h14"/>',
    inspect: '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/><path d="M8.5 11h5" />',
    text: '<path d="M5 6h14M12 6v13"/><path d="M8.5 19h7"/>',
    ocr: '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/><path d="M8 9.5h8M8 12h8M8 14.5h5"/>',
    layout: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M10 10v10M15 10v10"/>',
    model: '<path d="M12 3.5 13.8 9 19.5 10.5 13.8 12 12 17.5 10.2 12 4.5 10.5 10.2 9z"/><path d="M18.5 16.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
    word: '<path d="M7 3.5h7l4 4v13H7a1.5 1.5 0 0 1-1.5-1.5V5A1.5 1.5 0 0 1 7 3.5z"/><path d="m8.5 11 1.3 5 1.6-4 1.6 4 1.3-5"/>',
    structure: '<path d="M5 5h9M5 10h14M8 15h11M8 19.5h8"/><circle cx="5" cy="15" r="1"/><circle cx="5" cy="19.5" r="1"/>',
    reader: '<path d="M4 5.5C6.5 4.5 9.5 4.5 12 6c2.5-1.5 5.5-1.5 8-.5v13c-2.5-1-5.5-1-8 .5-2.5-1.5-5.5-1.5-8-.5z"/><path d="M12 6v13.5"/>',
    split: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    normalize: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    markdown: '<rect x="2.5" y="5.5" width="19" height="13" rx="2.5"/><path d="M6 15V9l2.5 3L11 9v6"/><path d="M16.5 9v6m0 0-2-2m2 2 2-2"/>',
    batch: '<path d="M8 4h8M6 7.5h12"/><rect x="4" y="11" width="16" height="9" rx="2"/>',
  };

  function icon(name) {
    return (
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      (ICONS[name] || ICONS.extract) +
      "</svg>"
    );
  }

  // --- Stage library -------------------------------------------------------------
  // `weight` is the stage's share of the *estimated* processing time; upload is
  // measured separately and the final node lights only when the response is in.
  const STAGES = {
    upload: {
      title: "Upload",
      sub: "to the API",
      icon: "upload",
      detail:
        "The file streams to the backend (25 MB limit per file). This is the one stage the bar measures directly — every later stage is an estimate, because the server reports no progress.",
    },
    render: {
      title: "Render",
      sub: "headless Chrome",
      icon: "globe",
      weight: 6,
      detail:
        "A headless Chromium session loads the page and runs its JavaScript, so content built in the browser is captured as a reader would see it.",
    },
    extract: {
      title: "Extract",
      sub: "Trafilatura",
      icon: "extract",
      weight: 2,
      detail:
        "Trafilatura separates the main content from navigation, ads and boilerplate — algorithmically, with no per-site CSS selectors.",
    },
    fetchPdf: {
      title: "Download",
      sub: "PDF in-session",
      icon: "download",
      weight: 3,
      detail:
        "The URL serves a PDF, so the browser session downloads it and it is converted exactly like an uploaded document.",
    },
    inspect: {
      title: "Inspect",
      sub: "every page",
      icon: "inspect",
      weight: 1,
      detail:
        "Each page is checked for a usable text layer. A page without one — or with only an invisible OCR layer over a scan — is read from its pixels instead.",
    },
    read: {
      title: "Read pages",
      sub: "text layer or OCR",
      icon: "text",
      weight: 4,
      detail:
        "Pages with a text layer are read by PyMuPDF with positions and fonts; scanned pages are rendered at 300 DPI and OCR'd. Which applies is decided page by page.",
    },
    textLayer: {
      title: "Text layer",
      sub: "PyMuPDF",
      icon: "text",
      weight: 3,
      detail:
        "PyMuPDF reads the embedded text with its positions, fonts and the document outline. Deterministic: the same file always gives the same Markdown.",
    },
    ocr: {
      title: "OCR",
      sub: "Tesseract · 300 DPI",
      icon: "ocr",
      weight: 6,
      detail:
        "Each page is rendered at 300 DPI and Tesseract reads the words, keeping columns in reading order and stitching table rows back together. This is the slow part of a scan: many seconds a page.",
    },
    layout: {
      title: "Layout & tables",
      sub: "region + table models",
      icon: "layout",
      weight: 4,
      detail:
        "A layout model types each region — heading, paragraph, figure, table — and Table Transformer rebuilds table cells, so a scanned table arrives as a Markdown table (when this deployment has the models on).",
    },
    docling: {
      title: "docling",
      sub: "ML document model",
      icon: "model",
      weight: 10,
      fallback: "falls back to PyMuPDF",
      detail:
        "docling runs ML layout, table and OCR models in a separate service — the most faithful reading of complex, multi-column and scanned documents, at tens of seconds to minutes. If it is cold, busy or unavailable, the fast parser takes over automatically and you still get a result.",
    },
    glm: {
      title: "GLM-OCR",
      sub: "vision-language model",
      icon: "model",
      weight: 10,
      fallback: "falls back to PyMuPDF",
      detail:
        "A vision-language model transcribes every region of every scanned page — very accurate and very slow on CPU. Born-digital pages skip it. Falls back to the fast parser if unavailable.",
    },
    mammoth: {
      title: "Word styles",
      sub: "Mammoth",
      icon: "word",
      weight: 3,
      detail:
        "Mammoth maps Word's own paragraph styles to headings, lists, tables and emphasis — the structure the author applied, not guessed from formatting.",
    },
    mixed: {
      title: "Read",
      sub: "by file type",
      icon: "extract",
      weight: 5,
      detail:
        "Each document is read by the right parser for its type — PyMuPDF or OCR for PDFs and scans, Mammoth for Word files — one after another.",
    },
    structure: {
      title: "Structure",
      sub: "headings · captions · notes",
      icon: "structure",
      weight: 1.5,
      detail:
        "Deterministic passes over the document model recover heading levels, figure captions, footnotes, and paragraphs and tables that continue across columns or pages.",
    },
    reader: {
      title: "Reader mode",
      sub: "drops page furniture",
      icon: "reader",
      weight: 0.6,
      detail:
        "Running headers, footers and page numbers are left out, and a table that continues across pages is written with its header once. Without it every printed word is kept.",
    },
    chapters: {
      title: "Chapters",
      sub: "contents page · headings",
      icon: "split",
      weight: 0.6,
      detail:
        "The result is split into chapters found from the document's contents page, its headings, or “Chapter N” lines. If nothing plausible is found, the document is left whole rather than guessed at.",
    },
    normalize: {
      title: "Normalize",
      sub: "serialize · clean",
      icon: "normalize",
      weight: 0.8,
      detail:
        "The document model is written out as Markdown and every path ends in the same normalizer: Unicode, whitespace and stray image payloads cleaned up.",
    },
    markdown: {
      title: "Markdown",
      sub: ".md",
      icon: "markdown",
      detail: "Clean, structured Markdown — readable by people, parseable by LLMs and agents.",
    },
  };

  function stage(id, overrides) {
    return Object.assign({ id, weight: 0 }, STAGES[id], overrides || {});
  }

  // --- Route building --------------------------------------------------------------
  /**
   * What a conversion will do, given:
   *   source:   "url" | "file"
   *   url:      the URL typed (for "url")
   *   files:    [{ name, type: "pdf"|"docx"|"image"|"other", probe? }]
   *   engine:   the engine that will run ("pymupdf" | "docling" | "glm-ocr")
   *   chapters, reader: the two options (booleans)
   */
  function buildRoute(context) {
    const stages = [];
    const notes = [];
    const engine = context.engine || "pymupdf";
    let label = "";

    if (context.source === "url") {
      const pdf = /\.pdf($|[?#])/i.test(context.url || "");
      if (pdf) {
        label = "PDF link";
        stages.push(stage("render", { sub: "opens the link" }), stage("fetchPdf"));
        pushDocument(stages, { type: "pdf" }, engine, false);
      } else {
        label = "Web page";
        stages.push(stage("render"), stage("extract"));
      }
      if (context.chapters) stages.push(stage("chapters"));
      stages.push(stage("normalize"), stage("markdown"));
      return { label, stages, notes, engine };
    }

    const files = context.files || [];
    const types = new Set(files.map((file) => file.type));
    stages.push(stage("upload", files.length > 1 ? { sub: `${files.length} files` } : null));

    if (!files.length) {
      label = "Document";
      pushDocument(stages, { type: "pdf" }, engine, context.reader);
    } else if (types.size === 1) {
      const first = files[0];
      label = describe(first, files.length);
      pushDocument(stages, files.length === 1 ? first : { type: first.type, probe: mergeProbes(files) }, engine, context.reader);
    } else {
      label = `${files.length} mixed documents`;
      stages.push(stage("mixed"));
      if (engine === "pymupdf") stages.push(stage("structure"));
      if (context.reader) stages.push(stage("reader"));
    }

    if (files.length > 1) notes.push("Converted one after another; each becomes its own .md");
    if (context.chapters && files.length <= 1) stages.push(stage("chapters"));
    stages.push(stage("normalize"), stage("markdown"));
    return { label, stages, notes, engine };
  }

  /** Batch files of one type: call it a scan if any of them is one. */
  function mergeProbes(files) {
    const probes = files.map((file) => file.probe).filter(Boolean);
    if (!probes.length) return null;
    const scan = probes.some((probe) => probe.kind === "scan");
    const text = probes.some((probe) => probe.kind === "text");
    return { kind: scan && text ? "mixed" : scan ? "scan" : "text" };
  }

  function describe(file, count) {
    const plural = count > 1;
    if (file.type === "docx") return plural ? `${count} Word documents` : "Word document";
    if (file.type === "image") return plural ? `${count} images` : "Image";
    if (file.type === "pdf") {
      const probe = file.probe;
      if (plural) return `${count} PDFs`;
      if (!probe) return "PDF";
      const scanned = probe.kind === "scan";
      return `${scanned ? "Scanned" : "Digital"} PDF · ${probe.pages} page${probe.pages === 1 ? "" : "s"}`;
    }
    return plural ? `${count} files` : "File";
  }

  function pushDocument(stages, file, engine, reader) {
    const probe = file.probe;
    const remote = engine === "docling" ? "docling" : engine === "glm-ocr" ? "glm" : null;

    if (file.type === "docx") {
      // GLM-OCR has no Word reader, so a Word file always takes Mammoth there.
      if (remote === "docling") stages.push(stage("docling"));
      else stages.push(stage("mammoth"));
      if (reader) stages.push(stage("reader"));
      return;
    }

    if (remote) {
      stages.push(stage(remote));
      stages.push(stage("structure"));
      if (reader) stages.push(stage("reader"));
      return;
    }

    if (file.type === "image") {
      stages.push(stage("ocr", { sub: "Tesseract" }), stage("layout"));
    } else if (probe && probe.kind === "scan") {
      stages.push(stage("inspect"), stage("ocr"), stage("layout"));
    } else if (probe && probe.kind === "text") {
      stages.push(stage("inspect"), stage("textLayer"));
    } else {
      stages.push(stage("inspect"), stage("read"));
    }
    stages.push(stage("structure"));
    if (reader) stages.push(stage("reader"));
  }

  // --- Rendering + animation ---------------------------------------------------------
  const reduceMotion =
    typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const view = {
    track: null,
    detail: null,
    summary: null,
    route: null,
    nodes: [],
    links: [],
    mode: "idle", // idle | running | done | error
    demoIndex: 0,
    demoTimer: null,
    pinned: -1, // a node the user is hovering or focusing
  };

  function mount(elements) {
    view.track = elements.track;
    view.detail = elements.detail;
    view.summary = elements.summary;
    view.track.addEventListener("mouseleave", () => unpin());
  }

  function show(route) {
    view.route = route;
    if (view.mode === "running") return; // never redraw under a running conversion
    view.mode = "idle";
    render();
    startDemo();
  }

  function render() {
    const route = view.route;
    view.track.replaceChildren();
    view.nodes = [];
    view.links = [];
    view.track.dataset.mode = view.mode;
    route.stages.forEach((item, index) => {
      if (index > 0) {
        const link = document.createElement("li");
        link.className = "pipe__link";
        link.setAttribute("aria-hidden", "true");
        link.innerHTML = '<span class="pipe__wire"></span><span class="pipe__packet"></span>';
        view.track.append(link);
        view.links.push(link);
      }
      const node = document.createElement("li");
      node.className = "pipe__node";
      node.tabIndex = 0;
      node.dataset.stage = item.id;
      if (item.id === "markdown") node.classList.add("pipe__node--final");
      node.innerHTML =
        `<span class="pipe__icon">${icon(item.icon)}<span class="pipe__ring"></span></span>` +
        '<span class="pipe__text"><span class="pipe__title"></span><span class="pipe__sub"></span></span>' +
        (item.fallback ? '<span class="pipe__fallback"></span>' : "");
      node.querySelector(".pipe__title").textContent = item.title;
      node.querySelector(".pipe__sub").textContent = item.sub || "";
      if (item.fallback) node.querySelector(".pipe__fallback").textContent = `↳ ${item.fallback}`;
      node.addEventListener("mouseenter", () => pin(index));
      node.addEventListener("focus", () => pin(index));
      node.addEventListener("blur", () => unpin());
      view.track.append(node);
      view.nodes.push(node);
    });
    if (view.summary) {
      const engineName = { pymupdf: "Fast engine", docling: "docling", "glm-ocr": "GLM-OCR" }[route.engine] || route.engine;
      const parts = [route.label];
      if (route.label !== "Web page") parts.push(engineName);
      view.summary.textContent = parts.concat(route.notes).join(" · ");
    }
    setDetail(0);
  }

  function setDetail(index, prefix) {
    const item = view.route && view.route.stages[index];
    if (!view.detail || !item) return;
    view.detail.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = (prefix ? `${prefix} ` : "") + item.title;
    const body = document.createElement("span");
    body.textContent = item.detail || "";
    view.detail.append(title, body);
  }

  /** Paint node states: indices before `active` done, `active` active, the rest pending. */
  function paint(active, options) {
    const opts = options || {};
    view.nodes.forEach((node, index) => {
      let state = "pending";
      if (index < active || opts.allDone) state = "done";
      else if (index === active) state = opts.error ? "error" : "active";
      node.dataset.state = state;
    });
    view.links.forEach((link, index) => {
      // links[index] joins nodes[index] → nodes[index + 1]
      let state = "pending";
      if (index + 1 < active || opts.allDone) state = "done";
      else if (index + 1 === active) state = opts.error ? "error" : "flowing";
      link.dataset.state = state;
    });
  }

  // Idle: a demonstration that walks the route, explaining each stage in turn.
  function startDemo() {
    stopDemo();
    view.demoIndex = 0;
    if (reduceMotion) {
      paint(-1);
      return;
    }
    const step = () => {
      if (view.mode !== "idle" || view.pinned >= 0) return;
      const count = view.nodes.length;
      paint(view.demoIndex);
      setDetail(view.demoIndex);
      view.demoIndex = (view.demoIndex + 1) % (count + 1); // one beat of rest at the end
      if (view.demoIndex === 0) {
        // Rest: everything done, Markdown glowing.
        view.demoTimer = setTimeout(() => {
          paint(count, { allDone: true });
          view.demoTimer = setTimeout(step, 2200);
        }, 2000);
        return;
      }
      view.demoTimer = setTimeout(step, 2000);
    };
    step();
  }

  function stopDemo() {
    if (view.demoTimer) clearTimeout(view.demoTimer);
    view.demoTimer = null;
  }

  function pin(index) {
    view.pinned = index;
    view.nodes.forEach((node, position) => node.classList.toggle("pipe__node--pinned", position === index));
    setDetail(index);
    if (view.mode === "idle") {
      stopDemo();
      paint(index);
    }
  }

  function unpin() {
    if (view.pinned < 0) return;
    view.pinned = -1;
    view.nodes.forEach((node) => node.classList.remove("pipe__node--pinned"));
    if (view.mode === "idle") startDemo();
    else setDetail(currentActive());
  }

  // --- Running -------------------------------------------------------------------
  let runState = { active: 0 };

  function currentActive() {
    return Math.min(runState.active, view.nodes.length - 1);
  }

  function start() {
    stopDemo();
    view.mode = "running";
    render();
    view.track.dataset.mode = "running";
    runState = { active: 0 };
    paint(0);
    setDetail(0, "Now:");
  }

  /**
   * Move the run along.
   *   state.upload:  0..1 while the file is uploading (measured), else null
   *   state.process: 0..1 of the *estimated* processing time
   */
  function update(state) {
    if (view.mode !== "running" || !view.route) return null;
    const stages = view.route.stages;
    // Once the server reports stages, they drive the view, not the estimate.
    if (runState.live) return stages[runState.active] || null;
    const hasUpload = stages[0] && stages[0].id === "upload";
    let active;
    if (hasUpload && state.upload !== null && state.upload !== undefined) {
      active = 0;
    } else {
      const first = hasUpload ? 1 : 0;
      const last = stages.length - 2; // the final "Markdown" node lights on finish
      const weights = stages.slice(first, last + 1).map((item) => item.weight || 1);
      const total = weights.reduce((sum, weight) => sum + weight, 0);
      let budget = Math.max(0, Math.min(1, state.process || 0)) * total;
      active = first;
      for (let i = 0; i < weights.length; i += 1) {
        if (budget < weights[i] || i === weights.length - 1) {
          active = first + i;
          break;
        }
        budget -= weights[i];
      }
    }
    if (active !== runState.active) {
      runState.active = active;
      paint(active);
      if (view.pinned < 0) setDetail(active, "Now:");
    }
    return stages[runState.active] || null;
  }

  function finish(ok) {
    if (view.mode !== "running") return;
    view.mode = ok ? "done" : "error";
    view.track.dataset.mode = view.mode;
    if (ok) {
      paint(view.nodes.length, { allDone: true });
      setDetail(view.nodes.length - 1, "Done:");
    } else {
      paint(currentActive(), { error: true });
    }
  }

  // --- Live: what the server reports (ADR-058) -----------------------------------
  // A stage the backend names, and the route nodes that stand for it, in order
  // of preference: a PDF whose probe failed has a generic "Read pages" node
  // where a scan would have "OCR".
  const SERVER_STAGES = {
    inspect: ["inspect"],
    text_layer: ["textLayer", "read", "mixed"],
    ocr: ["ocr", "read", "mixed"],
    mammoth: ["mammoth", "mixed"],
    docling: ["docling"],
    "glm-ocr": ["glm"],
    render: ["render"],
    extract: ["extract", "fetchPdf"],
    structure: ["structure"],
    normalize: ["normalize"],
    chapters: ["chapters"],
  };

  /**
   * The server started `stage`. Moves the run to the node standing for it —
   * forward only, so a batch's second document does not rewind the view — and
   * returns that stage, or null when the route has no node for it.
   */
  function live(stage) {
    if (view.mode !== "running" || !view.route) return null;
    const ids = SERVER_STAGES[stage] || [];
    const stages = view.route.stages;
    let index = -1;
    for (const id of ids) {
      index = stages.findIndex((item) => item.id === id);
      if (index >= 0) break;
    }
    if (index < 0) return null;
    if (index > runState.active) {
      runState.active = index;
      paint(index);
    }
    runState.live = true;
    if (view.pinned < 0) setDetail(runState.active, "Now:");
    return stages[runState.active];
  }

  /** Replace the active node's subtitle — "page 7 of 36" — or restore it. */
  function liveSub(text) {
    const node = view.nodes[currentActive()];
    const item = view.route && view.route.stages[currentActive()];
    if (!node || !item) return;
    node.querySelector(".pipe__sub").textContent = text || item.sub || "";
  }

  /** Say something about the run in the detail line (a fallback, a queue). */
  function note(title, text) {
    if (!view.detail) return;
    view.detail.replaceChildren();
    const strong = document.createElement("strong");
    strong.textContent = title;
    const body = document.createElement("span");
    body.textContent = text;
    view.detail.append(strong, body);
  }

  /** Mark a remote engine's node as having fallen back to the local parser. */
  function fellBack(engine) {
    const id = engine === "glm-ocr" ? "glm" : engine;
    const index = view.route ? view.route.stages.findIndex((item) => item.id === id) : -1;
    if (index >= 0 && view.nodes[index]) view.nodes[index].classList.add("pipe__node--fallback");
  }

  function isLive() {
    return Boolean(runState.live);
  }

  /** Back to the idle demonstration after a finished run. */
  function reset() {
    if (view.mode === "running") return;
    view.mode = "idle";
    if (view.route) show(view.route);
  }

  window.wiseauPipeline = {
    buildRoute, mount, show, start, update, finish, reset, icon,
    live, liveSub, note, fellBack, isLive,
  };
})();
