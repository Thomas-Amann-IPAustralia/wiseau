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
    "V5N1": "pdf-test/Volume 5 Number 1.pdf",
    "V5N3": "pdf-test/Volume 5 Number 3.pdf",
    "V4N1": "pdf-test/Volume 4 Number 1.pdf",
    "V2N2": "pdf-test/Volume 2 Number 2.pdf",
    "V1N3": "pdf-test/Volume 1 Number 3.pdf",
    "P63a": "pdf-test/Extra-4-Broad-Test/1963026099 B0 specification (1).pdf",
    "P63b": "pdf-test/Extra-4-Broad-Test/1963026103 B0 specification (1).pdf",
    "P85": "pdf-test/Extra-4-Broad-Test/1985043407 A0 specification (1).pdf",
    "P97": "pdf-test/Extra-4-Broad-Test/1997024746 B0 specification (1).pdf",
    "P00": "pdf-test/Extra-4-Broad-Test/2000028878 B2 specification (1).pdf",
    "WO06": "pdf-test/Extra-4-Broad-Test/WO_2006070397_A1 (1).pdf",
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
# Set D (2026-11-06): a held-out set from two journals no earlier set has touched
# (Vol 5 No 1 and Vol 5 No 3, both image-only). Same blind procedure; groups from a
# Tesseract survey of their 58 pages; seed 20261107; 3 + 3 + 4 + 2 pages. No duplicate
# scans: every drawn page maps to a distinct printed page number.
SAMPLE_D = {"V5N1": [9, 11, 18, 19, 20, 24], "V5N3": [4, 12, 17, 19, 23, 24]}
GROUPS_D = {
    "two-column prose": ["V5N3_p12", "V5N3_p17", "V5N3_p23"],
    "tables": ["V5N1_p11", "V5N1_p20", "V5N3_p19"],
    "two-column + data": ["V5N1_p9", "V5N1_p18", "V5N1_p19", "V5N3_p24"],
    "single column": ["V5N1_p24", "V5N3_p4"],
}
# Set F (2026-11-08): the held-out PBR set for Phase 14 (roadmap 1.1b) and for the
# text-layer trust check (0.2). Drawn from Vol 4 No 1 (image-only) and Vol 2 No 2
# (scans with a hidden Acrobat 8 OCR layer, which the references never used), seed
# 20261108, quota 3/5/4/2 weighted to tables, split by native resolution (7 pages at
# 150-200 dpi, 7 at about 300). Survey and draw: survey/set-f/. Transcribed blind
# from the page images with printed punctuation kept (‘ ’ “ ” – — as printed).
SAMPLE_F = {"V2N2": [3, 4, 6, 10, 12, 16, 31, 32], "V4N1": [7, 13, 21, 22, 25, 27]}
GROUPS_F = {
    "two-column prose": ["V2N2_p31", "V2N2_p4", "V4N1_p22"],
    "tables": ["V2N2_p10", "V2N2_p12", "V2N2_p16", "V2N2_p32", "V4N1_p25"],
    "two-column + data": ["V2N2_p6", "V4N1_p13", "V4N1_p21", "V4N1_p7"],
    "single column": ["V2N2_p3", "V4N1_p27"],
}
# Set E (2026-11-08, transcribed 2026-11-08): the do-no-harm set from other
# publications. Two pages from each of five Australian patent specifications (1963-2000,
# scans with an invisible OCR layer of unknown producer) and one image-only WO
# publication (2006), seed 20261109, `--per-doc 2`. Survey and draw: survey/set-e/.
# Transcribed blind with set F's conventions, plus: line numbers *typed* in a patent's
# margin are kept at the start of their line (they are printed text); handwritten
# margin numbers (P63a p7) and chemical structure drawings (P97) are left out, like
# text inside a photograph. No drawn page is a duplicate scan (image check).
SAMPLE_E = {"P63a": [3, 7], "P63b": [14, 20], "P85": [6, 21], "P97": [13, 15], "P00": [4, 15],
            "WO06": [4, 16]}
GROUPS_E = {  # by publication: the point of set E is breadth, not layout
    "1963 typewritten": ["P63a_p3", "P63a_p7", "P63b_p14", "P63b_p20"],
    "1985 typewritten": ["P85_p6", "P85_p21"],
    "1997-2000 typeset": ["P97_p13", "P97_p15", "P00_p4", "P00_p15"],
    "2006 WO (image-only)": ["WO06_p4", "WO06_p16"],
}
# Set L (2026-11-08): the layer audit. A seeded random sample (seed 20261110,
# `--per-doc 4`) of pages from the two PBR journals whose scans carry a hidden
# "Adobe Acrobat 8.13 Paper Capture" OCR layer, Vol 1 No 3 and Vol 2 No 2 (set F's
# pages excluded). Its job is an unbiased estimate of how wrong that layer is, so the
# draw is plain random, not stratified, and Vol 2 No 2's known coverage failure
# (p23) was left in when the draw picked it. Survey and draw: survey/set-l/.
# Transcribed blind from the page images with set F's conventions.
SAMPLE_L = {"V1N3": [7, 12, 16, 19], "V2N2": [23, 29, 30, 33]}
GROUPS_L = {
    "two-column prose": ["V1N3_p7", "V1N3_p19", "V2N2_p33"],
    "tables": ["V1N3_p16", "V2N2_p23"],
    "single column": ["V1N3_p12", "V2N2_p29", "V2N2_p30"],
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
