# Candidate OCR model investigation: OvisOCR3 and TeleOCR

**Purpose:** A proposal for Claude Code to assess against Wiseau's current architecture and roadmap. This is an investigation, not a decision to adopt either model.

## Recommendation

Run a small, isolated feasibility test on a free Kaggle NVIDIA T4. **Test OvisOCR3 first, then TeleOCR.** The key question is not simply which model produces the best Markdown. It is whether either can provide reliable page-structure information that adds value to Wiseau's canonical document IR and eventual region-level routing.

Keep this work separate from production. Do not replace Tesseract/PyMuPDF, change the default parser, add model dependencies to the production environment, or delay Tier 2.1–2.3 (IR, neutral serializer and structural gold set).

## Candidates

### 1. OvisOCR3 — first choice

[Hugging Face model card](https://huggingface.co/ATH-MaaS/OvisOCR3)

OvisOCR3 is approximately 0.9B parameters, with BF16 weights, and is built on Qwen3.5-0.8B. It offers several distinct outputs:

- **Layout-only:** region bounding boxes and categories, without transcription.
- **Structured parsing:** region-level JSON with bounding boxes, categories and content.
- **Markdown parsing:** document output with table and formula support.
- **Cross-page table reconstruction:** a separate process for joining table fragments across consecutive pages.

Its layout-only and structured modes make it particularly interesting as a possible *structural sidecar* to Wiseau's existing OCR. Initially, use its output only as candidate structural metadata; do not let its generated text replace or correct the source transcription.

The model card documents a vLLM route using vLLM 0.22.1 and a Triton prefill backend, as well as a Transformers route. The Qwen3.5/linear-attention kernel path is the main T4 compatibility risk. Try Transformers/PyTorch first, avoiding optional fused kernels where possible. Explicitly test FP16 loading; do not assume casting BF16 weights guarantees every operation is T4-compatible. FP32 is a diagnostic fallback, not the preferred mode.

### 2. TeleOCR — second choice

[Hugging Face model card](https://huggingface.co/XingChen-AGI/TeleOCR) · [project repository](https://github.com/caipeng328/TeleOCR)

TeleOCR is approximately 1.4B parameters and is based on Qwen2.5-VL, so it does not share OvisOCR3's Qwen3.5 linear-attention risk. Its published usage includes Transformers and vLLM routes. Its larger parameter count is not, by itself, a reason to rule it out on a 16 GB T4.

Try the documented vLLM route in a clean environment, but first verify the repository's current Torch/CUDA/vLLM/xformers requirements. Do not combine incompatible pinned packages with Kaggle's preinstalled Torch. If vLLM proves troublesome, test Transformers separately.

Community GGUF builds may be a last-resort route for either model. Verify that the quantisation supports the complete multimodal path (including vision components) and assess the quality loss. A GGUF file existing does not prove that image OCR works correctly in llama.cpp.

## Kaggle constraints and setup

Inspect the actual session before installing anything:

```bash
nvidia-smi
python --version
pip show torch transformers vllm xformers triton
```

A T4 has 16 GB VRAM, compute capability 7.5, supports FP16 and lacks native BF16 tensor-core support. Check the current compatibility requirements for the exact vLLM release; see the [vLLM CUDA installation guide](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/). Kaggle's GPU type, count and quotas can vary. If two T4s are available, they remain separate 16 GB devices, not one pooled 32 GB GPU. Do not make multi-GPU execution a requirement.

Use one GPU, one page at a time and batch size 1 for the initial test. Model-weight size is not total inference memory: account for vision processing, activations, context/cache, image resolution and runtime overhead. Record the exact GPU, driver, Python and package versions.

## Proposed experiment

Keep the work in a separate Kaggle notebook or isolated environment. Do not alter Wiseau's `backend/requirements.txt`, Docker image or production parser just to install a candidate.

1. **OvisOCR3:** use Transformers first. Attempt FP16 where supported; run layout-only and structured modes on a few development pages. Record whether loading and inference succeed, raw outputs, peak VRAM, host RAM and seconds per page.
2. **TeleOCR:** test the documented vLLM path in a clean environment with a compatible dependency set. If it fails, record the exact failure and test Transformers separately rather than spending time patching a mixed environment.
3. **Use representative development pages:** ordinary prose, a difficult PBR table (including side-by-side or multi-column content), a two-column prose page, a figure/caption page and a typewriter/patent page. Use the same rasterisation/orientation/resolution as the existing bake-off. Do not tune against held-out sets.
4. **Preserve evidence:** retain input images, prompts, raw outputs, parsed outputs, model revision, settings, package versions and errors. Do not silently clean, repair or truncate model output.
5. **Test cross-page tables separately**, and only after page-level parsing looks credible. Check merged rows, repeated headers and source-page provenance.

## Evaluation and decision criteria

Measure distinct capabilities rather than relying on one Markdown score:

- **Operational:** load/inference success, failure rate, peak VRAM, host RAM, load time and seconds per page.
- **Text (where applicable):** WER, CER and number recall against existing blind references.
- **Structure:** region/category accuracy, bounding-box quality, block precision/recall or F1, reading order, and table structure (TEDS or the closest existing metric).
- **Known Wiseau failure modes:** prose classified as a table; adjacent tables merged; rows/columns lost; captions detached; headers/footers treated as body text; columns interleaved; small/typewriter text omitted; invented or unsupported content.

Claude should inspect the current benchmark harness and set definitions before choosing pages. Follow the existing blind-reference rules. Use development material for initial iteration; do not tune prompts, thresholds or preprocessing against sets E/F or any other held-out material.

Recommend a roadmap item only if a model is installable and reliable on the available Kaggle GPU and supplies structural information that the existing path does not already provide. Recommend integration only after evaluation against the structural gold set demonstrates measurable benefit without unacceptable regressions, hallucinations, runtime or memory cost. Otherwise, document the result and close the experiment without changing production.

## How it could fit the roadmap

Keep the existing Tier 2 sequence intact:

- **2.1 Canonical IR:** initially populated from the current PyMuPDF text-layer and Tesseract layout paths. Preserve original text, provenance and coordinates separately from inferred structure.
- **2.2 Deterministic serializer:** retain the byte-for-byte-neutral migration gate.
- **2.3 Structural gold set and metrics:** establish ground truth before judging structural improvements.

A small Kaggle feasibility spike can run in parallel on development pages. If promising, consider a later optional task to evaluate OvisOCR3/TeleOCR as external layout/structure providers feeding Tier 3.7 (region-level routing). Validate against the structural gold set and held-out checks before any production integration.

A possible integration pattern is: existing OCR supplies the source transcription; an optional model supplies candidate regions/categories; the IR stores these separately with model/version, provenance and confidence; a deterministic gate accepts or rejects the structural signal; and only validated regions inform routing. **A model may label structure, but must not rewrite, correct or fill gaps in the original transcription.**

## Instructions for Claude Code

Read `CLAUDE.md`, `STATUS.md`, `docs/handover-2026-11-09.md` and `docs/roadmap-ocr-and-structure.md` first. Verify current model cards, repository instructions, dependency versions and Kaggle assumptions rather than treating this note as authoritative. Reconcile the proposal with the latest repo state; explain what you accept, defer or reject and why. Add only justified roadmap items. Do not implement either model or change production behaviour as part of this review unless separately asked.
