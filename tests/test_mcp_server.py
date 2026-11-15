import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
from anyio.streams.text import TextReceiveStream
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types.version import LATEST_HANDSHAKE_VERSION

from lease_clause_checker.mcp_server import build_server
from lease_clause_checker.statute_tools import Statutes

VERBATIM = "A provision in a tenancy agreement prohibiting widgets is void."


async def _call(server: Any, name: str, arguments: dict[str, str]) -> Any:
    async with Client(server) as client:
        return await client.call_tool(name, arguments)


def test_the_server_lists_three_read_only_tools_with_output_schemas(cache_dir: Path) -> None:
    async def tools() -> Any:
        async with Client(build_server(Statutes.load(cache_dir))) as client:
            return (await client.list_tools()).tools

    listed = {tool.name: tool for tool in anyio.run(tools)}

    assert set(listed) == {"get_section", "verify_quote", "check_text"}
    for tool in listed.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.output_schema is not None
        assert "not an official version" in (tool.description or "")
        assert "Not legal advice" in (tool.description or "")
    summary = listed["verify_quote"].output_schema["properties"]["summary"]
    assert summary["description"] == "What was found, in a sentence or two."
    section = listed["get_section"].input_schema["properties"]["section"]
    assert "s. 27(2)" in section["description"]
    check_text = " ".join((listed["check_text"].description or "").split())
    assert "gives no verdict on any clause or tenancy term" in check_text
    findings = listed["check_text"].output_schema["$defs"]["Finding"]["properties"]
    assert findings["status"]["enum"] == [
        "checked",
        "uncited",
        "citation_only",
        "no_such_section",
        "other_statute",
        "unpaired_mark",
    ]


def test_verify_quote_returns_the_category_diff_and_notice(cache_dir: Path) -> None:
    server = build_server(Statutes.load(cache_dir))
    quote = "A provision in a tenancy agreement prohibiting gizmos is void."

    result = anyio.run(
        _call, server, "verify_quote", {"act": "RTA", "section": "1", "quote": quote}
    )

    assert result.is_error is False
    data = result.structured_content
    assert data["category"] == "content_words_differ"
    assert data["diff"] == (
        "A provision in a tenancy agreement prohibiting [-widgets-] {+gizmos+} is void."
    )
    assert "King's Printer for Ontario" in data["source"]["notice"]
    assert data["scope"].startswith("Checks wording")


def test_check_text_reports_each_quote_and_citation(cache_dir: Path) -> None:
    server = build_server(Statutes.load(cache_dir))
    text = (
        'Section 1 says "A provision in a tenancy agreement prohibiting gizmos is void." See s. 9.'
    )

    result = anyio.run(_call, server, "check_text", {"text": text})

    assert result.is_error is False
    checked, missing = result.structured_content["findings"]
    assert (checked["status"], checked["result"]["category"]) == ("checked", "content_words_differ")
    assert (missing["status"], missing["summary"]) == (
        "no_such_section",
        "The Act has no in-force section 9.",
    )


def test_an_unknown_section_is_a_tool_error_the_model_can_read(cache_dir: Path) -> None:
    server = build_server(Statutes.load(cache_dir))

    result = anyio.run(_call, server, "get_section", {"act": "RTA", "section": "s. 99"})

    assert result.is_error is True
    assert "no in-force section 99" in result.content[0].text


def test_lcc_mcp_serves_the_cached_act_over_stdio(cache_dir: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lease_clause_checker", "--cache-dir", str(cache_dir), "mcp"],
    )

    async def session() -> tuple[Any, Any]:
        with anyio.fail_after(60):
            return await _stdio_session(params)

    section, check = anyio.run(session)

    assert section.structured_content["text"] == VERBATIM
    [finding] = check.structured_content["findings"]
    assert finding["result"]["category"] == "verbatim"


async def _stdio_session(params: StdioServerParameters) -> tuple[Any, Any]:
    async with Client(params) as client:
        section = await client.call_tool("get_section", {"act": "RTA", "section": "1"})
        check = await client.call_tool("check_text", {"text": f'Section 1 reads "{VERBATIM}"'})
        return section, check


def test_lcc_mcp_writes_nothing_but_protocol_messages_to_stdout(cache_dir: Path) -> None:
    """The SDK's client tolerates a stray line on stdout, so this reads the raw stream, to the
    end: a print anywhere in the server (buffered until it exits) would corrupt the protocol
    for a stricter client."""
    command = [sys.executable, "-m", "lease_clause_checker", "--cache-dir", str(cache_dir), "mcp"]
    initialize = {
        "protocolVersion": LATEST_HANDSHAKE_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    }
    call = {"name": "get_section", "arguments": {"act": "RTA", "section": "1"}}

    async def exchange() -> list[str]:
        lines: list[str] = []
        with anyio.fail_after(60):
            async with await anyio.open_process(command) as process:
                assert process.stdin is not None
                assert process.stdout is not None
                replies = _lines(TextReceiveStream(process.stdout))
                await _send(process.stdin, {"id": 1, "method": "initialize", "params": initialize})
                lines += await _until(replies, 1)
                await _send(process.stdin, {"method": "notifications/initialized"})
                await _send(process.stdin, {"id": 2, "method": "tools/call", "params": call})
                lines += await _until(replies, 2)
                await process.stdin.aclose()
                lines += [line async for line in replies]  # to the end, flushed on exit
        return lines

    lines = anyio.run(exchange)

    assert [json.loads(line)["jsonrpc"] for line in lines] == ["2.0", "2.0"]
    assert json.loads(lines[-1])["result"]["structuredContent"]["text"] == VERBATIM


async def _send(stdin: Any, message: dict[str, Any]) -> None:
    await stdin.send((json.dumps({"jsonrpc": "2.0", **message}) + "\n").encode())


async def _lines(chunks: TextReceiveStream) -> AsyncIterator[str]:
    buffer = ""
    async for chunk in chunks:
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            yield line


async def _until(replies: AsyncIterator[str], reply_id: int) -> list[str]:
    """Every line up to and including the reply with `reply_id`."""
    lines = []
    async for line in replies:
        lines.append(line)
        if json.loads(line).get("id") == reply_id:
            break
    return lines
