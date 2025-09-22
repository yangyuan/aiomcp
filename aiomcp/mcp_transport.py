import re
from typing import Optional, Tuple

from aiomcp.transports.http import McpHttpTransport


class McpTransportResolver:
    @staticmethod
    def resolve(connection: str) -> Tuple[str, Optional[int], str]:

        pattern = re.compile(r"^http://([A-Za-z0-9.-]+):(\d+)(/.*)?$")
        match = pattern.match(connection)
        if match:
            hostname, port, path = match.groups()
            return McpHttpTransport(hostname, int(port), path=path)
        else:
            raise ValueError(
                f"{McpTransportResolver.__name__} failed to resolve transport connection: {connection}"
            )
