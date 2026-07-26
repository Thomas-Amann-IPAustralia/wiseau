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
    monkeypatch.delenv("WISEAU_DOCLING_API_KEY", raising=False)
    captured: dict = {}
    docling_client.convert_document(
        b"data", "d.pdf", base="http://docling", token="", transport=_transport(captured, body=_ok_body("x"))
    )
    assert "Authorization" not in captured["request"].headers
    assert "X-Api-Key" not in captured["request"].headers


def test_api_key_sent_as_x_api_key_header():
    # docling-serve's own guard (DOCLING_SERVE_API_KEY) is a separate mechanism
    # from the Space-gateway bearer token; both can be in play at once.
    captured: dict = {}
    docling_client.convert_document(
        b"data",
        "d.pdf",
        base="http://docling",
        token="gateway",
        api_key="app-key",
        transport=_transport(captured, body=_ok_body("x")),
    )
    headers = captured["request"].headers
    assert headers["Authorization"] == "Bearer gateway"
    assert headers["X-api-key"] == "app-key"  # urllib capitalizes header names


def test_api_key_read_from_environment(monkeypatch):
    monkeypatch.setenv("WISEAU_DOCLING_API_KEY", "from-env")
    captured: dict = {}
    docling_client.convert_document(
        b"data", "d.pdf", base="http://docling", transport=_transport(captured, body=_ok_body("x"))
    )
    assert captured["request"].headers["X-api-key"] == "from-env"


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


@pytest.mark.parametrize("code", [401, 403, 429])
def test_auth_and_rate_limit_4xx_raise_unavailable(code):
    # A wrong credential or a rate limit says nothing about the document — it is
    # a deployment problem, so it must not be logged as a rejected document.
    def send(request, timeout):
        raise _http_error(code, "nope")

    with pytest.raises(docling_client.DoclingUnavailable) as excinfo:
        docling_client.convert_document(b"data", "d.pdf", base="http://docling", transport=send)
    assert str(code) in str(excinfo.value)


def test_gateway_timeout_from_max_sync_wait_raises_unavailable():
    # docling-serve answers 504 past DOCLING_SERVE_MAX_SYNC_WAIT.
    def send(request, timeout):
        raise _http_error(504, "Conversion is taking too long.")

    with pytest.raises(docling_client.DoclingUnavailable):
        docling_client.convert_document(b"data", "d.pdf", base="http://docling", transport=send)


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


# --- Over a real socket -----------------------------------------------------
# Everything above injects a transport, which cannot catch a mistake in the
# request the *default* transport actually puts on the wire. These two drive the
# real `urllib` path against a loopback stub — still no docling Space, but a
# genuine HTTP round trip (multipart body, headers, endpoint, status handling).


@pytest.fixture()
def stub_docling():
    """A loopback HTTP server standing in for docling-serve."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state: dict = {"status": 200, "body": _ok_body("# Over the wire\n")}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
            length = int(self.headers.get("Content-Length", 0))
            state["received"] = {
                "path": self.path,
                "body": self.rfile.read(length),
                "content_type": self.headers.get("Content-Type", ""),
                "api_key": self.headers.get("X-Api-Key"),
                "authorization": self.headers.get("Authorization"),
            }
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, *args):  # keep pytest output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["base"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def test_real_request_reaches_docling_with_the_expected_shape(stub_docling, monkeypatch):
    monkeypatch.setenv("WISEAU_DOCLING_API_KEY", "sekrit")
    monkeypatch.setenv("WISEAU_DOCLING_TOKEN", "hf_token")

    markdown = docling_client.convert_document(b"%PDF-1.4 body", "gov.pdf", base=stub_docling["base"])

    assert markdown == "# Over the wire\n"  # raw; the caller cleans
    received = stub_docling["received"]
    assert received["path"] == "/v1/convert/file"
    assert received["content_type"].startswith("multipart/form-data; boundary=")
    assert received["api_key"] == "sekrit"  # docling-serve's own guard
    assert received["authorization"] == "Bearer hf_token"  # the Space gateway's
    assert b'filename="gov.pdf"' in received["body"]
    assert b"%PDF-1.4 body" in received["body"]
    assert b"md" in received["body"]  # to_formats


def test_a_real_5xx_is_classified_as_unavailable(stub_docling):
    stub_docling["status"] = 503
    stub_docling["body"] = b'{"detail":"model still loading"}'

    with pytest.raises(docling_client.DoclingUnavailable):
        docling_client.convert_document(b"data", "d.pdf", base=stub_docling["base"])
