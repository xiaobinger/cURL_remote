"""Compatibility helpers for MCP Python SDK 1.x and 2.x.

SDK 2 renamed ``FastMCP`` to ``MCPServer`` and moved it to
``mcp.server.mcpserver``. Both lines expose the same high-level tool API, so
only the import path and the optional ``version`` constructor argument need
adapting.
"""

import inspect

try:
    from mcp.server.mcpserver import MCPServer as _MCPServer
except ModuleNotFoundError as exc:
    if not (exc.name or "").startswith("mcp.server.mcpserver"):
        raise
    from mcp.server.fastmcp import FastMCP as _MCPServer

MCP_SDK_MAJOR = 2 if _MCPServer.__module__.startswith("mcp.server.mcpserver") else 1


def create_server(name, *, instructions=None, version=None):
    """Build an SDK server instance on either the 1.x or 2.x API line."""
    kwargs = {}
    if instructions is not None:
        kwargs["instructions"] = instructions
    supports_version = "version" in inspect.signature(_MCPServer).parameters
    if version is not None and supports_version:
        kwargs["version"] = version

    server = _MCPServer(name, **kwargs)

    if version is not None and not supports_version:
        # 1.x FastMCP has no ``version`` argument, so serverInfo would fall
        # back to the SDK version. The low-level server it wraps accepts one.
        low_level = getattr(server, "_mcp_server", None)
        if low_level is not None and hasattr(low_level, "version"):
            low_level.version = version
    return server