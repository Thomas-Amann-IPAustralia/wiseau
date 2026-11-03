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

# Set B (2026-11-03): 10 more scanned pages, transcribed by hand from the page
# images *before* any engine was run on them (unlike set A's scans, which were
# corrected from Tesseract drafts). Drawn by seeded stratified random sampling
# over the 62 image-only pages, by layout group:
#   two-column prose  V2 p9, V2 p11, V2 p23    tables            V2 p14, V4 p32
#   two-column + data V4 p8, V4 p22, V4 p25    single column     V2 p2, V4 p27
# V2 p13 and V2 p16 were drawn first and replaced: V2's PDF scans four journal
# pages twice (p15=p5, p16=p6, p17=p13, p18=p14), and those two repeat set-A pages.
SAMPLE_B = {"V2": [2, 9, 11, 14, 23], "V4": [8, 22, 25, 27, 32]}
# Set C (2026-11-03): a held-out set for the two Tesseract fixes and the router,
# both of which were designed on sets A and B. Same blind procedure and layout
# groups; seed 20261106; excludes sets A and B and every duplicate scan of their pages.
SAMPLE_C = {"V2": [4, 8, 12, 21], "V4": [3, 10, 14, 17, 19, 23]}
GROUPS_C = {
    "two-column prose": ["V2_p4", "V2_p8", "V4_p17"],
    "tables": ["V2_p12", "V4_p19"],
    "two-column + data": ["V2_p21", "V4_p10", "V4_p14"],
    "single column": ["V4_p3", "V4_p23"],
}
GROUPS_B = {
    "two-column prose": ["V2_p9", "V2_p11", "V2_p23"],
    "tables": ["V2_p14", "V4_p32"],
    "two-column + data": ["V4_p8", "V4_p22", "V4_p25"],
    "single column": ["V2_p2", "V4_p27"],
}

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
