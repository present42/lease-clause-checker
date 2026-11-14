"""An MCP server (stdio) that gives a model the Act's text and checks its quotes: `lcc mcp`.

The tools are thin wrappers over `statute_tools.Statutes`, which reads the e-Laws cache once at
start-up. A lookup that can't succeed (an unknown Act, section or citation) is a `ToolError`,
which the model reads; a quote that fails the check is a normal result that says how.
"""

from importlib.metadata import version
from inspect import cleandoc
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from lease_clause_checker.statute_tools import (
    SCOPE,
    QuoteResult,
    SectionText,
    Statutes,
    UnknownActError,
    UnknownSectionError,
)

INSTRUCTIONS = (
    "Tools for the Residential Tenancies Act, 2006 (Ontario), from a cached copy of the "
    "e-Laws text (not an official version). Use get_section to read a section before quoting "
    "it, and verify_quote to check any quote of the Act you write: it reports whether the "
    "quote is word for word in the cited section and, if not, how it differs (a word-level "
    f"diff and a category). {SCOPE}"
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True)
GET_SECTION = cleandoc(
    f"""
    The in-force text of one section of the Residential Tenancies Act, 2006 (Ontario), with its
    heading, e-Laws link, consolidation period and the King's Printer notice. If the citation
    names a subsection, that subsection (not a paragraph within it) is also returned on its
    own. {SCOPE}
    """
)
VERIFY_QUOTE = cleandoc(
    f"""
    Check whether a quote is word for word in the cited section of the Residential Tenancies
    Act, 2006 (Ontario), and if not, how it differs: verbatim, drops a negation, passes with
    quoting conventions (an ellipsis, cosmetic changes in brackets, a changed first capital),
    cosmetic (articles, punctuation, case, singular and plural), wrong section cited, content
    words differ, or not in the Act; with a word-level diff. The whole section is searched,
    and a note says if the quote is in another subsection than the one cited. {SCOPE}
    """
)
Act = Annotated[str, Field(description='The Act: "RTA" (Residential Tenancies Act, 2006).')]
Citation = Annotated[
    str, Field(description='A section number or citation, such as "14" or "s. 27(2)".')
]
Quote = Annotated[
    str, Field(description="The words as quoted, with any ellipsis or brackets they use.")
]


def build_server(statutes: Statutes) -> MCPServer:
    """The server, with `get_section` and `verify_quote` on `statutes`."""
    server = MCPServer(
        name="lease-clause-checker",
        instructions=INSTRUCTIONS,
        version=version("lease-clause-checker"),
    )

    @server.tool(description=GET_SECTION, annotations=READ_ONLY)
    def get_section(act: Act, section: Citation) -> SectionText:
        try:
            return statutes.get_section(act, section)
        except (UnknownActError, UnknownSectionError) as error:
            raise ToolError(str(error)) from error

    @server.tool(description=VERIFY_QUOTE, annotations=READ_ONLY)
    def verify_quote(act: Act, section: Citation, quote: Quote) -> QuoteResult:
        try:
            return statutes.verify_quote(act, section, quote)
        except (UnknownActError, UnknownSectionError) as error:
            raise ToolError(str(error)) from error

    return server
