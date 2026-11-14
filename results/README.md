# Results

Raw model answers, one JSON file per model and clause:
`<prompt version>/<model>/<clause id>.json`. They are written by `uv run lcc baseline run` and
read by `uv run lcc baseline report`, which never calls an API. A run only asks for answers
that are not saved here yet, so these files are also the cache.

Each record holds the model id requested and the one that answered, the prompt version and the
SHA-256 of the exact prompt sent, the SHA-256 of the test set, the full request, the UTC time of
the request, the visible answer text, why the answer ended, the billed token counts (thinking
included in output), and the provider's complete raw response. Claude responses include an
opaque `signature` for their hidden thinking; it takes up most of each file and is kept only so
the raw response is complete.

The pipeline (`uv run lcc pipeline run`) saves its first answer to each clause as
`<clause id>.json` and, if that answer's quote failed the check, the second as
`<clause id>.retry.json`. In its saved requests the statute text is replaced by the section
numbers and the SHA-256 of their text; `prompt_sha256` is still the hash of the exact prompt
sent. `uv run lcc pipeline report` re-checks every quote against the cached Act.

The closed-book run (`uv run lcc closedbook run`) saves one answer per clause under
`closedbook-v1+conventions-v1/`, with no retry; its prompt contains no statute text, so the
saved request is the full prompt. `uv run lcc closedbook report` checks each quote against the
cached Act.

The Gemini answers were served through Vertex AI's global endpoint (set by
`GOOGLE_CLOUD_PROJECT`, see `.env.example`), not the Gemini API; the model ids are the same.
Their costs in the reports use the Gemini API list prices in `models.py`.

The main test set's answers are at the top level. Two other sets have their own directories:
`stress/` holds the answers on the frozen stress set (`data/stress.jsonl`), and
`stress-candidates/` holds the one selection run on the stress candidates, which was scored
against draft labels (see [data/README.md](../data/README.md)).

`check/` holds the answers behind the README's `lcc check` example: the pipeline (Claude
Haiku 5.5) on [samples/lease_short.txt](../samples/lease_short.txt), in a directory named for
the first 16 hex digits of the lease's SHA-256. `lcc check` reuses them, so the example replays
without an API key. Answers for any other lease go to the gitignored `.cache/check/`.

The answers are model outputs, not legal advice.
