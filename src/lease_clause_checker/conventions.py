"""The reading conventions every method's prompt states, word for word the same."""

from pathlib import Path

CONVENTIONS = "conventions-v1"


def load_conventions(prompts_dir: Path) -> str:
    """The text of `prompts/<CONVENTIONS>.txt`, shared by the baseline and pipeline prompts."""
    return (prompts_dir / f"{CONVENTIONS}.txt").read_text(encoding="utf-8").strip()
