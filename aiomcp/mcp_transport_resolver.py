import re

from aiomcp.transports.base import McpTransport
from aiomcp.transports.http import McpHttpTransport


class McpTransportResolver:
    @staticmethod
    def resolve(connection: str) -> McpTransport:

        pattern = re.compile(r"^http://([A-Za-z0-9.-]+):(\d+)(/.*)?$")
        match = pattern.match(connection)
        if match:
            hostname, port, path = match.groups()
            path = path or "/"
            return McpHttpTransport(hostname, int(port), path=path)
        else:
            raise ValueError(
                f"{McpTransportResolver.__name__} failed to resolve transport connection: {connection}"
            )
