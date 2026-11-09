"""Image preprocessing variants for the ablation (roadmap 14 item 1.2).

Each variant takes a one-page PDF, renders the page at the OCR resolution, changes
the pixels, and returns a new one-page PDF whose only content is the changed image,
placed so that rendering it at the same resolution gives exactly those pixels back.
The default path then OCRs that page as it would any scan.

Why a re-render and not an edit of the embedded image: most PBR scans are several
image strips, and the scans are a mix of 1-bit JBIG2/CCITT, greyscale and colour
JPEG/JPX. A page-level image is the one thing every scan has. The round trip is
pixel-exact at 300 dpi (checked on V2, V4, V2N2 and P85 pages), but the default
path also renders the page at lower resolutions (borders.py at 36 dpi, the photo
mask), and those see a different source. So the control variant ``ident`` does the
round trip and changes nothing: anything ``ident`` moves is the round trip, not the
processing. Compare each variant with ``ident``, not only with the original.

Variants (``VARIANTS``):

* ``ident`` - the round trip, nothing changed (the control).
* ``deskew`` - rotate by the skew angle the projection profile finds, if at least
  ``MIN_SKEW_DEG``.
* ``norm`` - deskew, then flatten the background (divide by a large-kernel
  closing) and stretch contrast to the 1st-99th percentiles.
* ``sauvola`` - Sauvola thresholding (window ``SAUVOLA_WINDOW``, k ``SAUVOLA_K``).
* ``denoise`` - non-local-means denoise (h ``NLM_H``), then remove dark specks of
  at most ``SPECK_AREA`` pixels.

``measure`` logs the page features a per-page rule could use: native image type and
resolution, skew, contrast (1st-99th percentile grey spread), blur (variance of the
Laplacian) and speckle (dark specks of at most ``SPECK_AREA`` pixels per megapixel).

OpenCV and NumPy only; every operation is deterministic for fixed pixels.
"""
from __future__ import annotations

import cv2
import numpy as np
import pymupdf

DPI = 300
MIN_SKEW_DEG = 0.1
MAX_SKEW_DEG = 3.0
SKEW_STEP_DEG = 0.05
SAUVOLA_WINDOW = 31
SAUVOLA_K = 0.2
SAUVOLA_R = 128.0
NLM_H = 10
SPECK_AREA = 4
BG_KERNEL = 41

VARIANTS = ("ident", "deskew", "norm", "sauvola", "denoise")


def render(page: pymupdf.Page) -> tuple[np.ndarray, int]:
    """The page at ``DPI``, as an H x W x n uint8 array (n = 3, or 1 for grey sources)."""
    pix = page.get_pixmap(dpi=DPI, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n).copy(), pix.n


def to_pdf(img: np.ndarray) -> bytes:
    """A one-page PDF that renders back to exactly ``img`` at ``DPI``."""
    h, w = img.shape[:2]
    if img.ndim == 2:
        img = img[:, :, None]
    cs = pymupdf.csGRAY if img.shape[2] == 1 else pymupdf.csRGB
    pix = pymupdf.Pixmap(cs, w, h, np.ascontiguousarray(img).tobytes(), False)
    out = pymupdf.open()
    page = out.new_page(width=w * 72 / DPI, height=h * 72 / DPI)
    page.insert_image(page.rect, pixmap=pix)
    return out.tobytes()


def grey(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 1:
        return img[:, :, 0]
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def _ink(g: np.ndarray) -> np.ndarray:
    """Dark pixels (Otsu), as a 0/1 array."""
    _, b = cv2.threshold(g, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return b


def skew_angle(g: np.ndarray) -> float:
    """The rotation (degrees, counter-clockwise positive) that makes text lines level.

    Projection profile: rotate a 75 dpi ink map through +-MAX_SKEW_DEG and keep the
    angle whose row sums vary most (level lines give sharp peaks). Ties go to the
    smallest angle, so a level page reports 0.
    """
    small = cv2.resize(g, (g.shape[1] // 4, g.shape[0] // 4), interpolation=cv2.INTER_AREA)
    ink = _ink(small).astype(np.float32)
    h, w = ink.shape
    best, best_score = 0.0, -1.0
    steps = int(round(MAX_SKEW_DEG / SKEW_STEP_DEG))
    for i in sorted(range(-steps, steps + 1), key=lambda s: (abs(s), s)):
        a = i * SKEW_STEP_DEG
        m = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
        rot = cv2.warpAffine(ink, m, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        score = float(np.var(rot.sum(axis=1)))
        if score > best_score * (1 + 1e-9):
            best, best_score = a, score
    return best


def deskew(g: np.ndarray, angle: float | None = None) -> np.ndarray:
    angle = skew_angle(g) if angle is None else angle
    if abs(angle) < MIN_SKEW_DEG:
        return g
    h, w = g.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(g, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)


def normalise(g: np.ndarray) -> np.ndarray:
    """Flatten the background and stretch contrast to the 1st-99th percentiles."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (BG_KERNEL, BG_KERNEL))
    bg = cv2.morphologyEx(g, cv2.MORPH_CLOSE, kernel)
    flat = np.clip(g.astype(np.float32) / np.maximum(bg.astype(np.float32), 1) * 255, 0, 255)
    lo, hi = np.percentile(flat, [1, 99])
    if hi - lo < 1:
        return flat.astype(np.uint8)
    return np.clip((flat - lo) * 255 / (hi - lo), 0, 255).astype(np.uint8)


def sauvola(g: np.ndarray) -> np.ndarray:
    f = g.astype(np.float64)
    win = (SAUVOLA_WINDOW, SAUVOLA_WINDOW)
    mean = cv2.boxFilter(f, -1, win, borderType=cv2.BORDER_REFLECT)
    sq = cv2.boxFilter(f * f, -1, win, borderType=cv2.BORDER_REFLECT)
    std = np.sqrt(np.maximum(sq - mean * mean, 0))
    thresh = mean * (1 + SAUVOLA_K * (std / SAUVOLA_R - 1))
    return np.where(f > thresh, 255, 0).astype(np.uint8)


def denoise(g: np.ndarray) -> np.ndarray:
    d = cv2.fastNlMeansDenoising(g, None, h=NLM_H, templateWindowSize=7, searchWindowSize=21)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(_ink(d), connectivity=8)
    small = np.zeros(n, bool)
    small[1:] = stats[1:, cv2.CC_STAT_AREA] <= SPECK_AREA
    d = d.copy()
    d[small[labels]] = 255
    return d


def apply(variant: str, data: bytes) -> bytes:
    """``data`` (a one-page PDF) with ``variant`` applied to its pixels."""
    page = pymupdf.open(stream=data, filetype="pdf")[0]
    img, _n = render(page)
    if variant == "ident":
        return to_pdf(img)
    g = grey(img)
    if variant == "deskew":
        return to_pdf(deskew(g))
    if variant == "norm":
        return to_pdf(normalise(deskew(g)))
    if variant == "sauvola":
        return to_pdf(sauvola(g))
    if variant == "denoise":
        return to_pdf(denoise(g))
    raise ValueError(variant)


def native(page: pymupdf.Page) -> tuple[str, int]:
    """The page's scan type (``bilevel``/``grey``/``colour``/``mixed``/``none``) and the
    native dpi of its largest image."""
    kinds, best = set(), (0, 0)
    for info in page.get_image_info(xrefs=True):
        bpc, cs = info.get("bpc", 8), info.get("colorspace", 1)
        kinds.add("bilevel" if bpc == 1 else ("grey" if cs == 1 else "colour"))
        bbox = pymupdf.Rect(info["bbox"])
        area = bbox.width * bbox.height
        if area > best[0] and bbox.width > 0:
            best = (area, round(info["width"] / (bbox.width / 72)))
    if not kinds:
        return "none", 0
    return (kinds.pop() if len(kinds) == 1 else "mixed"), best[1]


def measure(data: bytes) -> dict:
    """Page features for a per-page rule: type, dpi, skew, contrast, blur, speckle."""
    page = pymupdf.open(stream=data, filetype="pdf")[0]
    kind, dpi = native(page)
    g = grey(render(page)[0])
    p1, p99 = np.percentile(g, [1, 99])
    lap = cv2.Laplacian(g, cv2.CV_64F)
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(_ink(g), connectivity=8)
    specks = int((stats[1:, cv2.CC_STAT_AREA] <= SPECK_AREA).sum()) if n > 1 else 0
    return {
        "type": kind, "native_dpi": dpi, "skew_deg": round(skew_angle(g), 2),
        "contrast": int(p99 - p1), "blur_lapvar": round(float(lap.var()), 1),
        "specks_per_mpx": round(specks / (g.size / 1e6), 1),
    }


def main() -> None:
    """Write every scanned scored page's features to ``survey/preprocess-features.json``."""
    import json
    import sys
    from pathlib import Path

    here = Path(__file__).parent
    sys.path.insert(0, str(here))
    from regions import DOCS, SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L

    out = {}
    for sample in (SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L):
        for key, nums in sample.items():
            doc = pymupdf.open(here.parent.parent / DOCS[key])
            for n in nums:
                one = pymupdf.open()
                one.insert_pdf(doc, from_page=n - 1, to_page=n - 1)
                if native(one[0])[0] == "none":
                    continue
                out[f"{key}_p{n}"] = measure(one.tobytes())
    path = here / "survey" / "preprocess-features.json"
    path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"{len(out)} pages -> {path}")


if __name__ == "__main__":
    main()
