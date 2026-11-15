"""Download the Residential Tenancies Act, 2006 from Ontario e-Laws and cache it on disk.

The cache holds the full statute text, so it lives in a gitignored directory and is never
committed. See "Statute source and copyright" in docs/details.md.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import AwareDatetime, BaseModel, ValidationError

ACT_CODE = "06r17"
PUBLIC_URL = f"https://www.ontario.ca/laws/statute/{ACT_CODE}"
# The endpoint the e-Laws web app itself calls to load the current consolidation of a statute.
API_URL = f"https://www.ontario.ca/laws/api/v2/legislation/en/doc-search/statute/{ACT_CODE}"
USER_AGENT = "lease-clause-checker/0.1 (open-source research project)"
TIMEOUT_SECONDS = 30.0
NOTICE = (
    "Source: Ontario e-Laws. Not an official version of the statute. "
    "© King's Printer for Ontario, 2006."
)


class ElawsError(RuntimeError):
    """e-Laws returned something other than a statute document."""


class _ApiDocument(BaseModel):
    """The fields this project uses from the e-Laws API response."""

    title: str
    description: str
    content: str


class CachedAct(BaseModel):
    """The Act as e-Laws served it, plus where and when it was retrieved.

    `content_sha256` identifies the exact text, so results can record which version of the
    statute they were produced against even though the text itself is not committed.
    """

    source_url: str
    api_url: str
    retrieved_at: AwareDatetime
    title: str
    consolidation: str
    content_sha256: str
    content_html: str


def fetch_act(client: httpx.Client) -> CachedAct:
    """Make one request to e-Laws for the current consolidation of the Act."""
    response = client.get(API_URL, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    try:
        # The API reports some errors as HTTP 200 with a JSON body like {"statusCode": 404}.
        document = _ApiDocument.model_validate_json(response.content)
    except ValidationError as error:
        raise ElawsError(f"Unexpected response from {API_URL}: {response.text[:200]!r}") from error
    return CachedAct(
        source_url=PUBLIC_URL,
        api_url=API_URL,
        retrieved_at=datetime.now(UTC),
        title=document.title,
        consolidation=" ".join(document.description.split()),
        content_sha256=hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
        content_html=document.content,
    )


def section_url(anchor: str) -> str:
    """Link to a section on e-Laws, using the section's HTML anchor (e.g. "BK19")."""
    return f"{PUBLIC_URL}#{anchor}"


def cache_path(cache_dir: Path) -> Path:
    return cache_dir / "elaws" / f"{ACT_CODE}_en.json"


def save_act(act: CachedAct, path: Path) -> None:
    """Write atomically, so an interrupted write can't leave a half-written cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(act.model_dump_json(indent=2), encoding="utf-8")
    partial.replace(path)


def load_act(path: Path) -> CachedAct:
    if not path.exists():
        raise FileNotFoundError(f"No cached statute at {path}. Run `lcc statute fetch` first.")
    try:
        return CachedAct.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise ElawsError(
            f"Cached statute at {path} is unreadable; re-run `lcc statute fetch --refresh`."
        ) from error
