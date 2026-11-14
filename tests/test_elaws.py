import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from lease_clause_checker import elaws

API_BODY = {
    "title": "Residential Tenancies Act, 2006, S.O. 2006, c. 17",
    "description": "Consolidation Period:\xa0 From September 21, 2026 to the e-Laws currency date.",
    "content": '<p class="section"><b>1 </b>Text.</p>',
}


def serving(
    body: object, status: int = 200, *, raw: bytes | None = None
) -> tuple[httpx.Client, list[httpx.Request]]:
    """A client whose every request gets the same response; also returns the requests made."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if raw is not None:
            return httpx.Response(status, content=raw)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handle)), requests


def test_fetch_records_source_retrieval_time_and_hash() -> None:
    client, requests = serving(API_BODY)

    act = elaws.fetch_act(client)

    assert [str(r.url) for r in requests] == [elaws.API_URL]
    assert requests[0].headers["User-Agent"] == elaws.USER_AGENT
    assert act.source_url == elaws.PUBLIC_URL
    assert act.title == API_BODY["title"]
    assert act.consolidation == (
        "Consolidation Period: From September 21, 2026 to the e-Laws currency date."
    )
    assert act.content_html == API_BODY["content"]
    assert act.content_sha256 == hashlib.sha256(API_BODY["content"].encode()).hexdigest()
    assert act.retrieved_at.tzinfo is not None
    assert abs((datetime.now(UTC) - act.retrieved_at).total_seconds()) < 60


def test_fetch_rejects_error_reported_in_a_200_body() -> None:
    client, _ = serving({"statusCode": 404, "message": "Not found"})

    with pytest.raises(elaws.ElawsError, match="Unexpected response"):
        elaws.fetch_act(client)


def test_fetch_rejects_a_200_that_is_not_json() -> None:
    client, _ = serving(None, raw=b"<html>JavaScript required</html>")

    with pytest.raises(elaws.ElawsError, match="JavaScript required"):
        elaws.fetch_act(client)


def test_fetch_raises_on_http_error() -> None:
    client, _ = serving({}, status=503)

    with pytest.raises(httpx.HTTPStatusError):
        elaws.fetch_act(client)


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    client, _ = serving(API_BODY)
    act = elaws.fetch_act(client)
    path = elaws.cache_path(tmp_path)

    elaws.save_act(act, path)

    assert elaws.load_act(path) == act


def test_load_without_cache_says_how_to_fetch(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="lcc statute fetch"):
        elaws.load_act(elaws.cache_path(tmp_path))


def test_load_of_corrupt_cache_says_how_to_refresh(tmp_path: Path) -> None:
    path = elaws.cache_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"title": "truncated', encoding="utf-8")

    with pytest.raises(elaws.ElawsError, match="--refresh"):
        elaws.load_act(path)


def test_section_url_links_to_the_anchor() -> None:
    assert elaws.section_url("BK19") == "https://www.ontario.ca/laws/statute/06r17#BK19"
