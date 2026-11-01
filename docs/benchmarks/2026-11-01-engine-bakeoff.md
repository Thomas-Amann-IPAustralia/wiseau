# Engine bake-off — 2026-11-01 (partial; stopped early at the user's request)

Inputs: the three `pdf-test/` journals (V16.3: 108 born-digital pages; V2.1: 28 and
V4.2: 32 scanned pages). Hardware: 4 vCPU Xeon (AMX/bf16), 15 GB, no GPU.
Scored on a 10-page sample of hard pages (V16 p11/25/34/90; V2 p6/17/20; V4 p7/11/26).
References: V16 = PDF text layer in reading order; scans = hand-verified transcriptions.
Metrics: `wer` = word edit distance / ref length (reading order counts);
`bow_r`/`bow_p` = word recall/precision; `num_*` = the same for tokens with digits.

| Engine | Pages scored | wer | bow_r | bow_p | num_r | num_p |
|---|---|---|---|---|---|---|
| PyMuPDF + Tesseract (default) — digital | 4 | 0.024 | 0.995 | 0.977 | 1.000 | 0.960 |
| PyMuPDF + Tesseract — scanned | 6 | 0.676 | 0.930 | 0.941 | 0.837 | 0.864 |
| docling 2.145 (EasyOCR) — digital | 4 | 0.478 | 0.997 | 0.985 | 0.995 | 0.992 |
| docling — scanned | 6 | 0.221 | 0.852 | 0.916 | 0.680 | 0.812 |
| GLM-OCR (SDK + layout, CPU) — digital | 2 | 0.280 | 0.745 | 0.948 | 0.810 | 0.907 |
| GLM-OCR — scanned | 0 | not reached (run stopped) | | | | |
| Gemini 3.8 Flash (cloud) — digital | 4 | 0.032 | 0.976 | 0.997 | 0.995 | 1.000 |
| Gemini 3.8 Flash — scanned | 6 | 0.010 | 0.998 | 0.998 | 0.993 | 0.993 |
| GPT-5.4 (cloud) — digital | 4 | 0.014 | 0.998 | 0.999 | 0.995 | 1.000 |
| GPT-5.4 — scanned | 5 | 0.024 | 0.981 | 0.978 | 0.947 | 0.943 |

Speed (whole documents unless noted; CPU runs partly contended):
PyMuPDF 47 s / 95 s / 153 s; docling 26 / 19 / 26 min; GLM-OCR 11–44 min per page
contended, ~12 tok/s uncontended (minutes per dense page); Gemini Flash ~9 s/page;
GPT-5.4 ~15 s/page (cloud, parallel).

Observations
- GLM-OCR, V16 p11 (dotted-leader index): one region looped to the 4096-token cap and
  the left column was lost (recall 0.60); PBR symbol emitted as LaTeX.
- docling: mangled the V16 p11 index into a broken table; merged two side-by-side
  contact tables on p90 (phone numbers next to the wrong names); EasyOCR garbled the
  typewriter table on V2 p17 ("very Targe").
- GPT-5.4 substituted plausible text on scans: "Wonthaggi" for *Monbulk*, "Nadine"
  for *Neshka*, std dev 2.2 → 0.2, "Granted(s)" for *Grantee(s)*.
- Tesseract reads the right words but interleaves columns (high wer).
- Gemini 3.8 Flash was the most faithful on every scanned page scored.

Not done: GLM-OCR on scanned pages; GPT on 17 V4 pages (credits ran out); Gemini 3.1
Pro (daily quota). Reference drafts for scans started from GPT output and were
corrected against the images, which if anything biases toward GPT.
