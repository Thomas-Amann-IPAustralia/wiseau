"""Specialist recogniser and table engines for the bake-off (roadmap 14 item 1.3).

None of these ship. They answer two questions with the rest of the default path
held fixed, so a score difference belongs to the one stage that changed:

* **Recogniser.** ``rec_engine(model)`` keeps Tesseract's own page layout (psm 3,
  table rows stitched, ADR-038) and re-reads every line box Tesseract found with
  another recogniser. ``model="tesseract"`` keeps Tesseract's text: it is the
  control, the same code with nothing swapped. The PaddleOCR models are
  ``PP-OCRv6_small_rec`` (the roadmap's candidate) and ``en_PP-OCRv5_mobile_rec``
  (PaddleOCR's English model).
  ``word_rec_engine(model)`` (``wrec-MODEL``) re-reads each Tesseract *word* box
  instead, keeping Tesseract's word boundaries, because line-level PP-OCR drops
  the spaces in widely spaced table rows.
* **Tables.** ``tatr_engine()`` keeps the same layout and recogniser (Tesseract)
  but finds tables with Table Transformer (PubTables-1M detection, v1.1 structure
  recognition) instead of ADR-038's row stitching. Each detected table is read row
  by row from TATR's rows, a multi-line row line by line across, and placed where
  Tesseract's first block inside it was. With no table detected the output is the
  control's, word for word.
* **A whole pipeline.** ``ppstructure_engine()`` runs PaddleOCR's PP-StructureV3
  (layout detection, its own OCR, table recognition) and returns its Markdown.

All three skip the ADR-038 guard and the scanner-band cleanup (ADR-041), and all
of them, the control included, use the photo mask (ADR-037). Compare them with
``rec-tesseract``, not with ``pymupdf``. Born-digital pages (no scan) are copied
from ``outputs/pymupdf`` so a set's means stay comparable; TATR alone also runs on
them, reading words from the text layer (V16 p90 is a known table failure).

The backend's ``parsers`` package imports the whole service on import, so the
three pure modules used here (``tesseract_layout``, ``photo_regions``,
``cleaner``) are loaded without it (``_parsers()``), and these engines run in a
venv that has only PyMuPDF plus the ML stack.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import types
from pathlib import Path
from statistics import median

import numpy as np
import pymupdf

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
DPI = 300
LINE_PAD = 4          # pixels added around a Tesseract line box before re-reading it
TATR_DETECT = 0.8     # detection score for a table
TATR_ROW = 0.5        # structure score for a row
TATR_PAD = 0.02       # share of the page added around a detected table for structure recognition


def _parsers():
    """``tesseract_layout``, ``photo_regions`` and ``cleaner`` without ``parsers/__init__``."""
    if "parsers" not in sys.modules:
        pkg = types.ModuleType("parsers")
        pkg.__path__ = [str(ROOT / "backend" / "parsers")]
        sys.modules["parsers"] = pkg
    return (importlib.import_module("parsers.tesseract_layout"),
            importlib.import_module("parsers.photo_regions"),
            importlib.import_module("parsers.cleaner"))


def _is_scan(page: pymupdf.Page) -> bool:
    """An image covers >= 90% of the page (the scans in every scored set)."""
    area = abs(page.rect)
    return any(abs(pymupdf.Rect(i["bbox"]) & page.rect) >= 0.9 * area for i in page.get_image_info())


def _copied(key: str) -> str:
    return (HERE / "outputs" / "pymupdf" / f"{key}.md").read_text()


def _image(page: pymupdf.Page) -> np.ndarray:
    pix = page.get_pixmap(dpi=DPI, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n).copy()


def _tsv(img: np.ndarray) -> str:
    import cv2

    ok, png = cv2.imencode(".png", img[:, :, ::-1] if img.shape[2] == 3 else img)
    done = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "3", "-l", "eng", "tsv"],
                          input=png.tobytes(), capture_output=True, timeout=180, check=True,
                          env={**os.environ, "OMP_THREAD_LIMIT": "1"})   # as the default path (ADR-042)
    return done.stdout.decode("utf-8", errors="replace")


def _scaled(tl, blocks, scale: float):
    return [tl._block([ln._replace(x0=ln.x0 * scale, y0=ln.y0 * scale, x1=ln.x1 * scale, y1=ln.y1 * scale)
                       for ln in b.lines]) for b in blocks]


def _masked(tl, pr, page, blocks):
    mask = pr.photo_mask(page)
    if not mask.cells:
        return blocks
    kept = [[ln for ln in b.lines if not pr.is_photo_noise(ln, mask)] for b in blocks]
    return [tl._block(ls) for ls in kept if ls]


# --- recogniser swap ------------------------------------------------------------------

def rec_engine(model: str):
    tl, pr, cleaner = _parsers()
    recognise = None
    if model != "tesseract":
        os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "huggingface")
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        from paddleocr import TextRecognition

        rec = TextRecognition(model_name=model, cpu_threads=int(os.environ.get("PADDLE_THREADS", "4")))

        def recognise(crops):
            return [r["rec_text"] for r in rec.predict(crops, batch_size=8)]

    def run(data: bytes, key: str) -> str:
        page = pymupdf.open(stream=data, filetype="pdf")[0]
        if not _is_scan(page):
            return _copied(key)
        img = _image(page)
        blocks = tl.parse_tsv(_tsv(img), scale=1.0)
        if recognise is not None:
            lines = [ln for b in blocks for ln in b.lines]
            h, w = img.shape[:2]
            crops = [img[max(0, int(ln.y0) - LINE_PAD):min(h, int(ln.y1) + LINE_PAD),
                         max(0, int(ln.x0) - LINE_PAD):min(w, int(ln.x1) + LINE_PAD), ::-1].copy()
                     for ln in lines]
            texts = iter(recognise(crops) if crops else [])
            # An empty read keeps Tesseract's text, so a line is never silently lost.
            blocks = [tl._block([ln._replace(text=(t.strip() or ln.text)) for ln, t in zip(b.lines, texts)])
                      for b in blocks]
        blocks = _masked(tl, pr, page, _scaled(tl, blocks, 72.0 / DPI))
        return cleaner.clean_markdown(tl.render(blocks))

    return run


def word_rec_engine(model: str):
    """As ``rec_engine``, but every Tesseract *word* box is re-read, and words are
    joined with Tesseract's spaces. Line-level PP-OCR drops the spaces in widely
    spaced table rows (``range42 5746``); this keeps Tesseract's word segmentation
    and swaps only the reading of each word."""
    tl, pr, cleaner = _parsers()
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "huggingface")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import TextRecognition

    rec = TextRecognition(model_name=model, cpu_threads=int(os.environ.get("PADDLE_THREADS", "4")))

    def run(data: bytes, key: str) -> str:
        page = pymupdf.open(stream=data, filetype="pdf")[0]
        if not _is_scan(page):
            return _copied(key)
        img = _image(page)
        words = _tsv_words(_tsv(img), 1.0)
        h, w = img.shape[:2]
        crops = [img[max(0, int(wd.y0) - LINE_PAD):min(h, int(wd.y1) + LINE_PAD),
                     max(0, int(wd.x0) - LINE_PAD):min(w, int(wd.x1) + LINE_PAD), ::-1].copy() for wd in words]
        texts = [r["rec_text"] for r in rec.predict(crops, batch_size=16)] if crops else []
        for wd, t in zip(words, texts):
            t = "".join(t.split())          # one box, one word
            if t:                           # an empty read keeps Tesseract's word
                wd.text = t
        blocks = [b for _, b in _blocks_from_words(tl, words)]
        blocks = _masked(tl, pr, page, _scaled(tl, blocks, 72.0 / DPI))
        return cleaner.clean_markdown(tl.render(blocks))

    return run


# --- Table Transformer ------------------------------------------------------------------

class _Word:
    __slots__ = ("x0", "y0", "x1", "y1", "text", "line")

    def __init__(self, x0, y0, x1, y1, text, line):
        self.x0, self.y0, self.x1, self.y1, self.text, self.line = x0, y0, x1, y1, text, line


def _tsv_words(tsv: str, scale: float) -> list[_Word]:
    import csv
    import io

    out = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        x0, y0 = int(row["left"]) * scale, int(row["top"]) * scale
        out.append(_Word(x0, y0, x0 + int(row["width"]) * scale, y0 + int(row["height"]) * scale,
                         row["text"].strip(), (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))))
    return out


def _layer_words(page: pymupdf.Page) -> list[_Word]:
    return [_Word(x0, y0, x1, y1, t, (b, 0, ln)) for x0, y0, x1, y1, t, b, ln, _ in page.get_text("words")]


def _blocks_from_words(tl, words):
    from parsers.reading_order import Line

    lines: dict[tuple, list[_Word]] = {}
    for wd in words:
        lines.setdefault(wd.line, []).append(wd)
    blocks: dict[int, list] = {}
    for key, ws in lines.items():
        blocks.setdefault(key[0], []).append(Line(min(w.x0 for w in ws), min(w.y0 for w in ws),
                                                  max(w.x1 for w in ws), max(w.y1 for w in ws),
                                                  " ".join(w.text for w in ws)))
    return [(k, tl._block(ls)) for k, ls in blocks.items()]


def _sublines(words: list[_Word]) -> list[str]:
    """Words of one table row as visual lines, top to bottom, each left to right."""
    if not words:
        return []
    height = median(w.y1 - w.y0 for w in words)
    lines: list[tuple[float, list[_Word]]] = []
    for w in sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0)):
        c = (w.y0 + w.y1) / 2
        if lines and c - lines[-1][0] <= 0.5 * height:
            lines[-1][1].append(w)
        else:
            lines.append((c, [w]))
    return [" ".join(w.text for w in sorted(ws, key=lambda w: w.x0)) for _, ws in lines]


def _table_text(words: list[_Word], rows: list[tuple[float, float]]) -> str:
    """Each word to the row it overlaps most (nearest centre if none); rows top to bottom."""
    if not rows:
        return "\n".join(_sublines(words))
    buckets: list[list[_Word]] = [[] for _ in rows]
    for w in words:
        best, score = 0, None
        for i, (y0, y1) in enumerate(rows):
            overlap = min(w.y1, y1) - max(w.y0, y0)
            dist = abs((w.y0 + w.y1) / 2 - (y0 + y1) / 2)
            s = (overlap, -dist)
            if score is None or s > score:
                best, score = i, s
        buckets[best].append(w)
    return "\n".join(line for b in buckets for line in _sublines(b))


def tatr_engine():
    tl, pr, cleaner = _parsers()
    import torch
    from transformers import TableTransformerForObjectDetection
    from PIL import Image

    torch.manual_seed(0)
    torch.set_num_threads(int(os.environ.get("TATR_THREADS", "4")))
    det_m = TableTransformerForObjectDetection.from_pretrained("microsoft/table-transformer-detection").eval()
    str_m = TableTransformerForObjectDetection.from_pretrained(
        "microsoft/table-transformer-structure-recognition-v1.1-all").eval()
    mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)

    def detect(model, im: Image.Image, max_size: int, threshold: float):
        """Microsoft's reference inference: longest side resized to ``max_size``,
        ImageNet normalisation, best non-background class per query."""
        scale = max_size / max(im.size)
        resized = im.resize((round(im.width * scale), round(im.height * scale)), Image.BILINEAR)
        x = torch.from_numpy(np.asarray(resized, np.float32) / 255.0).permute(2, 0, 1)
        x = ((x - mean) / std).unsqueeze(0)
        with torch.inference_mode():
            out = model(pixel_values=x)
        probs = out.logits.softmax(-1)[0, :, :-1]
        scores, labels = probs.max(-1)
        found = []
        for sc, lab, (cx, cy, w, h) in zip(scores.tolist(), labels.tolist(), out.pred_boxes[0].tolist()):
            if sc >= threshold:
                found.append((model.config.id2label[lab], sc,
                              [(cx - w / 2) * im.width, (cy - h / 2) * im.height,
                               (cx + w / 2) * im.width, (cy + h / 2) * im.height]))
        return found

    def run(data: bytes, key: str) -> str:
        page = pymupdf.open(stream=data, filetype="pdf")[0]
        scan = _is_scan(page)
        img = _image(page)
        scale = 72.0 / DPI
        words = _tsv_words(_tsv(img), scale) if scan else _layer_words(page)
        im = Image.fromarray(img[:, :, :3] if img.shape[2] >= 3 else np.repeat(img, 3, axis=2))
        tables = sorted((b for lab, s, b in detect(det_m, im, 800, TATR_DETECT) if lab == "table"),
                        key=lambda b: (b[1], b[0]))
        if not tables:
            if not scan:
                return _copied(key)
            blocks = [b for _, b in _blocks_from_words(tl, words)]
            return cleaner.clean_markdown(tl.render(_masked(tl, pr, page, blocks)))
        W, H = im.size
        regions = []   # (box in points, text)
        taken: set[int] = set()
        for (x0, y0, x1, y1) in tables:
            px, py = TATR_PAD * W, TATR_PAD * H
            cx0, cy0, cx1, cy1 = max(0, x0 - px), max(0, y0 - py), min(W, x1 + px), min(H, y1 + py)
            crop = im.crop((int(cx0), int(cy0), int(cx1), int(cy1)))
            rows = sorted(((b[1] + cy0) * scale, (b[3] + cy0) * scale)
                          for lab, s, b in detect(str_m, crop, 1000, TATR_ROW) if lab == "table row")
            box = (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
            inside = [i for i, w in enumerate(words) if i not in taken
                      and box[0] <= (w.x0 + w.x1) / 2 <= box[2] and box[1] <= (w.y0 + w.y1) / 2 <= box[3]]
            taken.update(inside)
            regions.append((box, inside, _table_text([words[i] for i in inside], rows)))
        # Tesseract's blocks, table words removed, each table placed before the first
        # block (in Tesseract's order) that had a word inside it.
        first_block = {}
        for n, (_box, inside, _t) in enumerate(regions):
            ids = sorted({words[i].line[0] for i in inside})
            first_block[n] = ids[0] if ids else None
        rest = [w for i, w in enumerate(words) if i not in taken]
        order = list(dict.fromkeys(w.line[0] for w in words))
        by_block = dict(_blocks_from_words(tl, rest))
        out, segment = [], []
        placed = set()
        for b in order:
            for n, fb in first_block.items():
                if fb == b and n not in placed:
                    if segment:
                        out.append(tl.render(_masked(tl, pr, page, segment)))
                        segment = []
                    out.append(regions[n][2])
                    placed.add(n)
            if b in by_block:
                segment.append(by_block[b])
        if segment:
            out.append(tl.render(_masked(tl, pr, page, segment)))
        out += [regions[n][2] for n in range(len(regions)) if n not in placed and regions[n][2]]
        return cleaner.clean_markdown("\n\n".join(t for t in out if t))

    return run


# --- PP-StructureV3 ------------------------------------------------------------------------

def ppstructure_engine():
    _tl, _pr, cleaner = _parsers()
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "huggingface")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import PPStructureV3

    pipe = PPStructureV3(lang="en", cpu_threads=int(os.environ.get("PADDLE_THREADS", "4")), use_doc_orientation_classify=False, use_doc_unwarping=False,
                         use_textline_orientation=False, use_seal_recognition=False,
                         use_formula_recognition=False, use_chart_recognition=False)

    def run(data: bytes, key: str) -> str:
        page = pymupdf.open(stream=data, filetype="pdf")[0]
        img = _image(page)[:, :, ::-1].copy()
        res = pipe.predict(img)
        md = "\n\n".join(r.markdown["markdown_texts"] for r in res)
        return cleaner.clean_markdown(md)

    return run
