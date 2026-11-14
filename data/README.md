# Data

## `rules.yaml`

The RTA sections this project tests. Each rule has the section's heading as e-Laws shows it, a
one-line paraphrase written for this project, and a link to the section.
`uv run lcc statute check-rules` checks the headings and links against the cached Act.

## `clauses.jsonl`: the main test set

**Status: frozen on 11 November 2026, before any model saw it.** Two reviewers (the project
owner and a second reviewer) checked every label, so every clause has `reviewed: true`.

    SHA-256  4570d03b345c4232c2514d9f1d98ce03a0f98f6ee9f9fa54baddfe53ce8bf917  (38 clauses)

The main scoreboard only ever uses the file with this hash. Check it with
`shasum -a 256 data/clauses.jsonl`. Changing a clause means a new, separately reported set,
not an edit to this one.

One JSON object per line. Every clause is invented for this project. None is copied from a real
lease or template, and none contains personal data.

| field | meaning |
|---|---|
| `id` | `c01`, `c02`, … |
| `text` | the clause, in lease wording; the only field a model under test is shown |
| `label` | `void` or `allowed` under the Act |
| `kind` | `clear`; or `near_miss`, meaning a lawful clause that sounds restrictive or a void clause phrased softly |
| `section` | the section that decides the label; always one of the rules |
| `also_accept` | other sections that decide the label just as well, so a model citing one isn't marked wrong |
| `rationale` | one line explaining the label |
| `quote` | the decisive words of `section`, verbatim |
| `source` | the e-Laws link for `section`, the same as in `rules.yaml` |
| `reviewed` | `true` only after both reviewers have checked the label |

`also_accept` includes s. 4 ("a provision in a tenancy agreement that is inconsistent with this
Act or the regulations is void") for every void clause, because s. 4 is what voids a term that
breaks a duty set elsewhere in the Act.

A void clause about entering the unit also accepts s. 25 ("only in accordance with section 26
or 27") and whichever of ss. 26 and 27 the entry it allows fails to meet: s. 26 for entry
without written notice outside an emergency or consent at the time, s. 27 for entry that
lacks the written notice s. 27 requires.

`uv run lcc clauses review` checks every clause:

- the clause cites a rule, with that rule's link;
- its `quote` appears in the cached section's operative text (not its heading), starting and
  ending on word boundaries and inside a single subsection, with whitespace aside;
- every `also_accept` section exists in the Act.

If every clause passes, it writes `clauses_review.md`, a table for reviewing the labels. The
table records the SHA-256 of the `clauses.jsonl` it was made from, and a test fails if the two
drift apart.

The file must be UTF-8 without a byte-order mark, with `\n` line endings and a final newline.
The loader rejects anything else, and `.gitattributes` stops git from converting line endings,
so the SHA-256 depends only on the content.

### Labelling conventions

- A label must be decidable from the clause text and the Act alone. Clauses that depend on
  outside facts are left out, such as the s. 6.1 first-occupancy date. So are clauses that
  depend on regulations, such as charges under s. 134(1), "unless otherwise prescribed".
- Clauses are read as terms of an ordinary private rental: one the Act covers, with no s. 6.1
  exemption, and not student housing (s. 37(6)–(7)), a care home, a co-operative or a mobile
  home park. Rent is payable monthly where the clause says so. A model sees only `text`, so any
  prompt that is scored against these labels must state these conventions.
- `allowed` means the Act does not make the term void. It is not a view on whether the term is
  fair or wise.
- At most 4 clauses per section, so no one section dominates the score.

## `stress.jsonl`: the stress set

**Status: frozen on 13 November 2026.** The project owner and a second reviewer checked every
label, so every clause has `reviewed: true`.

    SHA-256  0519e3fbb53fb3c0e871f7f50a9bd48f8bca8aadb8e78d8ac43045341265dee5  (14 clauses)

These clauses were **chosen because Claude Haiku 5.5's baseline answer got them wrong**, so a
model's score on this set is not an estimate of its accuracy, and Haiku's baseline score is low
by design. Three things follow:

- Only 3 of the 14 were chosen for a wrong verdict (s02, s05, s15); the other 11 for a right
  verdict with a section that isn't accepted. The set stresses citations more than verdicts.
- They were chosen on the baseline's failures only, so comparing the baseline with the
  pipeline on this set favours the pipeline.
- They were chosen on one answer each. A fresh answer may be right, so results report the
  selection run beside a run made after the freeze.

The set is reported apart from the main scoreboard and never mixed into it: its answers are
saved under `results/stress/`, which the commands use by default for this file. The fields
and labelling conventions are the same as in `clauses.jsonl`.

How it was made:

1. 31 candidates were written as variants of the main set's near-miss patterns. Before any
   model saw them, a second reviewer read every draft label against the Act. One candidate
   ("Guests of the Tenant may not bring animals into the building") was dropped because s. 14
   could fairly be read as protecting only the tenant's own animals. Four were reworded to
   close a gap the reviewer found (s05, s18, s23 and s26 in the final numbering), three gained an
   accepted section, and five were re-tagged `clear`. The remaining 30 were numbered s01–s30:
   `stress_candidates.jsonl`. They keep `reviewed: false`, because that flag means the
   two-person review, which only the clauses kept below had.

       SHA-256  e7df924b43c745101666db1e3bcf9f22b7a7c4f44ca51a3ba6927d8b4e04fb76  (candidates)

2. Claude Haiku 5.5 answered all 30 once, with the baseline prompt
   (`results/stress-candidates/`, run with `--draft`). A candidate counted as a break if the
   verdict was wrong or the cited section was not accepted, a rule fixed before the run. 16
   broke: 3 on the verdict and 13 on the section only. To re-score that run:
   `uv run lcc baseline report --clauses data/stress_candidates.jsonl --draft --results
   results/stress-candidates --models claude-haiku-5-5 --details`.
3. The owner and a second reviewer checked the 16 labels and dropped 2: s07, which was lawful
   only because the Act is silent, not by its words, and s14, because a recurring transfer the
   tenant sets up is arguably not "automatic debiting" under s. 108(b). The other 14, unchanged
   apart from `reviewed: true`, are this set.

Ids are the candidates' ids, so each clause can be traced to its selection-run answer. Gemini
3.8 Flash, the other light model, could not be run when the set was chosen (the Gemini API
refused the project's key with HTTP 402; it pays for Vertex AI only), so it played no part in
choosing it; it was scored on the frozen set afterwards.

### Statute quotes

The `quote` fields, and the quotes in `clauses_review.md` and `stress_review.md`, are short
extracts from the Residential Tenancies Act, 2006 as consolidated on Ontario e-Laws
(consolidation period from September 21, 2026, retrieved November 8, 2026). Source: Ontario
e-Laws. Not an official version of the statute. © King's Printer for Ontario, 2006.

Not legal advice.
