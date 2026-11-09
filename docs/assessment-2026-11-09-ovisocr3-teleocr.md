# Assessment: OvisOCR3 and TeleOCR as structure providers (2026-11-09)

A review of [`Alt-solution-investigation.md`](../Alt-solution-investigation.md)
against the repo as it stands after ADR-047. **No code changed and nothing ran.**
The proposal's own rule holds throughout: a model may *label* structure, but it
never rewrites, corrects or fills in the transcription.

## Verdict

**Accept the spike, with three changes. Defer integration. Reject two parts.**

1. **Score it with what we already have.** The proposal waits on Tier 2.1–2.3.
   That work is done: the IR (ADR-043/044), the byte-neutral serializer (2.2),
   and set S with `structure.py` (2.3). So the spike can report set S metrics
   straight away. It doesn't need new metrics.
2. **Measure on CPU as well as on a T4.** Wiseau runs on a 16 GB **CPU** Space.
   A Kaggle T4 result can show whether the *output* is useful. It can't show
   whether the model can *run where Wiseau runs*. That gap has sunk a candidate
   before: GLM-OCR is in the same size class (0.9B) and took minutes per dense
   page on 2–4 cores (post-mortem 2026-11-01; ADR-034 rejected the GPU-based
   speed figures). The CPU check needs no GPU, so it can run in this repo's
   usual container.
3. **Compare against a CPU layout detector, not against nothing.** The question
   is whether these models give us structure that today's path lacks. The
   cheaper competitor is a layout detector: it doesn't generate tokens, it is
   deterministic, and it is fast on CPU. TeleOCR's own environment pins
   `doclayout-yolo==0.0.4`, and TATR already ships for tables. A generative
   model has to beat that detector on set S by enough to justify a GPU, or a
   CPU cost several times higher.

## What the cards actually say (checked 2026-11-09)

| | OvisOCR3 (`ATH-MaaS/OvisOCR3`) | TeleOCR (`XingChen-AGI/TeleOCR`) |
| --- | --- | --- |
| Size | Card: "0.8B" (the proposal says 0.9B). 1.71 GB BF16 safetensors | Card: "~1.2B". 2.83 GB BF16 safetensors (≈1.4B with vision) |
| Architecture | `Qwen3_5ForConditionalGeneration`: 18 of 24 layers are linear-attention (gated delta net), `mamba_ssm_dtype: float32` | `Qwen2_5_VLForConditionalGeneration`, full attention, plus `modeling_naviocr.py` (remote code) |
| Documented route | **vLLM 0.22.1 only**, `gdn_prefill_backend="triton"`. The card has **no Transformers example**. The class is native to Transformers (≥ 4.57 dev), so a Transformers route is plausible but untested | GitHub: vLLM (`vllm-async-engine`). Transformers is mentioned in `pyproject.toml` but not documented |
| Pins | `vllm==0.22.1` | `torch 2.8.0`, `transformers 4.57.1`, `vllm 0.11.0`, `xformers 0.0.32.post1`, `triton 3.4.0`, Python < 3.13 |
| Layout output | **Yes**: a `document_layout` prompt returns `bbox` (0–1000) + `category`, without text; `document_structured` adds content | **No documented schema.** `LAYOUT_MODE` = Detection/Segmentation is unexplained; the doclayout-yolo pin suggests a separate YOLO stage |
| Licence | Apache-2.0 | Apache-2.0 (HF card); the GitHub repo states none |
| T4 notes | Neither card mentions FP16, compute capability 7.5 or CPU. The BF16 weights and float32 SSM state are the risk the proposal names. | Same |

Corrections to the proposal: OvisOCR3 has one documented route (vLLM), not two.
TeleOCR's layout output is not documented, so it can't be called a structural
sidecar until someone shows it exposes one. **OvisOCR3 first is right.** It is
the only one of the two whose layout output is a documented contract.

**One warning sign to test first.** OvisOCR3's 24 layout categories
(`paragraph_title`, `figure_title`, `aside_text`, `vision_footnote`, `seal`,
`header_image` …) appear to be the PP-DocLayout label set. That suggests (an
inference, not a fact) that its layout supervision comes from that family.
PP-DocLayout is GLM-OCR's layout stage, and it merged V16 p90's side-by-side
tables into one region (roadmap 14, "Where the reviews meet the repo";
PP-StructureV3 did the same to V5N1 p18 in 1.3). If OvisOCR3 merges those two
pages too, it adds nothing for tables over TATR, and the spike should go
straight to headings, captions and page furniture.

## Accepted

- **An isolated experiment.** No change to `requirements*.txt`, the Dockerfile,
  the default engine or the API. It fits the pattern of 1.3, a harness engine
  under `benchmarks/bakeoff/`.
- **The sidecar shape.** The existing OCR supplies the words. The model supplies
  regions and categories. The IR stores them with model, revision and signal.
  A deterministic gate accepts or refuses them (ADR-030, as ADR-046's prose gate
  does for TATR). This is the Tier 3.7 pattern already in production. The blind
  frontier check (`docs/Frontier-transcription-check.md`) says the gap is
  structure, not recognition, which makes this the right question to ask.
- **Keep the evidence.** Keep raw output, prompt, model revision, settings,
  package versions and errors, and commit them to `benchmarks/bakeoff/outputs/`
  as soon as a run ends (post-mortem rule). Don't repair truncated JSON. Count it
  as a failure.
- **One GPU, batch 1, one page at a time.** Record the hardware and run nothing
  else at the same time.
- **Separate metrics**, mapped to the ones we have: block F1 / split / merge /
  order, typ_f, headings (hd_p/hd_r, lvl, htree), TEDS, relations, plus `score.py`'s
  wer/cer/num as a check that no words changed. That check must show *zero* word
  change, since the model contributes no text.

## Changed

- **How to score it.** Set S has no boxes; it is scored on text. So a model's
  regions can't be scored directly. Turn them into IR elements by assigning
  Tesseract's psm 3 words to each region (as `tatr_regions.py` does for cells),
  serialize, and run `structure.py`. That measures exactly what an integration
  would deliver. Box quality is visible only through the block metrics, which is
  the part that matters.
- **Which pages.** Develop on sets A/B pages only. Set S contains held-out pages
  from D (V5N1 p18–20), E (P63a p7) and F (V4N1 p7), so score set S **once**,
  with prompts and the gate frozen beforehand. The proposal's page list maps
  roughly as follows. Two-column prose: V2 p9/p11. Table: V4 p32, V2 p14.
  Side-by-side tables: V16 p90 (born-digital). Typewriter: V2 p17. V5N1 p18 has
  already been used in development for TATR, so it is a known-failure probe, not
  held-out evidence.
- **Add a CPU column.** Seconds per page and peak RSS for OvisOCR3 layout-only on
  4 vCPU (Transformers, FP32), once on a dense page and once on a sparse one.
  Layout-only output is short, but prefill of up to 2880² px (thousands of image
  tokens) is not. That figure decides whether this could ever be more than an
  opt-in GPU engine.
- **Determinism.** Greedy decoding at temperature 0 is close to repeatable on one
  machine but not guaranteed across machines. A generative layout signal can
  therefore never sit in the default path (ADR-013/027). It could be opt-in, like
  docling or GLM-OCR, or an offline tool.

## Rejected

- **Cross-page table HTML.** OvisOCR3's stage 2 *regenerates* the merged table's
  cells. That is the model writing text, which the standing rule forbids. Its
  `source_pages` / no-continuation decision could serve as a `continues_table`
  signal for 3.6, but only after page-level layout proves useful. Deferred, not
  part of this spike.
- **GGUF / ONNX / MLX community builds as a route.** There are many (more than ten
  OvisOCR3 conversions on the Hub), but nobody has shown that their vision path
  is complete. Fall back to them only if the CPU route above fails, and treat
  them as a different model when scoring.

## Deferred

- **Any integration, an ADR, or a roadmap item beyond the spike.** These come
  only after a result on set S beats both today's default and the CPU detector
  baseline, adds no words, and has an acceptable CPU cost (or the user accepts a
  GPU-backed opt-in engine).
- **TeleOCR.** Test it only if OvisOCR3 fails to load or run on the T4, *and*
  someone first shows TeleOCR exposes regions. Its pins (torch 2.8, vLLM 0.11,
  xformers) conflict with Kaggle's preinstalled Torch and need a clean
  environment. Whether vLLM 0.11 supports compute capability 7.5 must be checked
  against its release notes. I haven't verified it.

## Who runs it

Kaggle needs the user's account. This session can't reach it, and it has no
GPU. The CPU column and the harness adapter (regions → IR → `structure.py`) can
be built and run here. No paid APIs are involved.
