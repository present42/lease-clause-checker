from datetime import UTC, datetime
from pathlib import Path

import pytest

from lease_clause_checker import elaws
from lease_clause_checker.statute import Section, parse_sections

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def sample_html() -> str:
    """Synthetic statute HTML in e-Laws markup."""
    return (FIXTURES / "elaws_sample.html").read_text(encoding="utf-8")


@pytest.fixture
def sections(sample_html: str) -> dict[str, Section]:
    """The sample Act's sections: 1, 2.1 (with a subsection headnote) and 4."""
    return parse_sections(sample_html)


@pytest.fixture
def cache_dir(tmp_path: Path, sample_html: str) -> Path:
    """A cache directory holding the sample Act."""
    act = elaws.CachedAct(
        source_url=elaws.PUBLIC_URL,
        api_url=elaws.API_URL,
        retrieved_at=datetime(2026, 11, 8, 20, 0, tzinfo=UTC),
        title="Sample Act",
        consolidation="Consolidation Period: From September 21, 2026.",
        content_sha256="0" * 64,
        content_html=sample_html,
    )
    elaws.save_act(act, elaws.cache_path(tmp_path))
    return tmp_path
