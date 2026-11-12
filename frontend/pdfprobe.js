/*
 * Wiseau — a small, dependency-free look inside a PDF before it is uploaded.
 *
 * The API converts in one blocking call and reports no progress, so the only
 * honest way to estimate a conversion is from what the *document* is: how many
 * pages it has, and whether those pages carry a text layer (seconds for the
 * whole document) or are scans (OCR, many seconds a page). File size alone is a
 * poor proxy — a 3 MB, 40-page scan takes minutes, a 3 MB, 40-page report takes
 * about one second — which is why the old size-based bar raced to 99% and sat
 * there on exactly the documents that take longest.
 *
 * This is a heuristic, not a PDF parser. It reads the bytes as text and looks for
 * the page tree's `/Count`, font and image objects, and the producer string; when
 * those live inside compressed object streams (PDF 1.5+), it inflates them with
 * the browser's built-in `DecompressionStream`. It mirrors the backend's own test
 * (ADR-040: no usable text layer, or only an invisible OCR layer over a scan)
 * only roughly, so it reports `confidence` and the UI says "likely".
 *
 * Exposes `window.wiseauPdfProbe.probe(file) -> Promise<Probe | null>`, where
 * Probe = { pages, kind: "text" | "scan", confidence: "high" | "low", reason }.
 * Never throws; returns null when it cannot tell.
 */
(function () {
  "use strict";

  // Producers that write scans. A born-digital PDF almost never names these.
  const SCAN_PRODUCERS =
    /(scan|abbyy|finereader|omnipage|paper capture|ocrmypdf|tesseract|readiris|naps2|kofax|captiva|xerox|ricoh|konica|kyocera|canon|epson|brother|sharp mx|hp digital sending|imagerunner|bizhub)/i;

  const MAX_INFLATE_STREAMS = 400;
  const MAX_CONTENT_SAMPLES = 24;

  function latin1(bytes) {
    // `latin1` maps every byte to one char, so offsets line up with the bytes.
    return new TextDecoder("latin1").decode(bytes);
  }

  async function inflate(bytes) {
    if (typeof DecompressionStream !== "function") return "";
    // Read chunk by chunk and keep what decoded: a stream sliced up to
    // `endstream` carries a trailing EOL, which the decoder rejects as junk
    // after the end of the data — by then the content is already out.
    const chunks = [];
    try {
      const reader = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate")).getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(latin1(value));
      }
    } catch {
      /* keep the chunks decoded before the error */
    }
    return chunks.join("");
  }

  /** The decompressed text of every FlateDecode object stream (`/Type /ObjStm`). */
  async function objectStreams(text, bytes) {
    const out = [];
    const pattern = /\/Type\s*\/ObjStm\b/g;
    let match;
    while ((match = pattern.exec(text)) && out.length < MAX_INFLATE_STREAMS) {
      const dictStart = text.lastIndexOf("<<", match.index);
      const streamAt = text.indexOf("stream", match.index);
      if (dictStart < 0 || streamAt < 0) continue;
      const dict = text.slice(dictStart, streamAt);
      if (!/FlateDecode/.test(dict)) continue;
      let start = streamAt + "stream".length;
      if (text[start] === "\r") start += 1;
      if (text[start] === "\n") start += 1;
      const length = /\/Length\s+(\d+)(?!\s+\d+\s+R)/.exec(dict);
      const end = length ? start + Number(length[1]) : text.indexOf("endstream", start);
      if (end <= start) continue;
      out.push(await inflate(bytes.subarray(start, end)));
      pattern.lastIndex = end;
    }
    return out.join("\n");
  }

  /**
   * Of a sample of page content streams, how many draw text and how many draw it
   * only invisibly (render mode `3 Tr`) — the signature of an OCR layer laid
   * over a scan, which the backend reads from pixels anyway (ADR-040).
   */
  async function textRendering(text, bytes) {
    let drawing = 0;
    let invisible = 0;
    let sampled = 0;
    const pattern = /\bstream\r?\n/g;
    let match;
    while ((match = pattern.exec(text)) && sampled < MAX_CONTENT_SAMPLES) {
      const dictStart = text.lastIndexOf("<<", match.index);
      if (dictStart < 0) continue;
      const dict = text.slice(dictStart, match.index);
      if (!/FlateDecode/.test(dict) || /\/Subtype\s*\/Image|\/Type\s*\/(ObjStm|XRef|XObject)|\/Length[123]\b/.test(dict)) continue;
      const start = match.index + match[0].length;
      const end = text.indexOf("endstream", start);
      if (end <= start || end - start > 2 * 1024 * 1024) continue;
      pattern.lastIndex = end;
      const content = await inflate(bytes.subarray(start, end));
      if (!/\bBT\b/.test(content)) continue;
      sampled += 1;
      drawing += 1;
      const modes = content.match(/\b[0-7]\s+Tr\b/g) || [];
      if (modes.length && modes.every((mode) => mode.trim().startsWith("3"))) invisible += 1;
    }
    return { drawing, invisible };
  }

  /** The largest `/Count` on a `/Type /Pages` node: the root of the page tree. */
  function pageCount(text) {
    let best = 0;
    const pattern = /\/Type\s*\/Pages\b/g;
    let match;
    while ((match = pattern.exec(text))) {
      const open = text.lastIndexOf("<<", match.index);
      const window = text.slice(Math.max(0, open), match.index + 400);
      const count = /\/Count\s+(\d+)/.exec(window);
      if (count) best = Math.max(best, Number(count[1]));
    }
    if (best) return best;
    // No page tree found: count the leaves instead.
    const leaves = text.match(/\/Type\s*\/Page(?![s\w])/g);
    return leaves ? leaves.length : 0;
  }

  function count(text, pattern) {
    const found = text.match(pattern);
    return found ? found.length : 0;
  }

  function classify(text, pages, bytesPerPage, rendering) {
    const fonts = count(text, /\/Type\s*\/Font\b/g) + count(text, /\/FontDescriptor\b/g);
    const images = count(text, /\/Subtype\s*\/Image\b/g);
    const producer = /\/(Producer|Creator)\s*\(([^)]{0,200})\)/g;
    let scanner = false;
    let entry;
    while ((entry = producer.exec(text))) if (SCAN_PRODUCERS.test(entry[2])) scanner = true;

    if (fonts === 0) {
      return { kind: "scan", confidence: "high", reason: "no fonts — the pages are images" };
    }
    if (
      images >= pages * 0.8 &&
      rendering.drawing >= Math.min(3, pages) &&
      rendering.invisible >= rendering.drawing * 0.8
    ) {
      return { kind: "scan", confidence: "high", reason: "scanned images under an invisible OCR layer" };
    }
    if (/GlyphLessFont/.test(text)) {
      return { kind: "scan", confidence: "high", reason: "an invisible OCR layer over scanned images" };
    }
    if (scanner && images >= pages * 0.8) {
      return { kind: "scan", confidence: "low", reason: "written by a scanner or OCR tool" };
    }
    if (images >= pages && fonts <= 2 && bytesPerPage > 120 * 1024) {
      return { kind: "scan", confidence: "low", reason: "one large image a page, almost no fonts" };
    }
    return { kind: "text", confidence: images > pages ? "low" : "high", reason: "has a text layer" };
  }

  async function probe(file) {
    try {
      if (!file || !/\.pdf$/i.test(file.name)) return null;
      const bytes = new Uint8Array(await file.arrayBuffer());
      let text = latin1(bytes);
      let pages = pageCount(text);
      const hasObjectStreams = /\/Type\s*\/ObjStm\b/.test(text);
      if (hasObjectStreams) {
        text += "\n" + (await objectStreams(text, bytes));
        if (!pages) pages = pageCount(text);
      }
      if (!pages) return null;
      const rendering = await textRendering(latin1(bytes), bytes);
      return { pages, ...classify(text, pages, bytes.length / pages, rendering) };
    } catch {
      return null;
    }
  }

  window.wiseauPdfProbe = { probe };
})();
