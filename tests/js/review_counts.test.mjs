// The explorer's review counts (counts.js), in Node's built-in test runner: `make test-js`.
// The last test checks the Misquotes counts against data.json: the export in LCC_DATA, or
// site/data.json (`uv run lcc site`); it is skipped if neither exists.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { tierOf, reviewRows, reviewCounts } = require("../../src/lease_clause_checker/explorer_assets/counts.js");

const DATA_JSON = process.env.LCC_DATA || fileURLToPath(new URL("../../site/data.json", import.meta.url));

const hand = (states, changed, misleads) => ({ states_a_rule: states, meaning_changed: changed, misleads_here: misleads });

function answer(model, clause, review, { set = "main", method = "closedbook" } = {}) {
  return { set, method, model, clause, attempts: [review ? { hand_check: review } : {}] };
}

const MODELS = [{ key: "m1" }, { key: "m2" }];

test("each review lands in one tier", () => {
  assert.equal(tierOf(undefined), null);
  assert.equal(tierOf(hand("no", "", "")), "norule");
  assert.equal(tierOf(hand("yes", "no", "")), "same");
  assert.equal(tierOf(hand("yes", "", "")), "same");
  assert.equal(tierOf(hand("yes", "yes", "yes")), "mislead");
  assert.equal(tierOf(hand("yes", "yes", "unsure")), "unsure");
  assert.equal(tierOf(hand("yes", "yes", "no")), "changes");
});

test("the rows are the reviewed closed-book answers on the main set, by clause, then model", () => {
  const data = {
    models: MODELS,
    answers: [
      answer("m2", "c02", hand("yes", "no", "")),
      answer("m1", "c02", hand("no", "", "")),
      answer("m2", "c01", hand("yes", "no", "")),
      answer("m1", "c01", hand("yes", "yes", "yes")),
      answer("m1", "c03", null),
      answer("m1", "c04", hand("yes", "yes", "yes"), { set: "stress" }),
      answer("m1", "c05", hand("yes", "yes", "yes"), { method: "pipeline" }),
    ],
  };

  assert.deepEqual(
    reviewRows(data).map((a) => `${a.model} ${a.clause}`),
    ["m1 c01", "m2 c01", "m1 c02", "m2 c02"],
  );
});

test("the counts nest: rules within quotes, changes within rules, misleads within changes", () => {
  const data = {
    models: MODELS,
    answers: [
      answer("m1", "c01", hand("no", "", "")),
      answer("m1", "c02", hand("yes", "no", "")),
      answer("m1", "c03", hand("yes", "yes", "no")),
      answer("m2", "c01", hand("yes", "yes", "unsure")),
      answer("m2", "c02", hand("yes", "yes", "yes")),
    ],
  };

  assert.deepEqual(reviewCounts(data), {
    quotes: 5,
    rules: 4,
    changes: 3,
    misleads: 1,
    unsure: 1,
    tiers: { mislead: 1, unsure: 1, changes: 1, same: 1, norule: 1 },
    byModel: { m1: 3, m2: 2 },
  });
});

test(
  "the Misquotes counts match data.json",
  { skip: !existsSync(DATA_JSON) && `no ${DATA_JSON}: run \`uv run lcc site\` or set LCC_DATA` },
  () => {
    const data = JSON.parse(readFileSync(DATA_JSON, "utf8"));
    const counts = reviewCounts(data);
    const exported = data.story.hand_check;

    for (const key of ["quotes", "rules", "changes", "misleads", "unsure"]) {
      assert.equal(counts[key], exported[key], `${key}: the page counts ${counts[key]}, data.json ${exported[key]}`);
    }
    assert.deepEqual(counts.tiers, {
      mislead: exported.misleads,
      unsure: exported.unsure,
      changes: exported.changes - exported.misleads - exported.unsure,
      same: exported.rules - exported.changes,
      norule: exported.quotes - exported.rules,
    });
    assert.equal(
      Object.values(counts.byModel).reduce((a, b) => a + b, 0),
      exported.quotes,
    );
  },
);
