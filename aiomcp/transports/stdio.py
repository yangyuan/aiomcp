import asyncio
import sys
from typing import AsyncIterator, Optional, Sequence, Tuple, Union

from pydantic import TypeAdapter, ValidationError

from aiomcp.contracts.mcp_message import (
    McpClientMessageAnnotated,
    McpMessage,
    McpServerMessageUnion,
)
from aiomcp.mcp_serialization import McpSerialization
from aiomcp.transports.base import McpClientTransport, McpServerTransport



class McpStdioClientTransport(McpClientTransport):
    def __init__(
        self,
        command: Union[str, Sequence[str]],
        *,
        cwd: Optional[str] = None,
        shell: bool = False,
    ) -> None:
        self._command = command
        self._cwd = cwd
        self._shell = shell
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def _spawn_process(self) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._shell:
            if isinstance(self._command, str):
                cmd_str = self._command
            else:
                parts = list(self._command)
                if len(parts) != 1 or not isinstance(parts[0], str):
                    raise ValueError(
                        f"{McpStdioClientTransport.__name__} when shell=True, command must be a single string or list[str] with 1 element"
                    )
                cmd_str = parts[0]
            process = await asyncio.create_subprocess_shell(
                cmd_str,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )
        else:
            if isinstance(self._command, str):
                args = [self._command]
            else:
                args = list(self._command)
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )
        if process.stdout is None or process.stdin is None:
            raise RuntimeError(
                f"{McpStdioClientTransport.__name__} failed to start stdio process"
            )
        self._process = process
        return process.stdout, process.stdin

    async def client_initialize(self):
        if self._reader is not None and self._writer is not None:
            return
        self._reader, self._writer = await self._spawn_process()

    async def client_messages(self) -> AsyncIterator[McpMessage]:
        assert self._reader is not None, "Client not initialized"
        reader = self._reader
        adapter = TypeAdapter(McpServerMessageUnion)
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                message = adapter.validate_json(line)
            except ValidationError:
                continue
            yield message

    async def client_send_message(self, message: McpMessage) -> bool:
        assert self._writer is not None, "Client not initialized"
        message = McpSerialization.process_client_message(message)
        payload = message.model_dump_json(exclude_none=True).encode("utf-8") + b"\n"
        self._writer.write(payload)
        await self._writer.drain()
        return True

    async def close(self):
        reader = self._reader
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
            try:
                await writer.wait_closed()
            except Exception:
                pass
        if reader is not None:
            reader.feed_eof()
        proc = self._process
        self._process = None
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


class McpStdioServerTransport(McpServerTransport):
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._raw_lines: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._client_to_server: asyncio.Queue[Optional[McpMessage]] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._decoder_task: Optional[asyncio.Task[None]] = None
        self._writer_lock = asyncio.Lock()
        self._closed = False

    async def server_initialize(self):
        if self._loop is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._raw_lines = asyncio.Queue()
        self._client_to_server = asyncio.Queue()
        self._writer_lock = asyncio.Lock()
        self._closed = False
        self._reader_task = asyncio.create_task(self._read_loop())
        self._decoder_task = asyncio.create_task(self._decode_loop())

    async def _read_loop(self) -> None:
        buffer = sys.stdin.buffer
        try:
            while True:
                line = await asyncio.to_thread(buffer.readline)
                if not line:
                    break
                await self._raw_lines.put(line)
        except asyncio.CancelledError:
            raise
        finally:
            await self._raw_lines.put(None)

    async def _decode_loop(self) -> None:
        adapter = TypeAdapter(McpClientMessageAnnotated)
        while True:
            line = await self._raw_lines.get()
            if line is None:
                break
            try:
                message = adapter.validate_json(line)
            except ValidationError:
                continue
            await self._client_to_server.put(message)
        await self._client_to_server.put(None)

    async def server_messages(self) -> AsyncIterator[tuple[McpMessage, str | None]]:
        while True:
            message = await self._client_to_server.get()
            if message is None:
                break
            yield message, None

    async def server_send_message(
        self, message: McpMessage, session_id: str | None = None
    ) -> bool:
        message = McpSerialization.process_server_message(message)
        payload = message.model_dump_json(exclude_none=True).encode("utf-8") + b"\n"

        async with self._writer_lock:
            await asyncio.to_thread(self._write_bytes, payload)
        return True

    @staticmethod
    def _write_bytes(data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        for task in (self._reader_task, self._decoder_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        self._reader_task = None
        if self._decoder_task is not None:
            self._decoder_task = None
        try:
            self._client_to_server.put_nowait(None)
        except Exception:
            pass
        self._loop = None
