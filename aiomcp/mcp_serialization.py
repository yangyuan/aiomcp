from pydantic import TypeAdapter
from aiomcp.contracts.mcp_message import (
    McpMessage,
    McpClientMessageAnnotated,
    McpResponseOrError,
)

"""
Client message serialization is relatively stable thanks to the deterministic method field.
Server message serialization currently relies on pydantic and a strict server implementation of the response structure.
"""


class McpSerialization:
    @staticmethod
    def process_client_message(message: McpMessage) -> McpMessage:
        json_str = message.model_dump_json()
        adapter = TypeAdapter(McpClientMessageAnnotated)
        return adapter.validate_json(json_str)

    @staticmethod
    def process_server_message(message: McpMessage) -> McpMessage:
        # Server may pass a response with "error": null, which is not strictly conforming to the spec, but can be tolerated.
        # TODO: enhance server message serialization by peeking into message fields like "error", "result".
        json_str = message.model_dump_json()
        adapter = TypeAdapter(McpResponseOrError)
        return adapter.validate_json(json_str)
