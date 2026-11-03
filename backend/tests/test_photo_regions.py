"""Tests for photo-region detection on scanned pages (ADR-037).

The pages are built in memory with PyMuPDF drawing primitives, so these need no
Tesseract: they check the mask and the noise rule, not the OCR.
"""

from __future__ import annotations

import pymupdf

from parsers import photo_regions
from parsers.photo_regions import is_photo_noise, photo_mask, reads_as_words
from parsers.reading_order import Line

PHOTO = pymupdf.Rect(72, 100, 300, 330)     # a mid-grey "photograph"
BANNER = pymupdf.Rect(72, 700, 520, 720)    # a thin mid-grey band


def _page(*, grey_photo: bool = True, black_photo: bool = False, banner: bool = False) -> pymupdf.Page:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    if grey_photo:
        page.draw_rect(PHOTO, color=None, fill=(0.5, 0.5, 0.5))
    if black_photo:
        page.draw_rect(PHOTO, color=None, fill=(0, 0, 0))
    if banner:
        page.draw_rect(BANNER, color=None, fill=(0.45, 0.6, 0.45))
    for i in range(20):  # ordinary black body text, which must never count as a photo
        page.insert_text((320, 110 + 14 * i), "Body text of the right hand column, line %d." % i, fontsize=10)
    return page


def _line(rect: pymupdf.Rect, text: str) -> Line:
    return Line(rect.x0, rect.y0, rect.x1, rect.y1, text)


def test_a_mid_grey_region_is_found_and_body_text_is_not():
    mask = photo_mask(_page())
    assert mask.coverage(_line(pymupdf.Rect(100, 150, 250, 162), "x")) > 0.9
    assert mask.coverage(_line(pymupdf.Rect(320, 110, 560, 122), "x")) == 0.0


def test_a_page_without_photos_has_an_empty_mask():
    assert not photo_mask(_page(grey_photo=False)).cells


def test_a_small_coloured_block_is_not_a_photo():
    # A coloured heading or rule makes only a small tone region.
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(72, 100, 160, 116), color=None, fill=(0.45, 0.6, 0.45))
    assert not photo_mask(page).cells


def test_words_on_a_full_width_coloured_banner_survive():
    # A full-width band is large enough to count as a photo region, so its text
    # must survive on the strength of reading as words.
    mask = photo_mask(_page(grey_photo=False, banner=True))
    banner_line = _line(pymupdf.Rect(80, 703, 510, 717), "CLOSING DATE FOR SEPTEMBER ISSUE: 24 JULY 1991")
    assert mask.coverage(banner_line) >= photo_regions.LINE_IN_PHOTO
    assert not is_photo_noise(banner_line, mask)


def test_a_near_black_photo_is_not_detected():
    # A known limit, recorded in ADR-037: only mid-tone photographs are found.
    assert not photo_mask(_page(grey_photo=False, black_photo=True)).cells


def test_garbage_inside_a_photo_is_noise():
    mask = photo_mask(_page())
    assert is_photo_noise(_line(pymupdf.Rect(100, 150, 250, 162), "ie ST hee se ng"), mask)
    assert is_photo_noise(_line(pymupdf.Rect(100, 200, 140, 212), "4"), mask)


def test_real_words_inside_a_photo_region_are_kept():
    # Text printed on a coloured band reads as words, so it survives.
    mask = photo_mask(_page())
    line = _line(pymupdf.Rect(80, 150, 290, 162), "CLOSING DATE FOR SEPTEMBER ISSUE")
    assert not is_photo_noise(line, mask)


def test_garbage_outside_a_photo_is_kept():
    # Outside a photo, short tokens are usually table cells; never drop them.
    mask = photo_mask(_page())
    assert not is_photo_noise(_line(pymupdf.Rect(320, 500, 360, 512), "ee 4"), mask)


def test_reads_as_words():
    assert reads_as_words("a medium large diameter double flower")
    assert reads_as_words("Application No. 88/019 received")
    assert not reads_as_words("ie ST hee se ng")
    assert not reads_as_words("owe Gaoam")          # too few words
    assert not reads_as_words("")


def test_the_mask_is_deterministic():
    a, b = photo_mask(_page()), photo_mask(_page())
    assert a.cells == b.cells and a.scale == b.scale
