"""Tests for the MCP tool surface (`mcp_server.py`).

The MCP server is a thin HTTP adapter over the backend, so these tests stub the
backend with an ``httpx.MockTransport`` and assert that each tool (a) speaks the
right request to the right endpoint and (b) round-trips the shared
``MarkdownResponse`` contract — or surfaces the backend's ``detail`` cleanly on
error. No live server, browser, or network is involved.

The tool functions are plain async callables (FastMCP's ``@tool()`` registers
them and returns them unchanged), so they are driven directly via ``asyncio.run``
— no ``pytest-asyncio`` needed.
"""

from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest

import mcp_server


def _mock_client(monkeypatch, handler) -> None:
    """Point ``mcp_server`` at a MockTransport-backed client for one test."""

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=mcp_server.API_BASE,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(mcp_server, "_client", factory)


def test_convert_url_round_trips_contract(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"source": "https://example.com/", "markdown": "# Hi\n", "length": 5}
        )

    _mock_client(monkeypatch, handler)
    result = asyncio.run(mcp_server.convert_url("https://example.com"))

    assert captured["path"] == "/convert/url"
    # `auto` defers to the deployment default, so the tool adds no opinion of
    # its own unless the agent names an engine (ADR-025).
    assert captured["body"] == {"url": "https://example.com", "engine": "auto"}
    assert result == {"source": "https://example.com/", "markdown": "# Hi\n", "length": 5}


def test_convert_file_posts_multipart_with_filename(monkeypatch, tmp_path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["content_type"] = request.headers.get("content-type", "")
        captured["raw"] = request.content
        return httpx.Response(200, json={"source": "report.pdf", "markdown": "# R\n", "length": 4})

    _mock_client(monkeypatch, handler)
    result = asyncio.run(mcp_server.convert_file(str(pdf)))

    assert captured["path"] == "/convert/file"
    assert captured["content_type"].startswith("multipart/form-data")
    # The filename must reach the backend — it dispatches parsers by extension.
    assert b"report.pdf" in captured["raw"]
    assert b"application/pdf" in captured["raw"]
    assert result["source"] == "report.pdf"


def test_ping_round_trips(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ping"
        return httpx.Response(200, json={"status": "ok", "service": "x", "version": "0.2.0"})

    _mock_client(monkeypatch, handler)
    result = asyncio.run(mcp_server.ping())
    assert result["status"] == "ok"


def test_backend_error_detail_is_surfaced(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(415, json={"detail": "Unsupported file type '.txt'."})

    _mock_client(monkeypatch, handler)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(mcp_server.convert_file(_write_temp(monkeypatch)))

    message = str(excinfo.value)
    assert "415" in message
    assert "Unsupported file type" in message


def test_non_json_error_body_falls_back_to_text(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Bad Gateway")

    _mock_client(monkeypatch, handler)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(mcp_server.ping())
    assert "502" in str(excinfo.value)
    assert "Bad Gateway" in str(excinfo.value)


def test_tools_are_registered_with_the_server():
    # FastMCP exposes registered tools asynchronously; the three we defined
    # must be discoverable by an MCP client.
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"convert_url", "convert_file", "ping"} <= names


def _write_temp(monkeypatch) -> str:
    """Materialize a throwaway file so `convert_file` has something to read."""
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    handle.write(b"not markdownable")
    handle.close()
    return handle.name


def test_convert_file_forwards_a_requested_engine(monkeypatch, tmp_path):
    """An agent can ask for fidelity (docling) or speed (pymupdf), like the UI."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"source": "d.pdf", "markdown": "# Hi\n", "length": 5})

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    _mock_client(monkeypatch, handler)
    asyncio.run(mcp_server.convert_file(str(pdf), engine="docling"))

    assert b'name="engine"' in captured["body"]
    assert b"docling" in captured["body"]
