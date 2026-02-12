import re
from typing import Optional
from urllib.parse import urlparse

from aiomcp.mcp_authorization import McpAuthorizationClient, McpAuthorizationServer
from aiomcp.transports.base import McpTransport
from aiomcp.transports.http import McpHttpTransport


class McpTransportResolver:
    @staticmethod
    def resolve(
        connection: str,
        *,
        authorization_client: Optional[McpAuthorizationClient] = None,
        authorization_server: Optional[McpAuthorizationServer] = None,
    ) -> McpTransport:

        parsed = urlparse(connection)
        if parsed.scheme in ("http", "https"):
            hostname = parsed.hostname or "localhost"
            default_port = 443 if parsed.scheme == "https" else 80
            port = parsed.port or default_port
            path = parsed.path or "/"
            scheme = parsed.scheme
            return McpHttpTransport(
                hostname,
                port,
                path=path,
                scheme=scheme,
                authorization_client=authorization_client,
                authorization_server=authorization_server,
            )
        else:
            raise ValueError(
                f"{McpTransportResolver.__name__} failed to resolve transport connection: {connection}"
            )
