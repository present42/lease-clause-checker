"""Which differences in wording leave a provision's words the same: shared by the quoting
conventions (`verify.check_quote_with_conventions`) and the quote classifier (`quotediff`).

Two runs of words are the same but for cosmetics when, after dropping punctuation and articles,
their words pair up one to one and each pair differs at most in case or grammatical number
("reason"/"Reasons", "is"/"are"). A word in `CRITICAL_WORDS` is content like any other, so
adding or dropping one is never cosmetic.
"""

ARTICLES = frozenset({"a", "an", "the"})
# Words whose change reverses or narrows a provision; they are flagged wherever they differ.
CRITICAL_WORDS = frozenset(
    {"not", "no", "neither", "nor", "written", "except", "unless", "subject"}
)
# Verbs that change with grammatical number, as a plural noun beside them does.
AGREEMENT = ({"is", "are"}, {"was", "were"}, {"has", "have"}, {"does", "do"})


def content_words(words: list[str] | tuple[str, ...]) -> list[str]:
    """The words, without punctuation or articles."""
    return [w for w in words if w[:1].isalnum() and w.lower() not in ARTICLES]


def same_but_cosmetic(act: list[str] | tuple[str, ...], quote: list[str] | tuple[str, ...]) -> bool:
    act_words, quote_words = content_words(act), content_words(quote)
    return len(act_words) == len(quote_words) and all(map(same_word, act_words, quote_words))


def same_word(act: str, quote: str) -> bool:
    """The same word but for case or grammatical number ("reason", "Reasons"; "is", "are")."""
    a, q = act.lower(), quote.lower()
    if a == q or {a, q} in AGREEMENT:
        return True
    short, long = sorted((a, q), key=len)
    return len(short) >= 3 and long in (short + "s", short + "es", short[:-1] + "ies")
