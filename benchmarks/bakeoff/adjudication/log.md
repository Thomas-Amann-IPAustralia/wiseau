# Set B adjudication log

Every change made to a set-B reference after the blind transcription (commit
`88b99f4`). "Trigger" says what prompted the re-check; "Image" is what a zoomed
crop of the page showed. A change is made only when the image shows the
transcription was wrong.

| Page | Was | Now | Trigger | Image |
|---|---|---|---|---|
| V2 p23 | the | tne | self-check of a word flagged as uncertain while transcribing (before any engine output was read) | printed "tne" (broken h); kept as printed, like "cserrations" on V2 p9 |

## Round 1: Tesseract and EasyOCR (docling) agree against the reference

Run: `disagreements.py --set b --min 2`, before GLM-OCR had finished. Ten words
were listed. All were checked against the page crops, and **none was changed**:

| Page | Reference | Engines read | Image |
|---|---|---|---|
| V2 p14 | RHS 55A | 554 | "55A" printed clearly; A/4 confusion |
| V2 p14 | RHS 155D | 1550 | "155D"; D/0 confusion |
| V2 p14 | RHS 144B | 1448 | "144B"; B/8 confusion |
| V2 p14 | 5 | rhs | alignment artefact (cells read in a different order) |
| V2 p23 | A new | anew | two words in print |
| V4 p22 | inside | nside | faint but printed "i" |
| V4 p22 | mm | m | "mm" printed, second m faint |
| V4 p22 | few | ew | faint but printed "f" |
| V4 p25 | Lawson | 051 | alignment artefact (list read in a different order) |
| V4 p27 | Ian (twice) | lan | the name Ian; sans-serif capital I |

## Round 2: GLM-OCR added

Run: `disagreements.py --set b --min 2` with all four engines. Eight more words
were listed, and **none was changed**:

| Page | Reference | Engines read | Image / reason |
|---|---|---|---|
| V2 p14 | FLOWER COLOURS row (8 words) | same words, other order | GLM-OCR and Tesseract psm 3 read each two-line cell whole ("milky pink with paler tips"). The convention, as in set A, reads each printed line across the row. This is an order difference, not a misreading: it costs those engines wer, but not bow/num. |
| V2 p14 | 55D (×2) | width | alignment artefact from the same cell-order difference |
| V4 p8 | 54 mm, 0 cm, 18 mm | 54mm, 0cm, 18mm | printed with a space; the engines joined number and unit |
| V4 p27 | McIver | Mclver | the name McIver; sans-serif capital I |

**Outcome:** after both rounds, the only change to any blind transcription is
V2 p23's "the" → "tne", made on self-check before engine output was read. No
engine-prompted check found a transcription error.
