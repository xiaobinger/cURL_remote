import asyncio
import inspect
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from curlgen_mcp import server  # noqa: E402

EXPECTED_TOOLS = {
    "list_environments",
    "http_request",
    "generate_code",
    "ssh_list_targets",
    "ssh_exec",
    "ssh_upload",
    "ssh_download",
    "remote_http_request",
    "bastion_login",
    "bastion_list_hosts",
    "bastion_connect",
    "bastion_exec",
    "bastion_status",
    "bastion_logout",
    "bastion_upload",
    "bastion_download",
}


def _list_tools(mcp_server):
    tools = mcp_server.list_tools()
    if inspect.isawaitable(tools):
        tools = asyncio.run(tools)
    return tools


def _initialization_options(mcp_server):
    low_level = getattr(mcp_server, "_mcp_server", None) or getattr(
        mcp_server, "_lowlevel_server", None
    )
    if low_level is None or not hasattr(low_level, "create_initialization_options"):
        return None
    return low_level.create_initialization_options()


class MCPCompatibilityTests(unittest.TestCase):
    def test_server_constructs_with_a_stable_name(self):
        self.assertEqual(server.mcp.name, "curlgen-mcp")

    def test_server_reports_its_own_version_not_the_sdk_version(self):
        from curlgen_mcp import __version__

        options = _initialization_options(server.mcp)
        self.assertIsNotNone(options)
        self.assertEqual(options.server_name, "curlgen-mcp")
        self.assertEqual(options.server_version, __version__)

    def test_public_tool_listing_is_available_on_both_sdk_lines(self):
        names = {tool.name for tool in _list_tools(server.mcp)}
        self.assertEqual(names, EXPECTED_TOOLS)


if __name__ == "__main__":
    unittest.main()