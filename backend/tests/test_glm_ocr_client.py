"""Tests for the GLM-OCR service client (`parsers/glm_ocr_client.py`; ADR-034).

Same shape as the docling client's tests: a fake transport stands in for the
real ``urllib`` call, plus one loopback round trip so the bytes the *default*
transport puts on the wire are checked too. No model, no service, no network.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from parsers import glm_ocr_client


def _transport(captured: dict, *, body: bytes):
    def send(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return body

    return send


def _ok(markdown: str, key: str = "markdown") -> bytes:
    return json.dumps({key: markdown, "pages": 1}).encode("utf-8")


def _http_error(code: int, detail: str = "boom") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://glm/v1/convert/file",
        code=code,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(json.dumps({"detail": detail}).encode("utf-8")),
    )


def _raising(exc: Exception):
    def send(request, timeout):
        raise exc

    return send


# --- Success path -----------------------------------------------------------
def test_returns_markdown_from_the_service_answer():
    md = glm_ocr_client.convert_document(
        b"%PDF-1.4 fake", "scan.pdf", base="http://glm", transport=_transport({}, body=_ok("# Scanned\n\nBody."))
    )
    assert md == "# Scanned\n\nBody."


@pytest.mark.parametrize("key", ["markdown_result", "md_results"])
def test_tolerates_the_stock_sdk_server_shape(key):
    # A deployment may point the backend straight at the SDK's own server.
    md = glm_ocr_client.convert_document(
        b"x", "scan.png", base="http://glm", transport=_transport({}, body=_ok("Text", key=key))
    )
    assert md == "Text"


def test_result_is_not_normalized_by_the_client():
    raw = "# Title\n\n\n\nBody   \n"
    md = glm_ocr_client.convert_document(b"x", "a.pdf", base="http://glm", transport=_transport({}, body=_ok(raw)))
    assert md == raw  # the caller runs clean_markdown()


def test_builds_a_multipart_post_with_both_credentials():
    captured: dict = {}
    glm_ocr_client.convert_document(
        b"%PDF-1.4 body",
        "gov.pdf",
        base="http://glm/",
        token="hf_token",
        api_key="sekrit",
        timeout=42,
        transport=_transport(captured, body=_ok("ok")),
    )
    request = captured["request"]
    assert request.full_url == "http://glm/v1/convert/file"
    assert request.get_method() == "POST"
    assert request.headers["Content-type"].startswith("multipart/form-data; boundary=")
    assert request.headers["Authorization"] == "Bearer hf_token"
    assert request.headers["X-api-key"] == "sekrit"
    assert b'name="files"; filename="gov.pdf"' in request.data
    assert b"%PDF-1.4 body" in request.data
    assert captured["timeout"] == 42


def test_no_auth_headers_without_credentials(monkeypatch):
    monkeypatch.delenv("WISEAU_GLM_OCR_TOKEN", raising=False)
    monkeypatch.delenv("WISEAU_GLM_OCR_API_KEY", raising=False)
    captured: dict = {}
    glm_ocr_client.convert_document(b"x", "a.pdf", base="http://glm", transport=_transport(captured, body=_ok("ok")))
    assert "Authorization" not in captured["request"].headers
    assert "X-api-key" not in captured["request"].headers


def test_configuration_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("WISEAU_GLM_OCR_BASE", "http://glm-space")
    monkeypatch.setenv("WISEAU_GLM_OCR_TIMEOUT", "900")
    monkeypatch.setenv("WISEAU_GLM_OCR_PATH", "glmocr/convert")
    captured: dict = {}
    glm_ocr_client.convert_document(b"x", "a.pdf", transport=_transport(captured, body=_ok("ok")))
    assert captured["request"].full_url == "http://glm-space/glmocr/convert"
    assert captured["timeout"] == 900.0


def test_default_timeout_allows_for_cpu_inference(monkeypatch):
    # Minutes per page on CPU (ADR-034): the default must not be docling's 120s.
    monkeypatch.delenv("WISEAU_GLM_OCR_TIMEOUT", raising=False)
    captured: dict = {}
    glm_ocr_client.convert_document(b"x", "a.pdf", base="http://glm", transport=_transport(captured, body=_ok("ok")))
    assert captured["timeout"] == 300.0


def test_is_configured_reflects_env(monkeypatch):
    monkeypatch.delenv("WISEAU_GLM_OCR_BASE", raising=False)
    assert glm_ocr_client.is_configured() is False
    monkeypatch.setenv("WISEAU_GLM_OCR_BASE", "http://glm")
    assert glm_ocr_client.is_configured() is True


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("a.pdf", True), ("A.PDF", True), ("scan.png", True), ("scan.tiff", True), ("memo.docx", False), ("", False)],
)
def test_supports_pdfs_and_images_but_not_docx(filename, expected):
    # GLM-OCR reads pixels; it has no DOCX reader, so DOCX never goes to it.
    assert glm_ocr_client.supports(filename) is expected


# --- Failure modes ----------------------------------------------------------
def test_unconfigured_base_raises_unavailable(monkeypatch):
    monkeypatch.delenv("WISEAU_GLM_OCR_BASE", raising=False)
    with pytest.raises(glm_ocr_client.GlmOcrUnavailable):
        glm_ocr_client.convert_document(b"x", "a.pdf")


@pytest.mark.parametrize("code", [500, 502, 503, 504, 401, 403, 429])
def test_infrastructure_errors_raise_unavailable(code):
    with pytest.raises(glm_ocr_client.GlmOcrUnavailable):
        glm_ocr_client.convert_document(b"x", "a.pdf", base="http://glm", transport=_raising(_http_error(code)))


@pytest.mark.parametrize("code", [400, 413, 415, 422])
def test_other_4xx_raise_bad_document(code):
    with pytest.raises(glm_ocr_client.GlmOcrBadDocument) as excinfo:
        glm_ocr_client.convert_document(
            b"x", "a.pdf", base="http://glm", transport=_raising(_http_error(code, "too many pages"))
        )
    assert "too many pages" in str(excinfo.value)


@pytest.mark.parametrize(
    "exc", [urllib.error.URLError("refused"), TimeoutError("timed out"), OSError("reset")]
)
def test_transport_failures_raise_unavailable(exc):
    with pytest.raises(glm_ocr_client.GlmOcrUnavailable):
        glm_ocr_client.convert_document(b"x", "a.pdf", base="http://glm", transport=_raising(exc))


@pytest.mark.parametrize("body", [b"not json", json.dumps({"markdown": "   "}).encode(), b"{}"])
def test_unusable_answers_raise_unavailable(body):
    with pytest.raises(glm_ocr_client.GlmOcrUnavailable):
        glm_ocr_client.convert_document(b"x", "a.pdf", base="http://glm", transport=_transport({}, body=body))


def test_both_errors_are_glm_ocr_errors():
    assert issubclass(glm_ocr_client.GlmOcrUnavailable, glm_ocr_client.GlmOcrError)
    assert issubclass(glm_ocr_client.GlmOcrBadDocument, glm_ocr_client.GlmOcrError)


# --- Over a real socket -----------------------------------------------------
@pytest.fixture()
def stub_service():
    state: dict = {"status": 200, "body": _ok("# Over the wire\n")}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
            length = int(self.headers.get("Content-Length", 0))
            state["received"] = {
                "path": self.path,
                "body": self.rfile.read(length),
                "content_type": self.headers.get("Content-Type", ""),
                "api_key": self.headers.get("X-Api-Key"),
            }
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["base"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def test_real_request_reaches_the_service(stub_service, monkeypatch):
    monkeypatch.setenv("WISEAU_GLM_OCR_API_KEY", "sekrit")
    markdown = glm_ocr_client.convert_document(b"%PDF-1.4 body", "gov.pdf", base=stub_service["base"])
    assert markdown == "# Over the wire\n"
    received = stub_service["received"]
    assert received["path"] == "/v1/convert/file"
    assert received["content_type"].startswith("multipart/form-data; boundary=")
    assert received["api_key"] == "sekrit"
    assert b"%PDF-1.4 body" in received["body"]


def test_a_real_413_is_a_bad_document(stub_service):
    stub_service["status"] = 413
    stub_service["body"] = b'{"detail":"document has 40 pages; this service accepts at most 20"}'
    with pytest.raises(glm_ocr_client.GlmOcrBadDocument):
        glm_ocr_client.convert_document(b"data", "d.pdf", base=stub_service["base"])
