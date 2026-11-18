"""Reviewed quotes whose redline the code lines up with a different provision than the review
names, configured in `alignment_gaps.yaml` (`load`). The explorer says so above each redline;
`explorer.build` checks every entry against the note and the alignment it exports.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

FILE = Path(__file__).with_name("alignment_gaps.yaml")


class AlignmentGapError(ValueError):
    """The configuration is malformed, or lists one quote twice."""


class Gap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model: str
    """A model key, as in `models.MODELS`."""
    clause: str
    review: str
    """The provision the review's note names, such as "37(4)"."""


class Gaps(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    issue: int
    """The repository issue that describes the gap."""
    quotes: tuple[Gap, ...]


def load(path: Path = FILE) -> Gaps:
    """The gaps in `path`; `AlignmentGapError` if it is malformed or lists a quote twice."""
    try:
        gaps = Gaps.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise AlignmentGapError(f"{path}: {error}") from error
    keys = [(g.model, g.clause) for g in gaps.quotes]
    if len(keys) != len(set(keys)):
        raise AlignmentGapError(f"{path}: a quote is listed twice")
    return gaps
