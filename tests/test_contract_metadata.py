import pytest

from aiomcp.contracts.mcp_content import McpTextContent
from aiomcp.contracts.mcp_message import (
    McpCallToolParams,
    McpCallToolRequest,
    McpCallToolResult,
    McpError,
    McpListToolsResult,
    McpResponse,
)
from aiomcp.contracts.mcp_tool import McpTool, McpToolExecution, McpToolIcon
from aiomcp.mcp_server import McpServer


def test_content_blocks_parse_and_dump_annotations_and_meta():
    content = McpTextContent.model_validate(
        {
            "type": "text",
            "text": "hello",
            "annotations": {
                "audience": ["user", "assistant"],
                "priority": 0.7,
                "lastModified": "2026-05-11T12:00:00Z",
            },
            "_meta": {"cache_key": "abc"},
        }
    )

    assert content.annotations is not None
    assert content.annotations.audience == ["user", "assistant"]
    assert content.meta == {"cache_key": "abc"}

    dumped = content.model_dump(exclude_none=True, by_alias=True)
    assert dumped["_meta"] == {"cache_key": "abc"}
    assert "meta" not in dumped


def test_tool_contract_parses_meta_icon_theme_and_execution():
    tool = McpTool.model_validate(
        {
            "name": "example",
            "inputSchema": {"type": "object"},
            "icons": [
                {
                    "src": "https://example.com/icon.png",
                    "mimeType": "image/png",
                    "sizes": ["48x48"],
                    "theme": "dark",
                }
            ],
            "execution": {"taskSupport": "optional"},
            "_meta": {"version": "1"},
        }
    )

    assert tool.icons == [
        McpToolIcon(
            src="https://example.com/icon.png",
            mimeType="image/png",
            sizes=["48x48"],
            theme="dark",
        )
    ]
    assert tool.execution == McpToolExecution(taskSupport="optional")
    assert tool.meta == {"version": "1"}

    dumped = tool.model_dump(exclude_none=True, by_alias=True)
    assert dumped["_meta"] == {"version": "1"}
    assert dumped["icons"][0]["theme"] == "dark"
    assert dumped["execution"] == {"taskSupport": "optional"}


def test_tools_and_tool_call_results_parse_and_dump_meta():
    tools_result = McpListToolsResult.model_validate(
        {"tools": [{"name": "example"}], "_meta": {"page": 1}}
    )
    call_params = McpCallToolParams.model_validate(
        {"name": "example", "arguments": {}, "_meta": {"trace_id": "abc"}}
    )
    call_result = McpCallToolResult.model_validate(
        {
            "content": [{"type": "text", "text": "done"}],
            "structuredContent": {"ok": True},
            "_meta": {"elapsed_ms": 12},
        }
    )

    assert tools_result.model_dump(exclude_none=True, by_alias=True)["_meta"] == {
        "page": 1
    }
    assert call_params.model_dump(exclude_none=True, by_alias=True)["_meta"] == {
        "trace_id": "abc"
    }
    assert call_result.model_dump(exclude_none=True, by_alias=True)["_meta"] == {
        "elapsed_ms": 12
    }


def test_metadata_models_do_not_preserve_arbitrary_extra_fields():
    content = McpTextContent.model_validate(
        {"type": "text", "text": "hello", "unknownField": True}
    )

    dumped = content.model_dump(exclude_none=True, by_alias=True)
    assert "unknownField" not in dumped


@pytest.mark.asyncio
async def test_call_tool_result_meta_round_trips_through_server_response():
    async def advanced_tool():
        return McpCallToolResult(
            content=[McpTextContent(text="done", _meta={"content_meta": True})],
            structuredContent={"ok": True},
            _meta={"hidden": "client-only"},
        )

    server = McpServer()
    await server.mcp_tools_register("advanced", advanced_tool, {"type": "object"})

    response = await server.process(
        McpCallToolRequest(
            id="advanced",
            params=McpCallToolParams(name="advanced", arguments={}),
        )
    )

    assert isinstance(response, McpResponse)
    assert response.result["_meta"] == {"hidden": "client-only"}
    assert response.result["content"][0]["_meta"] == {"content_meta": True}
    assert "meta" not in response.result
    assert "meta" not in response.result["content"][0]


@pytest.mark.asyncio
async def test_task_augmented_tool_call_is_rejected_until_supported():
    async def advanced_tool():
        return "done"

    server = McpServer()
    await server.mcp_tools_register("advanced", advanced_tool, {"type": "object"})

    response = await server.process(
        McpCallToolRequest(
            id="advanced-task",
            params=McpCallToolParams(
                name="advanced",
                arguments={},
                task={"ttl": 1000},
            ),
        )
    )

    assert isinstance(response, McpError)
    assert response.error.code == -32602
    assert (
        response.error.message == "McpServer does not support task-augmented tools/call"
    )
