"""Reading-order regions for the 10 bake-off sample pages.

Each region is (x0, y0, x1, y1) as fractions of the page, listed in reading
order. They define the *reference* reading order only: references are built by
reading each region top-to-bottom, line by line (left-to-right within a line,
so table rows read across). Photographs are left out of the scanned pages'
regions (text printed inside a photo is not part of the reference).
"""
from __future__ import annotations

DOCS = {
    "V16": "pdf-test/PBR-PlantVarieties_Volume-16-Number-3.pdf",
    "V2": "pdf-test/PBR-PlantVarieties_Volume-2-Number-1.pdf",
    "V4": "pdf-test/PBR-PlantVarieties_Volume-4-Number-2.pdf",
}

# 1-based page numbers (the same pages as the 2026-11-01 bake-off).
SAMPLE = {"V16": [11, 25, 34, 90], "V2": [6, 17, 20], "V4": [7, 11, 26]}

HEADER = (0.0, 0.0, 1.0, 0.085)
LEFT = (0.0, 0.085, 0.5, 0.93)
RIGHT = (0.5, 0.085, 1.0, 0.93)
FOOTER = (0.0, 0.93, 1.0, 1.0)

REGIONS: dict[str, list[tuple[float, float, float, float]]] = {
    # Born-digital (text layer).
    "V16_p11": [HEADER, LEFT, RIGHT, FOOTER],
    "V16_p25": [HEADER, (0.0, 0.085, 0.9, 0.70), (0.0, 0.70, 0.5, 0.93),
                (0.5, 0.70, 0.9, 0.93), (0.9, 0.085, 1.0, 0.93), FOOTER],
    "V16_p34": [HEADER, LEFT, RIGHT, FOOTER],
    "V16_p90": [HEADER, LEFT, RIGHT, FOOTER],
    # Scanned (transcribed by hand; regions used to draft and to check order).
    "V2_p6": [(0.0, 0.0, 0.5, 0.95), (0.5, 0.0, 1.0, 0.95), (0.0, 0.95, 1.0, 1.0)],
    "V2_p17": [(0.0, 0.0, 1.0, 1.0)],
    "V2_p20": [(0.0, 0.0, 0.5, 0.95), (0.5, 0.0, 1.0, 0.95), (0.0, 0.95, 1.0, 1.0)],
    "V4_p7": [(0.0, 0.05, 0.5, 0.93), (0.5, 0.05, 1.0, 0.405),
              (0.5, 0.585, 1.0, 0.93), (0.85, 0.93, 1.0, 1.0)],
    "V4_p11": [(0.0, 0.495, 0.5, 0.93),
              (0.5, 0.05, 1.0, 0.93), (0.85, 0.93, 1.0, 1.0)],
    "V4_p26": [(0.0, 0.05, 0.5, 0.93), (0.5, 0.05, 1.0, 0.93), (0.0, 0.93, 1.0, 1.0)],
}
