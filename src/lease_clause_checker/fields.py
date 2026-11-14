"""Field types shared by the hand-written data files (`rules.yaml`, `clauses.jsonl`)."""

from typing import Annotated

from pydantic import StringConstraints

# \p{Cc} is control characters (including \n and \r); \p{Zl} and \p{Zp} are U+2028 and U+2029.
OneLine = Annotated[
    str,
    StringConstraints(
        pattern=r"^[^\s\p{Cc}](?:[^\p{Cc}\p{Zl}\p{Zp}]*[^\s\p{Cc}])?$",
    ),
]
"""Non-empty text with no line breaks or other control characters and no surrounding spaces.

Values are rejected rather than trimmed, so what a file says is exactly what is used.
"""

SectionNumber = Annotated[str, StringConstraints(pattern=r"^\d+(?:\.\d+)*$")]
"""An RTA section number such as "14" or "6.1"."""
