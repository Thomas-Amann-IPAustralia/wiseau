"""Cache the ADR-038 guard text and the psm 3 text of scanned pages (ADR-042).

For every scanned page of the scored sets, all of P85, and every page of the
documents named in ALLDOCS (comma-separated regions.DOCS keys), store both texts
the default path chooses between, after scanner-band cleanup and the photo mask,
exactly as ocr.TesseractEngine computes them. guard_eval.py then tests counting
rules offline. Resumable: pages already in OUT are skipped (delete an entry whose
psm3 is null, i.e. a timeout, to redo it).

  ALLDOCS=P63a,P63b,P97,P00,WO06,V5N1,V5N3,V4N1,V2,V4 python benchmarks/bakeoff/guard_cache.py OUT.json
"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend")); sys.path.insert(0, str(ROOT / "benchmarks/bakeoff"))
import pymupdf
from regions import DOCS, SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L
from parsers import ocr, borders, photo_regions, tesseract_layout, file_parser
out = Path(sys.argv[1]); cache = json.loads(out.read_text()) if out.exists() else {}
todo = []
for s in (SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L):
    for k, ns in s.items():
        todo += [(k, n) for n in ns]
todo += [(("P85"), n) for n in range(1, 27)]
import os
for k in os.environ.get("ALLDOCS","").split(","):
    if k: todo += [(k, n) for n in range(1, pymupdf.open(ROOT / DOCS[k]).page_count + 1)]
seen = set()
for k, n in todo:
    key = f"{k}_p{n}"
    if key in seen or key in cache: continue
    seen.add(key)
    doc = pymupdf.open(ROOT / DOCS[k]); page = doc[n - 1]
    if not file_parser._page_needs_ocr(page): continue
    cleaned = borders.cleaned_page(page)
    if cleaned is not None: _d, page = cleaned
    mask = photo_regions.photo_mask(page)
    guard = ocr.TesseractEngine._geometric_text(page, dpi=300, language="eng", mask=mask)
    text = tesseract_layout.read_page(page, dpi=300, language="eng", mask=mask)
    cache[key] = {"guard": guard, "psm3": text}
    out.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
    print(key, flush=True)
