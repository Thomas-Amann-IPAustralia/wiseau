"""Tests for the HTTP surface and its validation contract.

These exercise routing, request validation, and error codes without touching a
real browser: the one URL-conversion test monkeypatches the heavy ``url_to_
markdown`` worker so the endpoint's plumbing (semaphore, threading, response
shape) is verified while Chromium is not.
"""

from __future__ import annotations

import io

import pymupdf
import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main.app)


def test_ping_reports_ok(client):
    resp = client.get("/ping")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "markdown-ingestion-engine"
    assert body["version"] == main.app.version


def test_convert_url_rejects_invalid_url(client):
    resp = client.post("/convert/url", json={"url": "not-a-url"})
    assert resp.status_code == 422


def test_convert_url_requires_url_field(client):
    resp = client.post("/convert/url", json={})
    assert resp.status_code == 422


def test_convert_url_happy_path_is_mocked(client, monkeypatch):
    monkeypatch.setattr(main, "url_to_markdown", lambda url: "# Rendered\n")
    resp = client.post("/convert/url", json={"url": "https://example.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "https://example.com/"
    assert body["markdown"] == "# Rendered\n"
    assert body["length"] == len("# Rendered\n")


def test_convert_url_surfaces_worker_failure_as_502(client, monkeypatch):
    def boom(url):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(main, "url_to_markdown", boom)
    resp = client.post("/convert/url", json={"url": "https://example.com"})
    assert resp.status_code == 502


def test_convert_file_rejects_empty_upload(client):
    resp = client.post("/convert/file", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert resp.status_code == 400


def test_convert_file_rejects_unsupported_type_with_415(client):
    resp = client.post("/convert/file", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert resp.status_code == 415


def test_convert_file_rejects_oversized_upload_with_413(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 8)
    resp = client.post("/convert/file", files={"file": ("big.pdf", b"x" * 64, "application/pdf")})
    assert resp.status_code == 413


def test_convert_file_pdf_happy_path(client):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Integration Body")
    data = doc.tobytes()
    doc.close()

    resp = client.post("/convert/file", files={"file": ("doc.pdf", data, "application/pdf")})
    assert resp.status_code == 200
    body = resp.json()
    assert "Integration Body" in body["markdown"]
    assert body["length"] == len(body["markdown"])


def test_convert_file_docx_happy_path(client):
    docx = pytest.importorskip("docx", reason="python-docx (dev dependency) is required to synthesize a DOCX")
    document = docx.Document()
    document.add_heading("Contract Heading", level=1)
    document.add_paragraph("Body over the wire.")
    buf = io.BytesIO()
    document.save(buf)

    ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    resp = client.post("/convert/file", files={"file": ("doc.docx", buf.getvalue(), ctype)})
    assert resp.status_code == 200
    body = resp.json()
    assert "# Contract Heading" in body["markdown"]
    assert "Body over the wire." in body["markdown"]
    assert body["length"] == len(body["markdown"])


def test_openapi_schema_exposes_convert_operations(client):
    schema = client.get("/openapi.json").json()
    assert "/convert/url" in schema["paths"]
    assert "/convert/file" in schema["paths"]
    assert "/ping" in schema["paths"]
