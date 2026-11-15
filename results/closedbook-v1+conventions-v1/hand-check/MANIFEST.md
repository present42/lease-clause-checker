# Hand check of the 59 failed closed-book quotes: full record

Assembled 2026-11-15 from the session that ran the check. Times are UTC in the files.

## What happened, in order

1. **Claude's first suggestions** (`claude_suggestions_round1.json`). The owner worked four rows through by hand with Claude (Haiku c11, Haiku c25, Flash c18, Argon c24) before this point; those discussions shaped the suggestions.
2. **Round 1 review** (2026-11-15 00:43 UTC). Two fresh Claude Opus 5.5 reviewers, blind to the suggestions, **split** the 59 rows: reviewer-1a took the 35 Haiku and Opus rows (`rows_A.json`), reviewer-1b the 24 Flash and Argon rows (`rows_B.json`). Each row was judged by one reviewer. They had the definitions in their prompt but no written rules.
   - Disagreement with `claude_suggestions_round1.json`: **24 of 59 rows**. Most came from three unclear points, which the owner settled as rules 1–3; rule 4 came from the rows where the reviewers caught dropped sentence endings.
3. **Claude's second suggestions** (`claude_suggestions_round2.json`), revised under the four rules (`rules.md`).
4. **Round 2 review** (2026-11-15 01:15 UTC). Two more fresh reviewers (reviewer-2a, reviewer-2b), blind, same split, with `rules.md`.
   - Agreement with `claude_suggestions_round2.json`: **46 of 59 rows** (13 differ). On 12 of the 13, the reviewer's answer was adopted; on Haiku c34 Claude's category was kept (both questions agreed).
5. **Final** (`claude_suggestions_final.json`), approved by the owner on 2026-11-15. It matches round 2's reviewers on 58 of 59 rows, **after** adopting their 12 answers, so 58 of 59 is not an independent agreement figure. The independent figure is 46 of 59.

## How rows were compared

A row agrees when the category matches, question 1 matches (blank for not_in_act, no_quote, too_short, heading), and, where both said yes to question 1, question 2 matches. In round 1, Claude's "your call" rows count as q1 "unsure".

## Files

| File | What it is |
|---|---|
| `rules.md` | The owner's four rules, used in round 2 |
| `rows_A.json`, `rows_B.json` | Exactly what the reviewers were given for each row |
| `reviewer-*_prompt.txt` | Each reviewer's exact prompt, with the local scratch directory replaced by `<scratch>/`. The `prompt_sha256` in each reviewer's file is the hash of the original, unredacted prompt. |
| `round*_reviewer-*.json` | Each reviewer's answers, with reviewer, round, model, start time, prompt hash and input file |
| `claude_suggestions_*.json` | Claude's suggestions at each stage |
| `act_source.json` | The Act copy the reviewers searched: consolidation, retrieval date, SHA-256, notice. The full text itself is not included; it is the repo's cached e-Laws copy with that SHA-256 |

Model for every reviewer and for the suggestions: Claude Opus 5.5 (`claude-opus-5-5`).

The Act's text the reviewers searched (`act_sections.json` in the prompts) is not included. Statute words quoted in these files: Source: Ontario e-Laws. Not an official version of the statute. © King's Printer for Ontario, 2006. The judgements are the author's reading, not legal advice.
