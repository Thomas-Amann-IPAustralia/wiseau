"""Document -> Markdown extraction for PDF and DOCX uploads.

PDFs are converted with PyMuPDF4LLM (LLM-tuned Markdown output); DOCX files are
converted to HTML with Mammoth and then to Markdown with Markdownify. Both paths
finish in the shared `clean_markdown` normalizer for uniform output.
"""

from __future__ import annotations

import io
import os

import mammoth
import pymupdf  # provided by pymupdf4llm; formerly imported as `fitz`
import pymupdf4llm
from markdownify import markdownify as html_to_md

from .cleaner import clean_markdown

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


def pdf_to_markdown(data: bytes) -> str:
    """Convert PDF bytes to Markdown via PyMuPDF4LLM."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return pymupdf4llm.to_markdown(doc)
    finally:
        doc.close()


def docx_to_markdown(data: bytes) -> str:
    """Convert DOCX bytes to Markdown via Mammoth + Markdownify."""
    result = mammoth.convert_to_html(io.BytesIO(data))
    return html_to_md(result.value, heading_style="ATX")


def file_to_markdown(data: bytes, filename: str) -> str:
    """Dispatch an uploaded document to the right parser by extension."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        raw = pdf_to_markdown(data)
    elif ext == ".docx":
        raw = docx_to_markdown(data)
    else:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported file type '{ext or 'unknown'}'. Supported: {supported}.")

    return clean_markdown(raw)
