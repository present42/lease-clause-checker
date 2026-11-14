# Baseline verdict misses, read by hand

Every answer in this folder whose verdict disagrees with the label, read in full on
12 November 2026 (Claude) and 13 November 2026 (Gemini). The question is whether a "false
alarm" (a lawful clause called unenforceable) came from reasoning outside the Residential
Tenancies Act, such as the Human Rights Code. The baseline prompt asks "Is this clause
enforceable?", which is broader than the labels (void under the Act or not), so such an answer
would be scored wrong while being fair.

**Result: none of the false alarms came from outside the Act.** Each of Claude Haiku 5.5's
rests on a requirement the model attributes to the Act that the Act does not impose. The Gemini
false alarms cite provisions the Act does contain (ss. 119 and 126, among others), applied in a
way the labels reject; each row quotes the label's rationale. Reasoning outside the Act did
appear, in Claude Haiku 5.5's two missed voids, but the model kept it apart from its verdict.

Claude Opus 5.5 got every verdict right.

## Claude Haiku 5.5

False alarms (lawful clause called not enforceable):

| clause | deciding section | cited | what the answer relies on |
|---|---|---|---|
| c10 | s. 26 | s. 27 | Entry to clean needs 24 hours' written notice. s. 26(2) lets a landlord enter without written notice to clean at times the tenancy agreement sets. |
| c14 | s. 27 | s. 30 | Showing to a buyer is allowed only after a notice of termination. s. 27(2) allows it on 24 hours' written notice. The answer says it is "relying on my recollection of the section numbering". |
| c24 | s. 106 | s. 106 | "The Act does not let the landlord raise the deposit later." s. 106(3) does let the landlord top up the rent deposit when the lawful rent rises. |
| c37 | s. 26 | s. 26 | Showing to prospective tenants needs written notice. s. 26(3) allows it without written notice after a notice of termination, between 8 a.m. and 8 p.m., if the landlord informs or tries to inform the tenant first. |

Missed voids (void clause called enforceable):

| clause | deciding section | cited | what the answer relies on |
|---|---|---|---|
| c01 | s. 14 | s. 14 | s. 14 voids a term "only to the extent it contravenes the Act", and a no-pets term does not. s. 14 says a term prohibiting animals in or about the residential complex is void. The answer adds that the Human Rights Code limits the clause for service animals, and says "the RTA itself is not the source of that limit". |
| c02 | s. 14 | s. 14 | Same as c01, including a separate Human Rights Code paragraph. |

## Gemini 3.8 Flash

False alarm (lawful clause called not enforceable):

| clause | deciding section | cited | what the answer relies on |
|---|---|---|---|
| c32 | s. 120 | s. 4 | A promise that increases "will not exceed the annual rent increase guideline" gives up the landlord's right under s. 126 to apply to the Board for an above-guideline increase, so under s. 4 the clause "cannot be enforced to prevent an otherwise lawful above-guideline increase". The label's rationale: "A promise to keep increases within the guideline is lawful." |

No missed voids.

## Gemini 4 Argon

False alarms (lawful clause called not enforceable):

| clause | deciding section | cited | what the answer relies on |
|---|---|---|---|
| c29 | s. 116 | s. 119 | "Once in any 12-month period" could allow an increase less than 12 months after the start of the tenancy or after a previous increase, which s. 119 forbids, so s. 4 voids the clause. Close to Gemini 4 Carbon's argument below, which reasons from the start of the tenancy only. The label's rationale: "An increase power with 90 days' written notice on the Board's form, at most once in 12 months and within the guideline, is lawful." |
| c31 | s. 119 | s. 4 | A promise of no increase in the first 24 months restricts the landlord's right under s. 119 to raise the rent after 12 months, and the parties can't contract out of the Act, so s. 4 voids it "even though this clause favours the tenant". The label's rationale: "Waiting 24 months is longer than the 12-month minimum." |
| c32 | s. 120 | s. 4 | A cap at the guideline conflicts with the Act's provisions for above-guideline increases (the answer cites ss. 121, 123 and 126), so s. 4 voids the clause "to the extent of that conflict". Close to Gemini 3.8 Flash's argument. The label's rationale: "A promise to keep increases within the guideline is lawful." |

No missed voids.

## Gemini 4 Carbon (dropped)

Run first, then dropped from the tables as a top-tier model; its records are kept.

False alarm (lawful clause called not enforceable):

| clause | deciding section | cited | what the answer relies on |
|---|---|---|---|
| c29 | s. 116 | s. 119 | "Once in any 12-month period" would let the landlord make the first increase within 12 months of the start of the tenancy, which s. 119(1)(b) forbids, so s. 4 voids the clause. The answer quotes s. 119(1)(a) word for word and paraphrases (b) ("in any other case" for the Act's "if clause (a) does not apply"). The label's rationale: "An increase power with 90 days' written notice on the Board's form, at most once in 12 months and within the guideline, is lawful." |

No answer: c32, c33 and c35 stopped at the 16,000-token output cap with no visible text
(15,997 of the 16,000 tokens were thinking). They count as wrong verdicts, and there is no
reasoning to read.

No missed voids.

The verdicts and sections in these tables are the ones the scorer read from each answer's final
two lines; the descriptions are a reading of the answer text above them.
