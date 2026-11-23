"""Connecting to an MCP server with the official Python SDK.

The SDK's `Client` negotiates the protocol and makes the calls. The
transports here pass every message through unchanged and keep a copy of the
text the server wrote, so the runner can read each number as the server
wrote it (`jsonlit`), and check the SDK's parse of it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, TextIO

import anyio
import httpx2
import mcp_types as types
from anyio.streams.text import TextReceiveStream
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from mcp_types import Implementation

from modelcheck import __version__, jsonlit


@dataclass
class Recorder:
    """The JSON-RPC messages of one connection, as text, in both directions."""

    outgoing: list[str] = field(default_factory=list)
    incoming: list[str] = field(default_factory=list)
    _methods: dict[Any, str] = field(default_factory=dict)
    _responses: dict[Any, Any] = field(default_factory=dict)
    _last_id: dict[str, Any] = field(default_factory=dict)

    def sent(self, text: str) -> None:
        self.outgoing.append(text)
        msg = jsonlit.loads(text)
        if isinstance(msg, dict) and "method" in msg and "id" in msg:
            key = jsonlit.plain(msg["id"])
            self._methods[key] = msg["method"]
            self._last_id[msg["method"]] = key

    def received(self, text: str) -> None:
        self.incoming.append(text)
        try:
            msg = jsonlit.loads(text)
        except ValueError:
            return
        for m in msg if isinstance(msg, list) else [msg]:
            if isinstance(m, dict) and "id" in m and ("result" in m or "error" in m):
                self._responses[jsonlit.plain(m["id"])] = m

    def last_response(self, method: str) -> dict[str, Any] | None:
        """The response, with literal numbers, to the last request of `method`."""
        key = self._last_id.get(method)
        return None if key is None else self._responses.get(key)

    def exchange(self, method: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """The last request of `method` as sent, and its response."""
        key = self._last_id.get(method)
        if key is None:
            return None, None
        request = None
        for text in reversed(self.outgoing):
            m = jsonlit.loads(text)
            if isinstance(m, dict) and m.get("method") == method:
                request = m
                break
        return request, self._responses.get(key)


# ---- stdio ----------------------------------------------------------------------


@asynccontextmanager
async def stdio_transport(
    command: Sequence[str],
    recorder: Recorder,
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    errlog: TextIO | None = None,
) -> AsyncIterator[Any]:
    """Run `command` and exchange newline-delimited JSON-RPC over its stdio.

    The server inherits this process's environment, with `env` on top.
    """
    process = await anyio.open_process(
        list(command),
        env={**os.environ, **(env or {})},
        cwd=cwd,
        stderr=errlog if errlog is not None else sys.stderr,
    )
    read_send, read_recv = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_send, write_recv = anyio.create_memory_object_stream[SessionMessage](0)

    async def reader() -> None:
        assert process.stdout is not None
        buffer = ""
        async with read_send:
            try:
                async for chunk in TextReceiveStream(process.stdout):
                    lines = (buffer + chunk).split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        if not line.strip():
                            continue
                        recorder.received(line)
                        item: SessionMessage | Exception
                        try:
                            item = SessionMessage(
                                types.jsonrpc_message_adapter.validate_json(line, by_name=False)
                            )
                        except ValueError as exc:
                            item = exc
                        await read_send.send(item)
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                pass

    async def writer() -> None:
        assert process.stdin is not None
        async with write_recv:
            try:
                async for sm in write_recv:
                    text = sm.message.model_dump_json(by_alias=True, exclude_unset=True)
                    recorder.sent(text)
                    await process.stdin.send((text + "\n").encode("utf-8"))
            except (anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
                pass

    async with anyio.create_task_group() as tg:
        tg.start_soon(reader)
        tg.start_soon(writer)
        try:
            yield read_recv, write_send
        finally:
            with anyio.CancelScope(shield=True):
                await write_send.aclose()
                if process.stdin is not None:
                    await process.stdin.aclose()
                with anyio.move_on_after(5):
                    await process.wait()
                if process.returncode is None:
                    process.terminate()
                    with anyio.move_on_after(3):
                        await process.wait()
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                await read_recv.aclose()
            tg.cancel_scope.cancel()


# ---- streamable HTTP --------------------------------------------------------------


class _TeeStream(httpx2.AsyncByteStream):
    """Passes a response body through and gives each JSON-RPC message to the recorder."""

    def __init__(self, inner: Any, content_type: str, recorder: Recorder) -> None:
        self.inner = inner
        self.sse = "text/event-stream" in content_type
        self.json = "json" in content_type
        self.recorder = recorder
        self.buffer = ""

    def _events(self, final: bool) -> None:
        text = self.buffer.replace("\r\n", "\n").replace("\r", "\n")
        blocks = text.split("\n\n")
        self.buffer = "" if final else blocks.pop()
        for block in blocks:
            data = [ln[5:].removeprefix(" ") for ln in block.split("\n") if ln.startswith("data:")]
            if data:
                self.recorder.received("\n".join(data))

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.inner:
            if self.sse or self.json:
                self.buffer += chunk.decode("utf-8", errors="replace")
                if self.sse:
                    self._events(final=False)
            yield chunk
        if self.json and self.buffer.strip():
            self.recorder.received(self.buffer)
            self.buffer = ""
        elif self.sse:
            self._events(final=True)

    async def aclose(self) -> None:
        await self.inner.aclose()


class _TeeTransport(httpx2.AsyncBaseTransport):
    def __init__(self, recorder: Recorder) -> None:
        self.inner = httpx2.AsyncHTTPTransport()
        self.recorder = recorder

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await self.inner.handle_async_request(request)
        content_type = response.headers.get("content-type", "")
        return httpx2.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_TeeStream(response.stream, content_type, self.recorder),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


class _RecordingWriter:
    def __init__(self, inner: Any, recorder: Recorder) -> None:
        self.inner = inner
        self.recorder = recorder

    async def send(self, item: SessionMessage) -> None:
        self.recorder.sent(item.message.model_dump_json(by_alias=True, exclude_unset=True))
        await self.inner.send(item)

    async def aclose(self) -> None:
        await self.inner.aclose()

    async def __aenter__(self) -> _RecordingWriter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


@asynccontextmanager
async def http_transport(
    url: str, recorder: Recorder, headers: Mapping[str, str] | None = None
) -> AsyncIterator[Any]:
    """Streamable HTTP through the SDK, with response bodies copied to the recorder."""
    client = httpx2.AsyncClient(
        transport=_TeeTransport(recorder),
        timeout=httpx2.Timeout(30.0, read=300.0),
        # the copy is taken before decompression, so ask for none
        headers={"Accept-Encoding": "identity", **(headers or {})},
    )
    async with client, streamable_http_client(url, http_client=client) as (read, write):
        yield read, _RecordingWriter(write, recorder)


# ---- connection -------------------------------------------------------------------


@dataclass
class ToolOutcome:
    is_error: bool
    structured: Any
    """structuredContent as the server wrote it, numbers as `jsonlit.Num`."""
    sdk_structured: Any
    """structuredContent as the SDK parsed it."""
    text: str
    """The text content blocks, joined."""


@dataclass
class ServerSpec:
    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    mode: str = "auto"
    """The SDK's negotiation mode: "auto", "legacy", or a protocol version."""


class Connection:
    def __init__(self, spec: ServerSpec, errlog: TextIO | None = None) -> None:
        self.spec = spec
        self.recorder = Recorder()
        self.errlog = errlog
        self._client: Client | None = None
        self._cm: Any = None

    async def __aenter__(self) -> Connection:
        if self.spec.url:
            transport = http_transport(self.spec.url, self.recorder)
        elif self.spec.command:
            transport = stdio_transport(
                self.spec.command,
                self.recorder,
                env=self.spec.env,
                cwd=self.spec.cwd,
                errlog=self.errlog,
            )
        else:
            raise ValueError("a server needs a command or a URL")
        # no response cache: every call reaches the server
        self._cm = Client(
            transport,  # type: ignore[arg-type]
            mode=self.spec.mode,
            cache=None,
            client_info=Implementation(name="modelcheck", version=__version__),
        )
        self._client = await self._cm.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(*exc)

    @property
    def client(self) -> Client:
        assert self._client is not None
        return self._client

    def negotiation(self) -> dict[str, Any]:
        """What each side said about the protocol version, from the messages themselves."""
        out: dict[str, Any] = {"sdk_protocol_version": self.client.protocol_version}
        for method in ("server/discover", "initialize"):
            request, response = self.recorder.exchange(method)
            if request is None:
                continue
            params = jsonlit.plain(request.get("params") or {})
            entry: dict[str, Any] = {"client_sent": params.get("protocolVersion")}
            if response is not None and "result" in response:
                result = jsonlit.plain(response["result"])
                entry["server_answered"] = result.get("protocolVersion") or result.get(
                    "supportedVersions"
                )
                if "supportedVersions" in result:
                    entry["server_supported"] = result["supportedVersions"]
            elif response is not None:
                entry["server_error"] = jsonlit.plain(response.get("error"))
            out[method] = entry
        info = self.client.server_info
        if info is not None:
            out["server"] = {"name": info.name, "version": info.version}
        return out

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
        result = await self.client.call_tool(name, dict(arguments))
        raw = self.recorder.last_response("tools/call")
        structured = None
        if raw is not None and isinstance(raw.get("result"), dict):
            structured = raw["result"].get("structuredContent")
        text = "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")
        return ToolOutcome(
            is_error=bool(result.is_error),
            structured=structured,
            sdk_structured=result.structured_content,
            text=text,
        )

    async def read_resource(self, uri: str) -> str:
        result = await self.client.read_resource(uri)
        parts = []
        for c in result.contents:
            t = getattr(c, "text", None)
            if t is None:
                raise ValueError(f"{uri}: not a text resource")
            parts.append(t)
        return "".join(parts)
