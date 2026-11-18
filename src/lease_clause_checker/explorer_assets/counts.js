"use strict";
// The review's counts, from data.json alone: no page and no DOM. The explorer loads this before
// explorer.js; tests/js loads it in Node.

// The review's tier for one failed quote: "norule" (states no rule from the Act), "same" (states
// the Act's rule unchanged), "mislead" (changes the rule and would mislead on its clause),
// "unsure" (changes the rule; the review couldn't decide) or "changes" (changes the rule, but
// would not mislead here). Null for an answer with no review.
function tierOf(hand) {
  if (!hand) return null;
  if (hand.states_a_rule === "no") return "norule";
  if (hand.meaning_changed !== "yes") return "same";
  if (hand.misleads_here === "yes") return "mislead";
  return hand.misleads_here === "unsure" ? "unsure" : "changes";
}

// Every reviewed quote (closed book, main set), by clause, then in the models' order.
function reviewRows(data) {
  const order = data.models.map((m) => m.key);
  return data.answers
    .filter((a) => a.set === "main" && a.method === "closedbook" && a.attempts[0].hand_check)
    .sort((x, y) => x.clause.localeCompare(y.clause) || order.indexOf(x.model) - order.indexOf(y.model));
}

// What the Misquotes page counts: the reviewed quotes, those that state a rule, change it, would
// mislead or are unsure, each tier's size and each model's rows.
function reviewCounts(data) {
  const rows = reviewRows(data);
  const tiers = { mislead: 0, unsure: 0, changes: 0, same: 0, norule: 0 };
  for (const a of rows) tiers[tierOf(a.attempts[0].hand_check)] += 1;
  const byModel = {};
  for (const m of data.models) byModel[m.key] = rows.filter((a) => a.model === m.key).length;
  return {
    quotes: rows.length,
    rules: rows.length - tiers.norule,
    changes: tiers.mislead + tiers.unsure + tiers.changes,
    misleads: tiers.mislead,
    unsure: tiers.unsure,
    tiers,
    byModel,
  };
}

if (typeof module !== "undefined") module.exports = { tierOf, reviewRows, reviewCounts };
