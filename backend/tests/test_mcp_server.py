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
    assert captured["body"] == {
        "url": "https://example.com",
        "engine": "auto",
        "split_chapters": False,
    }
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
    # FastMCP exposes registered tools asynchronously; the ones we defined must
    # be discoverable by an MCP client.
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"convert_url", "convert_file", "extract_keywords", "ping"} <= names


def test_standalone_transport_defaults_bind_publicly(monkeypatch):
    """streamable-http must be reachable from outside the process by default."""
    monkeypatch.delenv("WISEAU_MCP_HOST", raising=False)
    monkeypatch.delenv("WISEAU_MCP_PORT", raising=False)
    monkeypatch.delenv("WISEAU_MCP_ALLOWED_HOSTS", raising=False)

    mcp_server._configure_standalone_transport()

    assert mcp_server.mcp.settings.host == "0.0.0.0"
    assert mcp_server.mcp.settings.port == 8080


def test_standalone_transport_honours_host_port_env(monkeypatch):
    monkeypatch.setenv("WISEAU_MCP_HOST", "10.0.0.5")
    monkeypatch.setenv("WISEAU_MCP_PORT", "9000")
    monkeypatch.delenv("WISEAU_MCP_ALLOWED_HOSTS", raising=False)

    mcp_server._configure_standalone_transport()

    assert mcp_server.mcp.settings.host == "10.0.0.5"
    assert mcp_server.mcp.settings.port == 9000


def test_standalone_transport_widens_host_allowlist(monkeypatch):
    """Naming hosts opts the standalone server into the Host check (ADR-029).

    Left unset the check is off, because a public deployment must answer its
    own hostname; an operator who names hosts gets it enforced.
    """
    monkeypatch.delenv("WISEAU_MCP_HOST", raising=False)
    monkeypatch.delenv("WISEAU_MCP_PORT", raising=False)
    monkeypatch.setenv("WISEAU_MCP_ALLOWED_HOSTS", "wiseau-mcp.example.com, other.example.com")

    mcp_server._configure_standalone_transport()

    security = mcp_server.mcp.settings.transport_security
    assert "wiseau-mcp.example.com" in security.allowed_hosts
    assert "other.example.com" in security.allowed_hosts
    assert "https://wiseau-mcp.example.com" in security.allowed_origins
    assert "http://wiseau-mcp.example.com" in security.allowed_origins
    # The original localhost entries must survive — DNS rebinding protection
    # for local use shouldn't be lost by opting into a remote allow-list.
    assert "localhost:*" in security.allowed_hosts


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


# --- Chapter splitting (ADR-030) --------------------------------------------
def test_convert_url_can_request_the_chapter_split(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "source": "https://example.com/",
                "markdown": "# Hi\n",
                "length": 5,
                "chapters": [
                    {"title": "One", "level": 1, "filename": "01-one.md", "markdown": "# One\n", "length": 6}
                ],
                "chapter_detection": "headings",
            },
        )

    _mock_client(monkeypatch, handler)
    result = asyncio.run(mcp_server.convert_url("https://example.com", split_chapters=True))

    assert captured["body"]["split_chapters"] is True
    # The agent gets the chapters verbatim — filename included, so "save each
    # chapter as its own file" is a loop, not a naming decision.
    assert result["chapter_detection"] == "headings"
    assert result["chapters"][0]["filename"] == "01-one.md"


def test_convert_file_sends_the_split_flag_as_form_data(monkeypatch, tmp_path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["raw"] = request.content
        return httpx.Response(200, json={"source": "book.pdf", "markdown": "# B\n", "length": 4})

    _mock_client(monkeypatch, handler)
    asyncio.run(mcp_server.convert_file(str(pdf), split_chapters=True))

    # Multipart carries strings, and the backend parses "true"/"false" — sending
    # Python's "True" would be read as an invalid boolean and 422 the request.
    assert b'name="split_chapters"' in captured["raw"]
    assert b"true" in captured["raw"]
    assert b"True" not in captured["raw"]


def test_the_split_is_off_unless_the_agent_asks(monkeypatch, tmp_path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["raw"] = request.content
        return httpx.Response(200, json={"source": "book.pdf", "markdown": "# B\n", "length": 4})

    _mock_client(monkeypatch, handler)
    asyncio.run(mcp_server.convert_file(str(pdf)))

    assert b"false" in captured["raw"]


def test_the_split_parameter_is_advertised_to_clients():
    tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
    for name in ("convert_url", "convert_file"):
        assert "split_chapters" in tools[name].inputSchema["properties"]


# --- Batch tool (ADR-031) ---------------------------------------------------
def test_convert_batch_posts_every_document_in_one_request(monkeypatch, tmp_path):
    first = tmp_path / "one.pdf"
    first.write_bytes(b"%PDF-1.4 first")
    second = tmp_path / "two.docx"
    second.write_bytes(b"PK second")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["raw"] = request.content
        return httpx.Response(
            200,
            json={
                "count": 2,
                "succeeded": 2,
                "failed": 0,
                "results": [
                    {"status": "ok", "source": "one.pdf", "filename": "one.md", "markdown": "# 1\n", "length": 4},
                    {"status": "ok", "source": "two.docx", "filename": "two.md", "markdown": "# 2\n", "length": 4},
                ],
            },
        )

    _mock_client(monkeypatch, handler)
    result = asyncio.run(mcp_server.convert_batch([str(first), str(second)]))

    # One request, not one per document — that is the point of the tool.
    assert captured["path"] == "/convert/batch"
    assert b"one.pdf" in captured["raw"] and b"two.docx" in captured["raw"]
    assert b"application/pdf" in captured["raw"]
    assert result["succeeded"] == 2
    assert [item["filename"] for item in result["results"]] == ["one.md", "two.md"]


def test_convert_batch_forwards_the_engine(monkeypatch, tmp_path):
    path = tmp_path / "one.pdf"
    path.write_bytes(b"%PDF-1.4 x")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["raw"] = request.content
        return httpx.Response(200, json={"count": 1, "succeeded": 1, "failed": 0, "results": []})

    _mock_client(monkeypatch, handler)
    asyncio.run(mcp_server.convert_batch([str(path)], engine="docling"))
    assert b"docling" in captured["raw"]


def test_convert_batch_reports_an_unreadable_path_before_converting_anything(monkeypatch, tmp_path):
    # Failing up front is deliberate: a batch that silently dropped a path would
    # look like a complete success, and the missing document would go unnoticed.
    real = tmp_path / "real.pdf"
    real.write_bytes(b"%PDF-1.4 x")
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    _mock_client(monkeypatch, handler)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(mcp_server.convert_batch([str(real), str(tmp_path / "missing.pdf")]))

    assert "missing.pdf" in str(excinfo.value)
    assert called is False


def test_convert_batch_surfaces_a_backend_error(monkeypatch, tmp_path):
    path = tmp_path / "one.pdf"
    path.write_bytes(b"%PDF-1.4 x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "A batch is limited to 20 files; 44 were sent."})

    _mock_client(monkeypatch, handler)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(mcp_server.convert_batch([str(path)]))
    assert "limited to 20 files" in str(excinfo.value)


def test_the_batch_tool_offers_no_chapter_splitting(monkeypatch):
    # Mutual exclusivity (ADR-031) has to be visible in the tool's *signature*:
    # an agent chooses a tool from its parameters, so a `split_chapters` it could
    # pass here would be a promise the backend refuses.
    import inspect

    assert "split_chapters" not in inspect.signature(mcp_server.convert_batch).parameters
    assert "split_chapters" in inspect.signature(mcp_server.convert_file).parameters


# --- Keyword tool (ADR-032) -------------------------------------------------
KEYWORD_RESPONSE = {
    "source": "report.pdf",
    "keyword_count": 1,
    "keywords": [
        {
            "term": "adaptation funding",
            "score": 1.0,
            "rank": 1,
            "kind": "phrase",
            "occurrences": 6,
            "agreement": 2,
            "methods": {"frequency": {"rank": 1, "score": 33.6}, "yake": {"rank": 1, "score": 0.03}},
        }
    ],
    "methods_used": ["frequency", "yake"],
    "methods_skipped": {},
    "language": "en",
    "note": None,
    "markdown": None,
}


def test_extract_keywords_posts_the_document_and_round_trips_the_contract(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=KEYWORD_RESPONSE)

    _mock_client(monkeypatch, handler)
    result = asyncio.run(mcp_server.extract_keywords("# Report\n\nAdaptation funding.\n"))

    assert captured["path"] == "/keywords"
    assert captured["body"] == {
        "markdown": "# Report\n\nAdaptation funding.\n",
        "top_k": 20,
        "prepend_table": False,
    }
    assert result == KEYWORD_RESPONSE


def test_the_keyword_tool_sends_only_the_options_the_agent_chose(monkeypatch):
    """An omitted option must defer to the deployment, not pin a value."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=KEYWORD_RESPONSE)

    _mock_client(monkeypatch, handler)
    asyncio.run(
        mcp_server.extract_keywords(
            "# Report\n\nAdaptation funding.\n",
            methods=["frequency", "keybert"],
            top_k=5,
            language="de",
            prepend_table=True,
            source="report.pdf",
        )
    )

    assert captured["body"] == {
        "markdown": "# Report\n\nAdaptation funding.\n",
        "top_k": 5,
        "prepend_table": True,
        "methods": ["frequency", "keybert"],
        "language": "de",
        "source": "report.pdf",
    }


def test_the_keyword_tool_surfaces_a_backend_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "Unknown keyword method 'tf-idf'."})

    _mock_client(monkeypatch, handler)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(mcp_server.extract_keywords("# Report\n\nText.\n", methods=["tf-idf"]))

    assert "tf-idf" in str(excinfo.value)


def test_the_keyword_tool_is_advertised_to_clients():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool = next(tool for tool in tools if tool.name == "extract_keywords")
    properties = tool.inputSchema["properties"]

    assert {"markdown", "methods", "top_k", "language", "prepend_table"} <= set(properties)
    # The agent has to be told which methods are slow, or it will reach for the
    # best one on every document (ADR-032).
    assert "keybert" in (tool.description or "")
