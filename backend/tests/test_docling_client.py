"""Tests for the docling-serve HTTP client (`parsers/docling_client.py`).

The client is a thin HTTP adapter, so these tests inject a fake transport in
place of the real ``urllib`` call — no live docling Space, browser, or network.
They cover the success path (Markdown extracted from the docling-serve JSON), the
request the client builds (endpoint, multipart body, auth header), and every
failure mode's typed error: timeout / connection error / 5xx / empty / non-JSON
all raise ``DoclingUnavailable`` (infrastructure — caller falls back), while a
4xx raises ``DoclingBadDocument`` (rejected input).
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from parsers import docling_client


def _transport(captured: dict, *, body: bytes, status: int = 200):
    """A fake transport that records the request and returns canned bytes."""

    def send(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return body

    return send


def _ok_body(markdown: str) -> bytes:
    return json.dumps({"document": {"md_content": markdown}, "status": "success"}).encode("utf-8")


def _http_error(code: int, detail: str = "boom") -> urllib.error.HTTPError:
    payload = json.dumps({"detail": detail}).encode("utf-8")
    return urllib.error.HTTPError(
        url="http://docling/v1/convert/file",
        code=code,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(payload),
    )


# --- Success path -----------------------------------------------------------
def test_returns_markdown_from_document_md_content():
    captured: dict = {}
    md = docling_client.convert_document(
        b"%PDF-1.4 fake",
        "report.pdf",
        base="http://docling",
        transport=_transport(captured, body=_ok_body("# Faithful\n\nBody.")),
    )
    assert md == "# Faithful\n\nBody."


def test_tolerates_flat_markdown_shape():
    captured: dict = {}
    body = json.dumps({"md_content": "flat markdown"}).encode("utf-8")
    md = docling_client.convert_document(
        b"data", "d.pdf", base="http://docling", transport=_transport(captured, body=body)
    )
    assert md == "flat markdown"


def test_result_is_not_normalized_by_the_client():
    # The client returns docling's Markdown verbatim; the caller runs clean_markdown.
    captured: dict = {}
    raw = "  # Heading\r\n\r\n\r\nMessy spacing  "
    md = docling_client.convert_document(
        b"data", "d.pdf", base="http://docling", transport=_transport(captured, body=_ok_body(raw))
    )
    assert md == raw


# --- Request construction ---------------------------------------------------
def test_builds_multipart_post_to_convert_endpoint():
    captured: dict = {}
    docling_client.convert_document(
        b"the-bytes",
        "my file.pdf",
        base="http://docling.example/",
        transport=_transport(captured, body=_ok_body("x")),
    )
    request = captured["request"]
    assert request.method == "POST"
    # Base's trailing slash is normalized; default path used.
    assert request.full_url == "http://docling.example/v1/convert/file"
    content_type = request.headers["Content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")

    body = request.data
    assert b'name="files"; filename="my file.pdf"' in body
    assert b'name="to_formats"' in body
    assert b"md" in body
    assert b"the-bytes" in body


def test_bearer_token_sent_when_configured():
    captured: dict = {}
    docling_client.convert_document(
        b"data",
        "d.pdf",
        base="http://docling",
        token="s3cret",
        transport=_transport(captured, body=_ok_body("x")),
    )
    assert captured["request"].headers["Authorization"] == "Bearer s3cret"


def test_no_auth_header_without_token(monkeypatch):
    monkeypatch.delenv("WISEAU_DOCLING_TOKEN", raising=False)
    captured: dict = {}
    docling_client.convert_document(
        b"data", "d.pdf", base="http://docling", token="", transport=_transport(captured, body=_ok_body("x"))
    )
    assert "Authorization" not in captured["request"].headers


def test_explicit_timeout_is_passed_to_transport():
    captured: dict = {}
    docling_client.convert_document(
        b"data", "d.pdf", base="http://docling", timeout=7.5, transport=_transport(captured, body=_ok_body("x"))
    )
    assert captured["timeout"] == 7.5


# --- Not configured ---------------------------------------------------------
def test_unconfigured_base_raises_unavailable(monkeypatch):
    monkeypatch.delenv("WISEAU_DOCLING_BASE", raising=False)
    with pytest.raises(docling_client.DoclingUnavailable):
        docling_client.convert_document(b"data", "d.pdf")


def test_is_configured_reflects_env(monkeypatch):
    monkeypatch.delenv("WISEAU_DOCLING_BASE", raising=False)
    assert docling_client.is_configured() is False
    monkeypatch.setenv("WISEAU_DOCLING_BASE", "http://docling")
    assert docling_client.is_configured() is True


# --- Failure modes → typed errors -------------------------------------------
def test_5xx_raises_unavailable():
    def send(request, timeout):
        raise _http_error(503, "cold start")

    with pytest.raises(docling_client.DoclingUnavailable) as excinfo:
        docling_client.convert_document(b"data", "d.pdf", base="http://docling", transport=send)
    assert "503" in str(excinfo.value)


def test_4xx_raises_bad_document():
    def send(request, timeout):
        raise _http_error(422, "unsupported format")

    with pytest.raises(docling_client.DoclingBadDocument) as excinfo:
        docling_client.convert_document(b"data", "d.pdf", base="http://docling", transport=send)
    assert "422" in str(excinfo.value)


def test_connection_error_raises_unavailable():
    def send(request, timeout):
        raise urllib.error.URLError("connection refused")

    with pytest.raises(docling_client.DoclingUnavailable):
        docling_client.convert_document(b"data", "d.pdf", base="http://docling", transport=send)


def test_timeout_raises_unavailable():
    def send(request, timeout):
        raise TimeoutError("timed out")

    with pytest.raises(docling_client.DoclingUnavailable):
        docling_client.convert_document(b"data", "d.pdf", base="http://docling", transport=send)


def test_empty_conversion_raises_unavailable():
    captured: dict = {}
    with pytest.raises(docling_client.DoclingUnavailable) as excinfo:
        docling_client.convert_document(
            b"data", "d.pdf", base="http://docling", transport=_transport(captured, body=_ok_body("   "))
        )
    assert "empty" in str(excinfo.value)


def test_non_json_response_raises_unavailable():
    captured: dict = {}
    with pytest.raises(docling_client.DoclingUnavailable):
        docling_client.convert_document(
            b"data", "d.pdf", base="http://docling", transport=_transport(captured, body=b"<html>nope</html>")
        )


def test_both_errors_are_docling_errors():
    # file_parser catches the common base to decide on fallback.
    assert issubclass(docling_client.DoclingUnavailable, docling_client.DoclingError)
    assert issubclass(docling_client.DoclingBadDocument, docling_client.DoclingError)
