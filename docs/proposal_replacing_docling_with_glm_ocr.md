# Proposal: Replacing Docling with GLM-OCR in `wiseau`

**Date:** 2026-10-31  
**Status:** Proposed  
**Author:** Engineering / Collaborative Contributor  
**Target:** `wiseau` Architecture (Phase 6 / Post-Phase 6 Refinement)

---

## Executive Summary

This proposal outlines migrating `wiseau`’s stochastic, high-fidelity document extraction layer from **Docling** (`docling-serve`) to **GLM-OCR**. 

By adopting GLM-OCR, we maintain the project's core design tenets established in **ADR-013 through ADR-027**:
1. **Fast and deterministic by default** via `PyMuPDF` / `Mammoth`.
2. **High-fidelity, stochastic extraction available on demand** via opt-in configuration (`engine="glm-ocr"` or `WISEAU_PDF_ENGINE=glm-ocr`).
3. **Resilient automatic fallback** to `PyMuPDF` if the ML extraction service is cold-starting or unavailable (**ADR-014**).
4. **Strict local execution & privacy**—100% self-hosted with zero external API dependencies.

---

## 1. Context & Problem Statement

Currently, high-fidelity PDF extraction is delegated to an internal `docling-serve` microservice. While Docling provides strong layout extraction, running it in resource-constrained environments (e.g., 16 GB CPU-only spaces or local CPU servers) introduces notable trade-offs:
* **High Memory Footprint & Cold Starts:** Heavy vision pipelines consume substantial RAM, creating cold-start latency or out-of-memory (OOM) risks during concurrent loads.
* **Complex Multi-Page Overhead:** Large autoregressive vision-language models scale memory quadratically as PDF page counts increase.

---

## 2. Why GLM-OCR?

GLM-OCR (0.9B total parameters: ~0.4B vision encoder + 0.5B language decoder) is designed specifically for decoupled layout parsing and low-resource execution.

| Metric / Dimension | Current (`docling-serve`) | Proposed (`GLM-OCR`) |
| :--- | :--- | :--- |
| **OmniDocBench Score** | ~80% – 85% | **94.62%** (#1 Overall) |
| **Model Size / RAM (Quantized)** | Multi-gigabyte footprint | **~800 MB RAM** (GGUF Q4_K_M) |
| **Execution Environment** | Resource-heavy CPU/GPU | **Extremely lightweight on local CPU** |
| **Throughput (CPU)** | Moderate / Variable | **~1.86 PDF pages / second** |
| **Data Privacy** | Local microservice | **100% Local / Air-gapped self-hosted** |

### Key Technological Drivers
1. **Decoupled Layout Architecture:** Uses a specialized lightweight layout analyzer (`PP-DocLayoutV3`) to detect text/table bounding boxes first, running parallel region recognition through Multi-Token Prediction (MTP).
2. **Superior Structural Output:** Natively generates clean Markdown tables, LaTeX mathematical blocks, code snippets, and preserved heading hierarchies.
3. **Low Memory Footprint:** Fits comfortably under 1 GB RAM when quantized, allowing it to co-exist cleanly with the main `wiseau` FastAPI container or run as a minimal sibling microservice.

---

## 3. Preserving "Fast & Deterministic by Default"

In accordance with **ADR-027**, fidelity is an opt-in choice, not a tax on every conversion:

* **Default Path:** `WISEAU_PDF_ENGINE` defaults to `pymupdf`. Standard extractions remain deterministic, instant (< 0.1s / page), and lightweight.
* **Opt-In High-Fidelity:** Users select GLM-OCR per-request via `engine="glm-ocr"` (ADR-025) or system-wide via `WISEAU_PDF_ENGINE=glm-ocr`.
* **Resilient Fallback (ADR-014):** If the GLM-OCR microservice times out or fails, `file_parser.py` gracefully degrades to the deterministic `PyMuPDF` parser rather than throwing a 500 error.

---

## 4. Proposed Repository Architecture Changes

The integration retains `wiseau`'s decoupled HTTP microservice pattern:

```
wiseau/
├── backend/
│   ├── parsers/
│   │   ├── file_parser.py          # Updated engine selector: pymupdf (default) vs glm-ocr
│   │   ├── glm_ocr_client.py       # NEW: Thin HTTP client replacing docling_client.py
│   │   └── cleaner.py              # Normalizer enforcing clean output & base64 stripping (ADR-024)
└── glm-ocr/                        # NEW: Local serving microservice (replaces docling/)
    ├── Dockerfile                  # Local Ollama / llama-server container definition
    └── README.md                   # Deployment & verification notes
```

### Proposed Environment Variables
* `WISEAU_GLM_OCR_BASE`: Base URL for internal GLM-OCR microservice (e.g., `http://localhost:11434` or `http://glm-ocr:7860`).
* `WISEAU_PDF_ENGINE`: Engine selection (`pymupdf` [default], `glm-ocr`, `mammoth`).

---

## 5. Local Deployment Architecture

The proposed stack runs entirely within your local server or local Docker network:

```
+-----------------------------------------------------------------------+
| Local Infrastructure / Server Network                                 |
|                                                                       |
|   +--------------------------+       HTTP        +----------------+   |
|   |  wiseau Backend          |  ------------->   | GLM-OCR Engine |   |
|   |  (FastAPI on Port 7860)  |  (Internal Net)   | (Local Container)| |
|   +--------------------------+                   +----------------+   |
|                 |                                         |           |
|                 v                                         v           |
|        PyMuPDF / Mammoth                          0.9B Open Weights   |
|      (Deterministic/Fast)                         (Local GGUF File)   |
+-----------------------------------------------------------------------+
```

---

## 6. Next Steps & ADR Documentation

1. **Draft ADR-034:** Document decision to replace `docling-serve` with GLM-OCR as the official stochastic high-fidelity parser.
2. **Implement `glm-ocr/` Container:** Package 4-bit GGUF model serving via `llama.cpp` / `ollama`.
3. **Implement `glm_ocr_client.py`:** Add thin async client in `backend/parsers/` with health-check and fallback logic.
4. **Update Contracts:** Update `docs/tech-spec.md` and OpenAPI schema to reflect supported engine types.