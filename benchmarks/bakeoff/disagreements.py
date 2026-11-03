"""List where the engines disagree with a hand transcription, for adjudication.

Set B's references were transcribed from the page images alone. This script
aligns each engine's output to a reference word by word, with the scorer's own
tokens, and reports every reference word that some engine replaced with a
different word. Agreement is counted per *recogniser family*: the two Tesseract
runs share one recogniser, so they count once; docling (EasyOCR) and GLM-OCR are
the other two. The more families agree on the same replacement, the likelier the
reference is wrong there. Each reported word is then checked against a
zoomed crop of the page image, and the reference is changed only when the image
shows the transcription was wrong. Engines never decide; they only point.

  python benchmarks/bakeoff/disagreements.py V4_p8 [--min 2]   # one page
  python benchmarks/bakeoff/disagreements.py --set b [--min 2]  # every set-B page (or --set c)
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from regions import SAMPLE_B, SAMPLE_C  # noqa: E402
from score import tokens  # noqa: E402
from vote import align  # noqa: E402

ENGINES = ["pymupdf", "tesseract-psm3", "docling", "glm-ocr"]
FAMILY = {"pymupdf": "tesseract", "tesseract-psm3": "tesseract", "docling": "easyocr", "glm-ocr": "glm"}


def disagreements(key: str, minimum: int) -> list[tuple[int, str, dict[str, str]]]:
    ref = tokens((HERE / "references" / f"{key}.txt").read_text())
    alts: dict[int, dict[str, str]] = defaultdict(dict)
    for engine in ENGINES:
        path = HERE / "outputs" / engine / f"{key}.md"
        if not path.exists():
            continue
        slot, _ = align(ref, tokens(path.read_text()))
        for i, word in enumerate(slot):
            if word and word != ref[i]:
                alts[i][engine] = word
    rows = []
    for i in sorted(alts):
        families: dict[str, set[str]] = defaultdict(set)
        for engine, word in alts[i].items():
            families[word].add(FAMILY[engine])
        if max(len(f) for f in families.values()) >= minimum:
            rows.append((i, ref[i], alts[i]))
    return rows


def main() -> None:
    minimum = int(sys.argv[sys.argv.index("--min") + 1]) if "--min" in sys.argv else 2
    if "--set" in sys.argv:
        sample = SAMPLE_C if sys.argv[sys.argv.index("--set") + 1].lower() == "c" else SAMPLE_B
        keys = [f"{d}_p{n}" for d, ns in sample.items() for n in ns]
    else:
        keys = [sys.argv[1]]
    for key in keys:
        ref = tokens((HERE / "references" / f"{key}.txt").read_text())
        rows = disagreements(key, minimum)
        print(f"== {key}: {len(rows)} word(s) where >= {minimum} recogniser families agree on another reading")
        for i, word, alt in rows:
            context = " ".join(ref[max(0, i - 4):i]) + f" [{word}] " + " ".join(ref[i + 1:i + 4])
            print(f"   {context}   <- " + ", ".join(f"{e}: {w}" for e, w in alt.items()))


if __name__ == "__main__":
    main()
