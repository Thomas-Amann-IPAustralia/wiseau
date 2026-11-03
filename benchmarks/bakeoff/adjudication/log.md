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
