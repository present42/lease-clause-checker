"""Saved model calls: each call is one JSON record, and the records are the cache.

A record lives at `results/<prompt version>/<model>/<name>.json`. A run calls a model only for
records that don't exist yet, so re-running costs nothing, and scoring reads the records
without calling any API. A record whose prompt or request no longer matches is an error, not
something to overwrite: change the prompt's version instead.
"""

import hashlib
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from lease_clause_checker.models import ModelSpec
from lease_clause_checker.providers import Complete, request_for


class StaleRecordError(RuntimeError):
    """A saved record was made with a different prompt or request than the current one."""


class RunRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    clause_id: str
    model_key: str
    model_id: str
    served_model: str
    prompt_version: str
    prompt_sha256: str
    """Of the exact prompt sent, statute text included."""
    clauses_sha256: str
    request: dict[str, Any]
    """The request sent, except that statute text in the prompt is replaced by a reference."""
    requested_at: datetime
    text: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    raw: dict[str, Any]


class ClauseText(Protocol):
    """What a call needs to know about a clause: a test-set clause, or one from a lease."""

    @property
    def id(self) -> str: ...

    @property
    def text(self) -> str: ...


@dataclass(frozen=True)
class Question:
    """One call to make: the prompt sent and the prompt saved."""

    spec: ModelSpec
    clause: ClauseText
    prompt: str
    saved_prompt: str
    """`prompt` with any statute text replaced by a reference, since the repo doesn't
    redistribute the Act; the record's `prompt_sha256` still pins the text sent."""
    schema: dict[str, Any] | None = None

    def request(self) -> dict[str, Any]:
        return request_for(self.spec, self.prompt, self.schema)

    def saved_request(self) -> dict[str, Any]:
        return request_for(self.spec, self.saved_prompt, self.schema)


@dataclass(frozen=True)
class Store:
    """The saved records of one prompt version, made from one clauses file or lease, whose
    SHA-256 is `clauses_sha256`."""

    results_dir: Path
    prompt_version: str
    clauses_sha256: str

    def path(self, spec: ModelSpec, name: str) -> Path:
        return self.results_dir / self.prompt_version / spec.key / f"{name}.json"

    def load(self, question: Question, name: str) -> RunRecord | None:
        """The saved answer to `question`, or None; an answer to another question is an error."""
        path = self.path(question.spec, name)
        if not path.exists():
            return None
        record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if (record.clause_id, record.model_key) != (question.clause.id, question.spec.key):
            raise StaleRecordError(
                f"{path} holds {record.model_key}'s answer to {record.clause_id}"
            )
        if record.clauses_sha256 != self.clauses_sha256:
            raise StaleRecordError(f"{path} was made from a different clauses file")
        if (
            record.prompt_sha256 != sha256_text(question.prompt)
            or record.request != question.saved_request()
        ):
            raise StaleRecordError(
                f"{path} was made with a different prompt or request; "
                "give the changed prompt a new version instead of overwriting results"
            )
        return record

    def answer(
        self,
        question: Question,
        name: str,
        complete: Complete,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> tuple[RunRecord, bool]:
        """The saved answer if there is one, else a new one, saved; and whether a call was made."""
        saved = self.load(question, name)
        if saved is not None:
            return saved, False
        requested_at = now()
        completion = complete(question.spec, question.request())
        record = RunRecord(
            clause_id=question.clause.id,
            model_key=question.spec.key,
            model_id=question.spec.model_id,
            served_model=completion.served_model,
            prompt_version=self.prompt_version,
            prompt_sha256=sha256_text(question.prompt),
            clauses_sha256=self.clauses_sha256,
            request=question.saved_request(),
            requested_at=requested_at,
            text=completion.text,
            finish_reason=completion.finish_reason,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            raw=completion.raw,
        )
        _write_atomically(self.path(question.spec, name), record.model_dump_json(indent=1) + "\n")
        return record, True


@dataclass
class RunSummary:
    called: int = 0
    """Calls made."""
    cached: int = 0
    """Saved answers reused."""
    failures: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def count(self, *, called: bool) -> None:
        """Count one answer as it is obtained; jobs on worker threads call this."""
        with self._lock:
            if called:
                self.called += 1
            else:
                self.cached += 1


Job = Callable[[RunSummary], None]
"""Answers one (model, clause) pair, counting each answer in the summary as it goes."""


def run_jobs(jobs: Sequence[tuple[str, Job]], workers: int) -> RunSummary:
    """Run labelled jobs in parallel; a failed job is reported, not fatal.

    A failed call writes no record, so the next run retries it. Answers a job obtained before
    it failed are still counted. Callers check saved records for staleness before this starts.
    """
    summary = RunSummary()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(job, summary): label for label, job in jobs}
        try:
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as error:
                    summary.failures.append(f"{futures[future]}: {type(error).__name__}: {error}")
        except BaseException:  # e.g. Ctrl-C: don't start the paid calls still queued
            pool.shutdown(cancel_futures=True)
            raise
    return summary


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
