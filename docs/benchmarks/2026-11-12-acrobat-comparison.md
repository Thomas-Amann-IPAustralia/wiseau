# Our OCR against Adobe Acrobat 26 (2026-11-12)

The user OCR'd five test PDFs with **Adobe Acrobat (64-bit) 26 Paper Capture**,
timed each run with a stopwatch, and asked for a comparison with our process.
No production code changed. No paid APIs were used.

**Verdict.**
- **Accuracy: ours is better.** This is measured on the 12 scanned pages that
  have blind hand-typed references (set D, Vol 5 No 1 and No 3):
  - Acrobat's text misses **3.6% of the words and 5.7% of the numbers**.
  - Our engines miss 1.9% and 2.7%, so Acrobat loses about twice as many.
  - In reading order, Acrobat's wer is 0.21, against 0.044 for Accurate and
    0.072 for Fast. Acrobat reads tables column by column and splits them from
    their captions.
  - **Acrobat wins on punctuation as printed.** It keeps ‘ ’ “ ” – — in 89%
    of places, against 72% for us. With the marks folded to ASCII, the two are
    level (0.965 against 0.956).
- **Speed on pure scans: Acrobat is quicker.** It takes 2.1–2.3 s a page.
  - Fast takes 2.9–3.1 s a page (about 1.35× Acrobat).
  - Accurate takes 7.8–8.5 s a page (about 3.7×).
  - The machines are different. Acrobat ran on a 4-core/8-thread i7-1185G7
    laptop (turbo to 4.8 GHz). We ran on a 4 vCPU 2.1 GHz Xeon with one
    thread per core, which is probably the slower machine. So "Fast is
    roughly Acrobat's speed" is the fair reading, but it is not measured.
- **On the other two documents, Acrobat was slower than both our engines:**
  - **Vol 2 No 2**, which already has an Acrobat 8 text layer: Acrobat took
    281 s, Accurate 297 s, and Fast 128 s.
  - **Vol 20 No 4**, born-digital with 371 pages: Acrobat took 17 min 23 s,
    Fast 32 s, and Accurate 32 s. Acrobat OCR'd the logos and table
    rules on 149 pages, and the 5,309 characters it added are mostly junk
    (`I I I I I`, `A11stralfan Go"crn meat`). We read the text layer that is
    already there.

## Material

| Document | Pages | Kind | Acrobat output supplied | References |
|---|---|---|---|---|
| Vol 5 No 1 | 31 | image-only scan (Acrobat 6 capture, no text) | yes | 6 pages (set D) |
| Vol 5 No 3 | 27 | image-only scan | yes | 6 pages (set D) |
| Vol 6 No 1 | 40 | scan; **original not in `pdf-test/`** | yes | none |
| Vol 2 No 2 | 40 | scan with an Acrobat 8 invisible layer | **no** (timed only) | 12 pages (sets F, L) |
| Vol 20 No 4 | 371 | born-digital (Acrobat Web Capture 7), 216 pages have images | yes | none |

Acrobat 26 writes an invisible text layer (render mode 3) under the page image.
It also **deskews and resamples the image**: for example, V5N1 p11 goes from
2480×3376 to 2463×3484. So its output's pixels are not the original's.

## Accuracy: set D (12 scanned pages, 7,148 reference words)

Engines:
- `hidden-layer@acrobat26` is Acrobat's text layer as stored
  (`page.get_text()`, no Markdown).
- `trust-layer@acrobat26` is what wiseau would return for Acrobat's PDF if it
  trusted the layer (the pre-ADR-040 path).
- `fast-cheap-300@acrobat26-pixels` is our Fast engine on Acrobat's resampled
  pixels.
- `image-default` (Accurate) and `fast-cheap-300` (Fast) are the committed
  outputs, re-checked byte for byte in this session.

| Engine | wer | cer | ord | word recall | word prec. | number recall | punct. recall | punct. (folded) |
|---|---|---|---|---|---|---|---|---|
| **Accurate** (image-default) | **0.044** | **0.027** | **0.112** | **0.981** | **0.986** | **0.973** | 0.721 | 0.956 |
| **Fast** (fast-cheap-300) | 0.072 | 0.052 | 0.143 | **0.981** | **0.986** | **0.973** | 0.721 | 0.956 |
| Acrobat 26, layer as is | 0.206 | 0.130 | 0.159 | 0.964 | 0.964 | 0.943 | **0.886** | **0.965** |
| Acrobat 26 through wiseau, layer trusted | 0.323 | 0.245 | 0.314 | 0.951 | 0.958 | 0.943 | 0.867 | – |
| Fast on Acrobat's pixels | 0.127 | 0.099 | 0.207 | 0.974 | 0.981 | 0.952 | 0.715 | 0.945 |

Per page, as wer / word recall / number recall:

| Page | Accurate | Fast | Acrobat 26 |
|---|---|---|---|
| V5N1_p9 | 0.012 / 0.992 / 0.972 | 0.012 / 0.992 / 0.972 | 0.132 / 0.983 / 0.977 |
| V5N1_p11 | 0.078 / 0.950 / 0.946 | 0.100 / 0.950 / 0.946 | 0.090 / 0.926 / 0.962 |
| V5N1_p18 | 0.024 / 0.981 / 0.965 | 0.299 / 0.981 / 0.965 | 0.244 / 0.948 / 0.912 |
| V5N1_p19 | 0.063 / 0.979 / 0.970 | 0.048 / 0.979 / 0.970 | 0.444 / 0.959 / 0.964 |
| V5N1_p20 | 0.050 / 0.967 / 0.966 | 0.033 / 0.967 / 0.966 | 0.185 / 0.962 / 0.986 |
| V5N1_p24 | 0.063 / 0.966 / 0.945 | 0.060 / 0.966 / 0.945 | 0.152 / **0.991** / **0.986** |
| V5N3_p4 | 0.009 / 0.993 / 1.000 | 0.009 / 0.993 / 1.000 | 0.054 / 0.974 / 0.889 |
| V5N3_p12 | 0.018 / 0.996 / 1.000 | 0.004 / 0.996 / 1.000 | 0.113 / 0.971 / 0.900 |
| V5N3_p17 | 0.057 / 0.972 / 0.967 | 0.036 / 0.972 / 0.967 | 0.111 / 0.938 / 0.967 |
| V5N3_p19 | 0.012 / 0.993 / 0.987 | 0.121 / 0.993 / 0.987 | 0.428 / 0.990 / 0.993 |
| V5N3_p23 | 0.005 / 0.997 / 0.980 | 0.005 / 0.997 / 0.980 | 0.053 / 0.989 / 0.920 |
| V5N3_p24 | 0.133 / 0.992 / 0.975 | 0.133 / 0.992 / 0.975 | 0.471 / 0.938 / 0.866 |

- **Word recall:** ours is higher on 11 of 12 pages. Acrobat wins on V5N1 p24.
- **Number recall:** Acrobat wins on 4 pages (p11, p19 and p20 of V5N1 by
  0.6–2 points, and p24 by 4). Ours wins on 7, by up to 12 points (V5N3 p24,
  p4, p12), and they tie on V5N3 p17.

What Acrobat got wrong (`word_errors.py hidden-layer@acrobat26 --set d`):
- **Table order.** Every table is read in a different order from its rows,
  and the caption ("Table of comparison of … varieties") is moved away from
  its table. This is most of the wer gap, because the words are present but
  out of order (V5N3 p19: word recall 0.990, wer 0.428).
- **Lost words.** Acrobat drops whole cells and headings: "grasslands gala
  grasslands matua", the "SDS polyacrylamide gel …" photo caption, and some
  number pairs.
- **1 / l / i confusion**, mainly in numbers: `1` → `i`, `10` → `io`, `100` →
  `i 00`, `1991` → `i 991`, `CB1486` → `CBl486`, `lanceolate` → `ianceolate`
  (4 times on one page).
- **Joined units:** `6 mm` → `6mm` throughout the tables.
- **Garbled headings:** `Table of` → `rlalle o1`.
- **Shared with us:** the journal's worn "e", printed so it reads as "c"
  ("thc", "dcsign"), is kept as printed in the references. Both Acrobat and
  our engines "correct" it, so it costs both the same (about 30 words).

Acrobat's deskew and resampling do not help us. Fast on Acrobat's pixels loses
more words (0.974 against 0.981) and more numbers (0.952 against 0.973) than
on the original scan. Its wer is also worse (0.127), mostly from reading
order on two-column pages. So **feed wiseau the original scan, not an
Acrobat-OCR'd copy**. An Acrobat copy still works, because ADR-040 ignores
the invisible layer and re-OCRs the page. It is just slightly worse.

## Speed: whole documents

`acrobat_timing.py` makes one `file_to_markdown` call per document, as the API
does, in a warm process (a one-page warm-up first, so model loading is not
counted). The runs went one after another on a quiet 4 vCPU box.
- Acrobat's times come from the user's stopwatch
  (`pdf-test/acrobat-pdf/AdobeAcrobat-OCR-Timing.txt`).
- Acrobat ran on an i7-1185G7 (4 cores / 8 threads, 3.0 GHz base) with
  32 GB of memory.

| Document | Pages | Acrobat 26 | Fast | Accurate |
|---|---|---|---|---|
| Vol 5 No 1 | 31 | **70.1 s** (2.26 s/page) | 93.6 s (3.02) | 263.6 s (8.50) |
| Vol 5 No 3 | 27 | **57.2 s** (2.12) | 79.0 s (2.93) | 210.8 s (7.81) |
| Vol 6 No 1 | 40 | **90.8 s** (2.27) | 122.4 s (3.06) | 331.8 s (8.30) |
| Vol 2 No 2 | 40 | 280.9 s (7.02) | **128.1 s** (3.20) | 296.8 s (7.42) |
| Vol 20 No 4 | 371 | 1043.0 s (2.81) | **31.6 s** (0.09) | **31.5 s** (0.08) |

Memory and CPU:

| Engine | Peak memory | CPU s / wall s |
|---|---|---|
| Fast | 0.54–0.58 GB | ~1.7 |
| Accurate | 2.3–2.4 GB | ~1.5 |

So neither engine uses all 4 CPUs on one document. A second concurrent
request would find spare capacity.

Notes:
- **Vol 6 No 1** was read from Acrobat's copy, because its original is not in
  `pdf-test/`. Our engines re-OCR the pixels (ADR-040), so its times are
  comparable, and its word accuracy would be slightly below an original scan's
  (see above).
- **Vol 2 No 2** took Acrobat three times longer a page than the other scans.
  Its output was not supplied, so why is not known. It may be the existing
  Acrobat 8 layer having to be replaced, or the 11 MB of images.
- Fast run-to-run noise: Vol 5 No 3 took 76.8 s and then 79.0 s (3%).

## What this means

1. **Our text is more complete than Acrobat 26's on these scans.** Acrobat
   loses about twice the words and numbers, and it scrambles tables.
   ADR-040's choice is confirmed against Acrobat's current version, not only
   the 2006-era Acrobat 8 layers of set L: re-OCR a scan's invisible layer
   rather than trust it.
2. **Fast is about Acrobat's speed and keeps Accurate's words.** Accurate's
   extra 5 s a page buys headings and rebuilt tables (ADR-059's write-up),
   which a text layer has no equivalent for.
3. **The one place Acrobat is clearly better is typographic punctuation.**
   Tesseract returns straight quotes and hyphens where ‘ ’ “ ” – were printed.
   This is a possible follow-up, but folded punctuation is already level, so
   it changes how the text looks, not what it says.

## Not done

- Vol 2 No 2's Acrobat output was not supplied. With it, set F and L's 12
  references would give a second, held-out score.
- Vol 6 No 1 and Vol 20 No 4 have no references, so they are timed only.
- The two machines differ, and Acrobat was not run on ours, nor ours on the
  user's laptop.
- Structure (headings, tables as tables) was not compared, because Acrobat's
  "searchable image" output has none to compare.

## Reproduce

```bash
python benchmarks/bakeoff/run_engines.py hidden-layer@acrobat26 --set d --acrobat
python benchmarks/bakeoff/run_engines.py trust-layer@acrobat26 --set d --acrobat
python benchmarks/bakeoff/run_engines.py fast-cheap-300@acrobat26-pixels --set d --acrobat
python benchmarks/bakeoff/score.py --set d
python benchmarks/bakeoff/word_errors.py hidden-layer@acrobat26 --set d
python benchmarks/bakeoff/acrobat_timing.py all     # ~35 min on 4 vCPUs; needs requirements-tables + models
```
