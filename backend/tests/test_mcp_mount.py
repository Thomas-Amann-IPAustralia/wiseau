"""Tests for the MCP endpoint the backend serves itself (ADR-029).

`main.py` grafts the streamable-HTTP transport onto the FastAPI app so one
container answers both the REST API and an MCP connector URL. These tests cover
the wiring that makes that work — and, in one end-to-end case, drive the real
JSON-RPC handshake through `TestClient` so a regression in it cannot pass
silently.

Nothing here needs a browser, a network, or a second process.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import main
import mcp_server


@pytest.fixture(scope="module")
def live_client():
    """A client with the app's lifespan running, so the MCP endpoint is served.

    Module-scoped deliberately: the transport's session manager refuses to be
    started twice, which is exactly right for a process that boots once and
    exactly wrong for a per-test client.
    """
    with TestClient(main.app) as client:
        yield client


def _initialize_request(request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    }


def _rpc_result(response) -> dict:
    """Read a JSON-RPC envelope from either a JSON or an SSE response body."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise AssertionError(f"no SSE data frame in {response.text!r}")
    return response.json()


# --- Where the endpoint lives -----------------------------------------------
def test_mcp_path_defaults_to_mcp(monkeypatch):
    monkeypatch.delenv("WISEAU_MCP_PATH", raising=False)
    assert mcp_server.mcp_path() == "/mcp"


@pytest.mark.parametrize("configured", ["/secret-abc", "secret-abc", "/secret-abc/"])
def test_mcp_path_is_normalised(monkeypatch, configured):
    """However the operator writes it, the endpoint is one rooted path.

    The path is the only access control every MCP client can express (a
    connector URL), so a stray slash must not produce a *different* endpoint
    from the one the operator pasted into their client.
    """
    monkeypatch.setenv("WISEAU_MCP_PATH", configured)
    assert mcp_server.mcp_path() == "/secret-abc"


def test_mcp_path_falls_back_when_blank(monkeypatch):
    monkeypatch.setenv("WISEAU_MCP_PATH", "/")
    assert mcp_server.mcp_path() == "/mcp"


# --- Host-header policy -----------------------------------------------------
def test_unset_allowed_hosts_disables_the_host_check(monkeypatch):
    """A public deployment must answer its own hostname out of the box.

    FastMCP's default allow-list is loopback-only, which would 421 every real
    client; the deployment is public by design and rate-limited (ADR-029).
    """
    monkeypatch.delenv("WISEAU_MCP_ALLOWED_HOSTS", raising=False)
    security = mcp_server.mcp.settings.transport_security
    monkeypatch.setattr(security, "enable_dns_rebinding_protection", True)

    mcp_server.configure_transport_security()

    assert security.enable_dns_rebinding_protection is False


def test_star_allowed_hosts_disables_the_host_check(monkeypatch):
    monkeypatch.setenv("WISEAU_MCP_ALLOWED_HOSTS", "*")
    security = mcp_server.mcp.settings.transport_security
    monkeypatch.setattr(security, "enable_dns_rebinding_protection", True)

    mcp_server.configure_transport_security()

    assert security.enable_dns_rebinding_protection is False


def test_named_allowed_hosts_are_enforced_alongside_loopback(monkeypatch):
    monkeypatch.setenv("WISEAU_MCP_ALLOWED_HOSTS", "wiseau.example.com, other.example.com")
    security = mcp_server.mcp.settings.transport_security
    monkeypatch.setattr(security, "enable_dns_rebinding_protection", False)
    monkeypatch.setattr(security, "allowed_hosts", ["localhost:*"])
    monkeypatch.setattr(security, "allowed_origins", ["http://localhost:*"])

    mcp_server.configure_transport_security()

    assert security.enable_dns_rebinding_protection is True
    assert "wiseau.example.com" in security.allowed_hosts
    assert "other.example.com" in security.allowed_hosts
    # Naming hosts must not lock the operator out of their own loopback probe.
    assert "localhost:*" in security.allowed_hosts
    assert "https://wiseau.example.com" in security.allowed_origins


# --- Loopback base URL ------------------------------------------------------
def test_default_api_base_follows_the_served_port(monkeypatch):
    """The tools call the backend back over loopback, on whatever port it bound.

    Cloud Run and Render inject `$PORT`; hard-coding 7860 would send every tool
    call to a closed socket there.
    """
    monkeypatch.setenv("PORT", "8080")
    assert mcp_server._default_api_base() == "http://127.0.0.1:8080"


def test_default_api_base_without_port_is_the_local_default(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    assert mcp_server._default_api_base() == "http://127.0.0.1:7860"


# --- Mount switch -----------------------------------------------------------
@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no"])
def test_mount_can_be_switched_off(monkeypatch, value):
    monkeypatch.setenv("WISEAU_MCP_MOUNT", value)
    assert main._mcp_mount_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", ""])
def test_mount_is_on_by_default(monkeypatch, value):
    monkeypatch.setenv("WISEAU_MCP_MOUNT", value)
    assert main._mcp_mount_enabled() is True


def test_missing_sdk_degrades_to_no_endpoint(monkeypatch, caplog):
    """Without the optional MCP SDK the app still starts; it just serves no /mcp."""
    import builtins

    real_import = builtins.__import__

    def refuse_mcp_server(name, *args, **kwargs):
        if name == "mcp_server":
            raise ImportError("No module named 'mcp'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_mcp_server)
    monkeypatch.setenv("WISEAU_MCP_MOUNT", "1")

    with caplog.at_level("WARNING"):
        assert main._load_mcp_routes() == []
    assert "MCP endpoint not served" in caplog.text


# --- The rate-limiter shim --------------------------------------------------
def test_asgi_endpoint_is_named_for_the_limiter():
    """`SlowAPIMiddleware` reads `endpoint.__name__`; an ASGI object has none.

    Without this the middleware raises before checking any limit and every MCP
    request is a 500 — which is how this was found.
    """

    class _AsgiObject:
        async def __call__(self, scope, receive, send):  # pragma: no cover
            pass

    class _Route:
        endpoint = _AsgiObject()

    route = _Route()
    main._name_endpoint_for_limiter(route)

    assert route.endpoint.__name__ == "mcp_streamable_http"


def test_naming_leaves_a_real_function_endpoint_alone():
    class _Route:
        endpoint = staticmethod(lambda: None)

    route = _Route()
    original = route.endpoint.__name__
    main._name_endpoint_for_limiter(route)

    assert route.endpoint.__name__ == original


# --- The served surface -----------------------------------------------------
def test_ping_advertises_the_mcp_endpoint(live_client):
    """A client should discover the path, not guess it — `WISEAU_MCP_PATH` moves it."""
    body = live_client.get("/ping").json()
    assert body["mcp_endpoint"] == main.MCP_ENDPOINT


def test_mcp_endpoint_is_not_in_the_openapi_schema(live_client):
    """The schema describes the REST contract; a JSON-RPC endpoint in it misleads
    a function-calling client into POSTing conversions at the wrong door."""
    paths = live_client.get("/openapi.json").json()["paths"]
    assert "/mcp" not in paths
    assert {"/ping", "/convert/url", "/convert/file"} <= set(paths)


@pytest.mark.skipif(main.MCP_ENDPOINT is None, reason="MCP SDK not installed")
def test_initialize_handshake_succeeds_at_the_exact_path(live_client):
    """The exact URL a connector is given must answer — not redirect, not 500.

    A mounted sub-app would only match `/mcp/…`, so `/mcp` would answer 307;
    grafting the route means the pasted URL is the one that works.
    """
    response = live_client.post(
        main.MCP_ENDPOINT,
        json=_initialize_request(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            # A public hostname: the deployment is reached by name, never as
            # localhost.
            "Host": "wiseau.example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    result = _rpc_result(response)["result"]
    assert result["serverInfo"]["name"] == "wiseau"
    assert "tools" in result["capabilities"]


@pytest.mark.skipif(main.MCP_ENDPOINT is None, reason="MCP SDK not installed")
def test_tools_list_matches_the_documented_surface(live_client):
    """The hosted transport exposes the same tools as stdio — one contract."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": "wiseau.example.com",
    }
    live_client.post(main.MCP_ENDPOINT, json=_initialize_request(), headers=headers)
    response = live_client.post(
        main.MCP_ENDPOINT,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=headers,
    )

    tools = {tool["name"] for tool in _rpc_result(response)["result"]["tools"]}
    assert tools == {
        "convert_url",
        "convert_file",
        "convert_batch",
        "extract_keywords",
        "extract_keywords_batch",
        "ping",
    }
