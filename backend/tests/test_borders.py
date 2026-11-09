"""Scanner-band cleanup (parsers/borders.py): what counts as a band, and what is painted."""
from __future__ import annotations

import pymupdf

from parsers import borders

W, H = 595, 842  # A4 in points


def _scan(*rects: tuple[float, float, float, float], text: str = "Body text that must survive") -> pymupdf.Page:
    """A one-page 'scan': black rectangles (fractions of the page) plus a line of text, flattened to an image."""
    src = pymupdf.open()
    page = src.new_page(width=W, height=H)
    for x0, y0, x1, y1 in rects:
        page.draw_rect(pymupdf.Rect(x0 * W, y0 * H, x1 * W, y1 * H), color=(0, 0, 0), fill=(0, 0, 0))
    page.insert_text((72, H / 2), text, fontsize=14)
    pix = page.get_pixmap(dpi=100)
    scan = pymupdf.open()
    out = scan.new_page(width=W, height=H)
    out.insert_image(out.rect, pixmap=pix)
    return pymupdf.open(stream=scan.tobytes(), filetype="pdf")[0]


def _pixels(page: pymupdf.Page, x0: float, y0: float, x1: float, y1: float) -> bytes:
    return page.get_pixmap(dpi=100, colorspace=pymupdf.csGRAY,
                           clip=pymupdf.Rect(x0 * W, y0 * H, x1 * W, y1 * H)).samples


TEXT = (0.1, 0.46, 0.6, 0.51)  # the line of body text in every fixture


def _ink(page: pymupdf.Page) -> int:
    """Dark pixels in the body-text line."""
    return sum(1 for v in _pixels(page, *TEXT) if v < 128)


def _dark_share(page: pymupdf.Page, x0: float, y0: float, x1: float, y1: float) -> float:
    pix = page.get_pixmap(dpi=36, colorspace=pymupdf.csGRAY,
                          clip=pymupdf.Rect(x0 * W, y0 * H, x1 * W, y1 * H))
    s = pix.samples
    return sum(1 for v in s if v < 80) / max(1, len(s))


def test_band_across_the_top_edge_is_found_and_painted_white():
    page = _scan((0.0, 0.02, 1.0, 0.07))
    assert len(borders.find_bands(page)) == 1

    _doc, clean = borders.cleaned_page(page)

    assert _dark_share(clean, 0.0, 0.02, 1.0, 0.07) < 0.01
    # the text in the middle of the page is untouched
    assert _ink(page) > 200
    assert _pixels(clean, *TEXT) == _pixels(page, *TEXT)


def test_band_down_one_side_is_found():
    page = _scan((0.93, 0.0, 1.0, 1.0))
    bands = borders.find_bands(page)

    assert len(bands) == 1 and not bands[0].horizontal


def test_black_banner_inside_the_page_is_not_a_band():
    # A full-width black bar a third of the way down: printed design, not the scanner.
    assert borders.find_bands(_scan((0.05, 0.30, 0.95, 0.36))) == []


def test_short_dark_block_in_a_corner_is_not_a_band():
    # A dark photo or logo in the margin is short, so it stays.
    assert borders.find_bands(_scan((0.0, 0.0, 0.2, 0.1))) == []


def test_thin_rule_is_not_a_band():
    assert borders.find_bands(_scan((0.05, 0.05, 0.95, 0.052))) == []


def test_a_frame_is_cleaned_without_whitening_the_page_inside_it():
    # A scan with a black surround on all four sides (P85 p17): one connected
    # region, top to bottom. Only the frame goes; the page inside it stays.
    frame = [(0.0, 0.0, 1.0, 0.05), (0.0, 0.95, 1.0, 1.0), (0.0, 0.0, 0.05, 1.0), (0.95, 0.0, 1.0, 1.0)]
    page = _scan(*frame)
    assert len(borders.find_bands(page)) == 1

    _doc, clean = borders.cleaned_page(page)

    assert _dark_share(clean, 0.0, 0.0, 1.0, 0.05) < 0.01
    assert _ink(page) > 200
    assert _pixels(clean, *TEXT) == _pixels(page, *TEXT)


def test_page_without_a_band_is_left_alone():
    assert borders.cleaned_page(_scan()) is None


def test_cleaning_is_deterministic():
    page = _scan((0.0, 0.9, 1.0, 0.96))
    a = borders.cleaned_page(page)[1].get_pixmap(dpi=72).samples
    b = borders.cleaned_page(page)[1].get_pixmap(dpi=72).samples

    assert a == b


def test_printed_header_bar_with_margins_at_both_ends_is_not_a_band():
    # The PBR journals print a black bar near the top, with white margin at each
    # end. A scanner band runs off the page instead.
    assert borders.find_bands(_scan((0.13, 0.06, 0.88, 0.09))) == []


def test_hairline_strip_along_the_very_edge_is_left_alone():
    # Within the outermost 4%: it reads as no words, and removing it only nudges
    # Tesseract's page segmentation.
    assert borders.find_bands(_scan((0.985, 0.0, 1.0, 1.0))) == []
