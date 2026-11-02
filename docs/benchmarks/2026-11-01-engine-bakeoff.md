# Engine bake-off — 2026-11-01

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
| GLM-OCR — scanned | 6 | 0.110 | 0.912 | 0.948 | 0.744 | 0.744 |
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

GLM-OCR on scans, per page (uncontended, 46–267 s/page): prose pages fine
(word recall 0.96–0.99, wer 0.02–0.10); tables poor — V2 p17 typewriter table
recall 0.64 / numbers found 0.46 (≈ docling), V4 p7/p11 numbers found 0.74/0.49.
Even on prose, 11–16% of the numbers it wrote on V2 p20 and V4 p26 were not on the
page. Two more regions ran away to the 4096-token cap. It is the best *self-hosted*
engine on scans by reading order, but not reliable on tables, and 4–14× slower
than PyMuPDF + Tesseract.

Open question: the model was served by our own transformers server
(`glm-ocr/model_server.py`), not the vLLM/SGLang path the authors support, and
without MTP decoding. Some of the runaways/errors may be the serving setup, not the
model. **Answered 2026-11-02** ([diagnosis](2026-11-02-glm-ocr-diagnosis.md)): the V16 p11
runaway was the SDK's `repetition_penalty=1.1` (it loops in float32 too); at 1.0 the
page converts in 2 min with nothing lost. The service now defaults to 1.0.
**Re-scored 2026-11-02** ([re-run](2026-11-02-bakeoff-rerun.md)): at 1.0, GLM-OCR on scans
reaches wer 0.103, word recall 0.930 and number recall 0.895 (was 0.744), on committed references.

Not done: GPT on 17 V4 pages (credits ran out); Gemini 3.1
Pro (daily quota). Reference drafts for scans started from GPT output and were
corrected against the images, which if anything biases toward GPT.
