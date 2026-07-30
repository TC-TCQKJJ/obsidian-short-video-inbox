from __future__ import annotations

import asyncio
import json
import unittest

import mcp_server


EXPECTED_TOOLS = {
    "wechat_capture_status",
    "wechat_capture_list",
    "wechat_capture_start",
    "wechat_capture_get",
    "wechat_capture_read_transcript",
}


class McpServerSchemaTest(unittest.TestCase):
    def test_exposes_only_the_five_bounded_capture_tools(self) -> None:
        tools = asyncio.run(mcp_server.mcp.list_tools())

        self.assertEqual({tool.name for tool in tools}, EXPECTED_TOOLS)

    def test_tool_inputs_do_not_accept_secrets_urls_paths_or_acknowledgement(self) -> None:
        tools = asyncio.run(mcp_server.mcp.list_tools())
        schemas = json.dumps(
            {tool.name: tool.inputSchema for tool in tools},
            ensure_ascii=False,
        ).lower()

        for forbidden in (
            "api_key",
            "media_url",
            "source_url",
            "request_headers",
            "decrypt_key",
            "file_path",
            "acknowledge",
            "delete",
        ):
            self.assertNotIn(forbidden, schemas)


if __name__ == "__main__":
    unittest.main()
