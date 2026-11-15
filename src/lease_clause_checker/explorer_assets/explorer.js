"use strict";
// The explorer: renders data.json (written by `lcc site`), routed by the URL hash:
// #/story, #/exhibit/<model>, #/closed/<clause>/<model>, #/clauses/<clause> and #/hand/<filter>.
// Nothing is computed here that the export did not already decide: verdicts, scores, quote
// checks, alignments, the hand check and every count shown come from data.json.

let DATA;
let METHODS;
const INDEX = { answers: new Map(), clauses: new Map(), models: new Map() };
const OUTCOMES = {
  correct: ["right", "right"],
  false_alarm: ["wrong: the clause is lawful", "wrong"],
  missed_void: ["wrong: the clause is void", "wrong"],
  unsure: ["unsure", "unsure"],
  no_verdict: ["no verdict", "unsure"],
};
const SECTION_RESULTS = {
  deciding: "the deciding section",
  also_accepted: "a section the label also accepts",
  other_section: "a section the label doesn't accept",
  not_in_act: "not an in-force section of the Act",
  none: "no section",
};
const VIEWS = {
  story: "The short version",
  exhibit: "Right verdict, quote not word for word",
  closed: "Closed book, clause by clause",
  clauses: "Every answer",
  hand: "Hand check",
};

function methods() {
  return {
    baseline: { name: "Baseline", note: "A plain question; the model answers without the statute text, in prose." },
    pipeline: {
      name: "Pipeline",
      note: `The text of the ${DATA.settings.rule_sections} rule sections in the prompt; the quote is checked in code, with one retry if it fails.`,
    },
    closedbook: {
      name: "Closed book",
      note: "The pipeline's question with no statute text in the prompt; the quote is checked, with no retry.",
    },
  };
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Text a screen reader announces but the page doesn't show.
function sr(text) {
  return el("span", { class: "sr-only" }, text);
}

function answerKey(method, model, clause) {
  return `${method}|${model}|${clause}`;
}

function sectionName(number) {
  const section = DATA.act.sections[number];
  return section ? `s. ${number}, ${section.heading}` : `s. ${number}`;
}

function sectionLink(number) {
  const section = DATA.act.sections[number];
  if (!section) return `s. ${number}`;
  return el("a", { href: section.url, rel: "noopener" }, sectionName(number));
}

// "a", "a and b", "a, b and c".
function andList(items) {
  return items.length < 3 ? items.join(" and ") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

// ---- Marked text ------------------------------------------------------------------------------

// A run of marked words: highlighted (in both), struck (the Act's, left out) or underlined (the
// quote's own), each with words a screen reader says.
function marked(kind, text) {
  if (kind === "same") return el("mark", { class: "same" }, sr("[lines up: "), text, sr("]"));
  if (kind === "del") return el("del", {}, sr("[the Act, left out: "), text, sr("]"));
  if (kind === "ins") return el("ins", {}, sr("[the quote's own words: "), text, sr("]"));
  return el("span", { class: kind === "context" ? "context" : null }, text);
}

// Tokens are [text, before, ...]: `before` is "\n" at a line start, " " after a space, "" if
// joined to the token before. Runs of one kind become one element; line breaks are kept.
function renderRuns(tokens, kindOf) {
  const out = [];
  let run = null;
  tokens.forEach((token, i) => {
    const kind = kindOf(i);
    const before = i === 0 ? "" : token[1];
    if (!run || run.kind !== kind || before === "\n") {
      run = { kind, text: "", before };
      out.push(run);
    } else if (before) {
      run.text += " ";
    }
    run.text += token[0];
  });
  return out.flatMap((r) => [r.before === "\n" ? el("br") : r.before ? " " : "", marked(r.kind, r.text)]);
}

function quoteView(alignment) {
  const tokens = alignment.quote;
  return renderRuns(tokens, (i) => (tokens[i][2] ? "plain" : "ins"));
}

function actView(alignment) {
  const tokens = DATA.act.sections[alignment.section].subsections[alignment.subsection];
  const same = alignment.same;
  const start = same[0][0];
  const end = same[same.length - 1][1];
  const inSame = (i) => same.some(([a, b]) => i >= a && i < b);
  return renderRuns(tokens, (i) => (inSame(i) ? "same" : i >= start && i < end ? "del" : "context"));
}


function legend() {
  return el(
    "ul",
    { class: "legend", "aria-label": "Marks" },
    el("li", {}, el("mark", { class: "same" }, "green"), " words line up with the Act"),
    el("li", {}, el("ins", {}, "yellow, underlined"), " words are the quote's own, not in the Act at that point"),
    el("li", {}, el("del", {}, "red, struck"), " words are in the Act but missing from the quote"),
  );
}

// ---- One answer -------------------------------------------------------------------------------

function checkText(attempt) {
  const s = attempt.section;
  const where = s ? `s. ${s}` : "the cited section";
  switch (attempt.check) {
    case "verified":
      return `The quote is word for word in ${where}.`;
    case "drops_negation":
      return `The quote's words are in ${where}, but each time right after a "no", "not", "neither" or "nor" that it leaves out.`;
    case "not_found":
      return `The quote is not word for word in ${where}.`;
    case "too_short":
      return `The quote is in ${where}, but it is under ${DATA.settings.min_quote_words} words, too short to pass the check.`;
    case "heading":
      return `The quote is only in a heading of ${where}, not its operative text.`;
    case "no_such_section":
      return `${s ? `s. ${s}` : "The cited number"} is not an in-force section of the Act, so the quote could not be checked.`;
    case "no_quote":
      return "The answer gave no quote.";
    default:
      return attempt.verdict === "unsure" ? "An unsure answer has no quote to check." : "";
  }
}

function alignmentText(attempt) {
  const alignment = attempt.alignment;
  if (!alignment || attempt.check === "verified") return "";
  const sub = `s. ${alignment.section}${alignment.subsection}`;
  if (alignment.matched_words === alignment.quote_words && alignment.same.length > 1) {
    return ` Every word of it is in ${sub}, in order, but not as one passage: the Act has words between them.`;
  }
  return ` ${alignment.matched_words} of its ${alignment.quote_words} words line up, in order, with ${sub}.`;
}

function attemptView(attempt, clause, title) {
  const parts = [];
  if (title) parts.push(el("p", { class: "attempt-title" }, title));
  if (!attempt.readable) {
    parts.push(el("p", { class: "check-line" }, "The answer was not readable JSON in the required format."));
    return el("div", { class: "attempt" }, parts);
  }
  const verdictClass = attempt.verdict === "unsure" ? "unsure" : "";
  parts.push(
    el(
      "p",
      { class: "meta" },
      el("span", {}, "Said ", el("b", { class: `verdict ${verdictClass}` }, attempt.verdict)),
      attempt.section ? el("span", {}, "citing ", sectionLink(attempt.section)) : null,
      attempt.category ? el("span", {}, "Quote: ", el("b", {}, attempt.category)) : null,
    ),
  );
  const line = checkText(attempt) + alignmentText(attempt);
  if (line) parts.push(el("p", { class: "check-line" }, line));
  if (attempt.stops_early) {
    parts.push(
      el(
        "p",
        { class: "warning" },
        el("b", {}, "Word for word, but maybe not complete: "),
        "the quote stops before a list item, or mid-phrase, with more of the subsection after it. It may leave out the words that decide the clause.",
      ),
    );
  }
  if (attempt.found_in && attempt.found_in.length) {
    const list = attempt.found_in.map((n) => `s. ${n}`).join(", ");
    parts.push(el("p", { class: "check-line" }, `These words are word for word in ${list}, not in the section cited.`));
  }
  if (attempt.quote && attempt.check) parts.push(comparePanes(attempt, clause));
  if (attempt.hand_check) parts.push(handCheckLine(attempt.hand_check));
  if (attempt.reason) parts.push(el("p", { class: "reason" }, el("b", {}, "The model's reason: "), attempt.reason));
  return el("div", { class: "attempt" }, parts);
}

function comparePanes(attempt, clause) {
  const alignment = attempt.alignment;
  const quotePane = el(
    "div",
    { class: "pane" },
    el("p", { class: "pane-title" }, "The model's quote (spacing and quotation marks evened out)"),
    el("p", { class: "statute" }, "“", alignment ? quoteView(alignment) : attempt.quote, "”"),
  );
  let actPane;
  if (alignment) {
    actPane = el(
      "div",
      { class: "pane" },
      el("p", { class: "pane-title" }, `The Act, s. ${alignment.section}${alignment.subsection}`),
      el("p", { class: "statute" }, actView(alignment)),
      el("p", { class: "notice" }, DATA.notice),
    );
  } else if (clause && attempt.section === clause.section) {
    actPane = el(
      "div",
      { class: "pane" },
      el("p", { class: "pane-title" }, `No run of words lines up. The label's decisive words, s. ${clause.section}`),
      el("p", { class: "statute" }, "“", clause.quote, "”"),
      el("p", { class: "notice" }, DATA.notice),
    );
  } else {
    actPane = el(
      "div",
      { class: "pane" },
      el("p", { class: "pane-title" }, "The Act"),
      el(
        "p",
        {},
        attempt.check === "no_such_section"
          ? "There is no such section to compare with."
          : `No run of ${DATA.settings.min_run} or more words lines up with any subsection of the section cited.`,
      ),
    );
  }
  return el("div", { class: "compare-grid" }, quotePane, actPane);
}

function handCheckLine(hand) {
  const parts = [`Hand check: ${hand.category}`];
  if (hand.category !== hand.code_category) parts.push(` (the code said ${hand.code_category})`);
  if (hand.states_a_rule === "no") parts.push("; states no rule from the Act");
  else {
    parts.push(hand.meaning_changed === "yes" ? "; changes what the Act says" : "; says what the Act says");
    if (hand.meaning_changed === "yes") {
      parts.push(hand.misleads_here === "yes" ? "; would mislead on this clause" : hand.misleads_here === "unsure" ? "; unsure whether it would mislead on this clause" : "; would not mislead on this clause");
    }
  }
  return el("p", { class: "hand-line" }, parts.join(""), ". ", hand.note ? el("span", { class: "note" }, hand.note) : null);
}

function outcomeView(answer) {
  const [words, cls] = OUTCOMES[answer.outcome];
  return el("span", { class: cls }, words);
}

function answerCard(answer, { showClause = true, showModel = true } = {}) {
  const clause = INDEX.clauses.get(answer.clause);
  const model = INDEX.models.get(answer.model);
  const card = el("article", { class: "card" });
  if (showClause) {
    card.append(
      el(
        "div",
        { class: "card-head" },
        el("span", { class: "id" }, clause.id),
        el("span", { class: "label" }, `Label: ${clause.label}, decided by ${sectionName(clause.section)}`),
      ),
      el("p", { class: "clause-text" }, clause.text),
    );
  }
  card.append(
    el(
      "p",
      { class: "meta" },
      showModel ? el("span", {}, el("b", {}, model.name), ` · ${METHODS[answer.method].name}`) : null,
      el("span", {}, "Verdict: ", outcomeView(answer)),
      el("span", {}, "Section: ", SECTION_RESULTS[answer.section_result]),
    ),
  );
  if (answer.method === "baseline") {
    card.append(
      el(
        "p",
        { class: "meta" },
        el("span", {}, "Read from the answer: ", el("b", {}, answer.verdict || "no verdict")),
        answer.section ? el("span", {}, "citing ", sectionLink(answer.section)) : null,
      ),
      el("details", {}, el("summary", {}, "The full answer"), el("pre", { class: "answer-text" }, answer.text)),
    );
    return card;
  }
  const attempts = answer.attempts;
  attempts.forEach((attempt, i) => {
    let title = null;
    if (attempts.length > 1) {
      title = i === 0 ? (attempt.readable ? "First answer (failed the check)" : "First answer (not readable)") : "Retry";
    }
    card.append(attemptView(attempt, clause, title));
  });
  if (answer.gave_up) card.append(el("p", { class: "check-line" }, "Both answers failed the check, so the pipeline answered unsure."));
  return card;
}

// ---- Views ------------------------------------------------------------------------------------

function heading(text) {
  return el("h2", { tabindex: "-1", class: "view-heading" }, text);
}

function storyView() {
  const story = DATA.story;
  const ex = story.example;
  const clause = INDEX.clauses.get(ex.clause);
  const model = INDEX.models.get(ex.model);
  const n = story.clauses;
  const hc = story.hand_check;
  const hand = DATA.hand_check;
  const right = (count, unsure) => `${count} of ${n}` + (unsure ? ` (${unsure} unsure)` : "");
  return [
    heading("Right verdicts, quotes not word for word"),
    el(
      "p",
      { class: "intro" },
      `${DATA.models.length} models judged ${n} synthetic Ontario lease clauses under the Residential Tenancies Act, 2006, each quoting the section that decides the clause: once with the statute in the prompt, and once without it (closed book).`,
    ),
    el(
      "table",
      { class: "grid numbers-table" },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          story.columns.map((h) => el("th", { scope: "col" }, h)),
        ),
      ),
      el(
        "tbody",
        {},
        story.rows.map((r) =>
          el(
            "tr",
            {},
            el("th", { scope: "row" }, r.model),
            el("td", {}, right(r.with_right, r.with_unsure)),
            el("td", {}, `${r.with_verified} of ${r.with_checked}`),
            el("td", {}, right(r.without_right, r.without_unsure)),
            el("td", {}, el("b", {}, `${r.without_verified} of ${r.without_with_verdict}`)),
          ),
        ),
      ),
    ),
    el(
      "p",
      { class: "note" },
      story.table_note,
    ),
    el(
      "p",
      {},
      el("b", {}, `The ${hc.quotes} closed-book quotes that failed, `),
      `${story.failed_from}: ${hc.rules} state a real provision of the Act; ${hc.changes} of those change the rule, and ${hc.misleads} of those ${hc.changes} would mislead about the clause they were quoted for`,
      hc.unsure ? ` (${hc.unsure} unsure)` : "",
      ". ",
      hand ? `${hand.labels} ${hand.recheck} ` : "",
      el("a", { href: "#/hand" }, "See the hand check"),
    ),
    el("h3", {}, `One closed-book answer: ${model.name} on ${clause.id}`),
    el("article", { class: "card story" },
      el("p", { class: "clause-text" }, clause.text),
      el("p", { class: "meta" }, el("span", {}, "Said ", el("b", { class: "verdict" }, ex.verdict), ` (${ex.right ? "right" : "wrong"})`), el("span", {}, "citing ", sectionLink(ex.section))),
      el("p", { class: "pane-title" }, `The quote against the whole of s. ${ex.section}${ex.subsection}`),
      legend(),
      el("p", { class: "statute" }, renderRuns(ex.tokens, (i) => ex.tokens[i][2])),
      el("p", { class: "notice" }, DATA.notice),
      ex.note ? el("p", { class: "hand-line" }, el("b", {}, "Hand check: "), ex.note) : null,
      el("p", {}, el("a", { href: `#/closed/${ex.clause}/${ex.model}` }, "This answer in the closed-book grid")),
    ),
    el(
      "p",
      { class: "explore" },
      "Explore all answers: ",
      el("a", { href: "#/exhibit" }, VIEWS.exhibit),
      " · ",
      el("a", { href: "#/closed" }, VIEWS.closed),
      " · ",
      el("a", { href: "#/clauses" }, VIEWS.clauses),
      " · ",
      el("a", { href: "#/hand" }, VIEWS.hand),
    ),
  ];
}

function closedBook(model) {
  return DATA.answers.filter((a) => a.method === "closedbook" && a.model === model);
}

function closedModels() {
  return DATA.models.filter((m) => closedBook(m.key).length);
}

function exhibitCounts(model) {
  const answers = closedBook(model);
  const right = answers.filter((a) => a.outcome === "correct");
  const failed = right.filter((a) => a.attempts[0].check !== "verified");
  return { total: answers.length, right, failed };
}

function exhibitView(modelKey) {
  const models = closedModels();
  const counts = new Map(models.map((m) => [m.key, exhibitCounts(m.key)]));
  if (!counts.has(modelKey)) modelKey = DATA.story.example.model;
  const chosen = counts.get(modelKey);
  const model = INDEX.models.get(modelKey);
  const pipe = DATA.pipeline_first_checks;
  const picker = el(
    "nav",
    { class: "model-picker", "aria-label": "Models" },
    models.map((m) => {
      const c = counts.get(m.key);
      return el(
        "a",
        { href: `#/exhibit/${m.key}`, "aria-current": m.key === modelKey ? "true" : null },
        el("span", { class: "name" }, m.name),
        el("span", { class: "count" }, el("strong", {}, c.failed.length), ` of ${c.right.length} right verdicts (${c.total} clauses)`),
      );
    }),
  );
  return [
    heading(VIEWS.exhibit),
    el(
      "p",
      { class: "intro" },
      "Closed book, each model answered the pipeline's question without the statute text in the prompt. ",
      "Code then checked its quote against the section it cited. These answers got the verdict right, ",
      "but the quote is not word for word in that section. Not word for word is not always wrong law: ",
      "some differ only cosmetically.",
    ),
    picker,
    el(
      "p",
      { class: "compare" },
      `With the statute in the prompt, ${pipe.verified} of ${pipe.checked} first pipeline answers with a verdict on the main set, from the ${DATA.models.length} models, had a quote that passed the same check.`,
    ),
    el("h3", {}, `${model.name}: ${chosen.failed.length} of ${chosen.right.length} right verdicts with a quote that failed`),
    legend(),
    chosen.failed.length
      ? el("div", { class: "cards" }, chosen.failed.map((a) => answerCard(a, { showModel: false })))
      : el("p", { class: "empty" }, "None: every right verdict came with a quote that passed."),
  ];
}

function cellText(answer) {
  const [, cls] = OUTCOMES[answer.outcome];
  const mark = { right: "✓", wrong: "✗", unsure: "?" }[cls];
  const attempt = answer.attempts[0];
  let quote = "not checked";
  let fail = false;
  if (attempt.check === "verified") quote = attempt.stops_early ? "quote passed, may stop early" : "quote passed";
  else if (attempt.check) {
    quote = "quote failed";
    fail = true;
  }
  const verdict = answer.verdict || "no verdict";
  return {
    nodes: [el("span", { class: cls }, `${mark} ${verdict}`), el("span", { class: fail ? "q fail" : "q" }, quote)],
    label: `${INDEX.models.get(answer.model).name} on ${answer.clause}: ${verdict}, ${OUTCOMES[answer.outcome][0]}; ${quote}`,
  };
}

function closedView(clauseId, modelKey) {
  const models = closedModels();
  const clauses = DATA.clauses.filter((c) => c.set === "main");
  const selected = INDEX.answers.get(answerKey("closedbook", modelKey, clauseId));
  const panel = el(
    "section",
    { class: "selected-panel", id: "selected", "aria-label": "The chosen answer" },
    selected
      ? [el("h3", { tabindex: "-1", id: "selected-heading" }, `${INDEX.models.get(selected.model).name} on ${selected.clause}`), legend(), answerCard(selected)]
      : el("p", { class: "empty" }, "Choose a cell to see the answer, its quote and the Act's words side by side."),
  );
  const head = el("tr", {}, el("th", { scope: "col" }, "Clause"), models.map((m) => el("th", { scope: "col" }, m.name)));
  const rows = clauses.map((c) =>
    el(
      "tr",
      {},
      el("th", { scope: "row", class: "clause-cell" }, el("b", {}, c.id), c.text.length > 90 ? `${c.text.slice(0, 88)}…` : c.text),
      models.map((m) => {
        const answer = INDEX.answers.get(answerKey("closedbook", m.key, c.id));
        if (!answer) return el("td", {}, "not run");
        const { nodes, label } = cellText(answer);
        const current = selected && selected === answer;
        return el(
          "td",
          {},
          el("a", { class: "cell-link", href: `#/closed/${c.id}/${m.key}`, "aria-label": label, "aria-current": current ? "true" : null }, nodes),
        );
      }),
    ),
  );
  return [
    heading(VIEWS.closed),
    el(
      "p",
      { class: "intro" },
      `${METHODS.closedbook.note} Each cell shows the verdict (✓ right, ✗ wrong, ? unsure) and whether the quote passed the check. `,
      "Main test set only.",
    ),
    panel,
    el("div", { class: "table-wrap" }, el("table", { class: "grid" }, el("thead", {}, head), el("tbody", {}, rows))),
  ];
}

function clausesView(clauseId) {
  const clause = INDEX.clauses.get(clauseId) || DATA.clauses[0];
  const setName = clause.set;
  const select = el(
    "select",
    { id: "set-filter" },
    el("option", { value: "main", selected: setName === "main" }, "Main test set"),
    el("option", { value: "stress", selected: setName === "stress" }, "Stress set"),
  );
  select.addEventListener("change", () => {
    const first = DATA.clauses.find((c) => c.set === select.value);
    location.hash = `#/clauses/${first.id}`;
  });
  const list = el(
    "ul",
    { class: "clause-list" },
    DATA.clauses
      .filter((c) => c.set === setName)
      .map((c) =>
        el(
          "li",
          {},
          el(
            "a",
            { href: `#/clauses/${c.id}`, "aria-current": c.id === clause.id ? "true" : null },
            el("b", {}, c.id),
            c.text.length > 70 ? `${c.text.slice(0, 68)}…` : c.text,
          ),
        ),
      ),
  );
  const accepted = clause.also_accept.length ? `; also accepted: ${clause.also_accept.map((n) => `s. ${n}`).join(", ")}` : "";
  const detail = el(
    "section",
    { "aria-label": `Answers for ${clause.id}` },
    el("h3", { tabindex: "-1", id: "clause-heading" }, `${clause.id}: ${clause.label}`),
    el("p", { class: "clause-text" }, clause.text),
    el("p", { class: "meta" }, el("span", {}, "Decided by ", sectionLink(clause.section), accepted)),
    el("div", { class: "pane" }, el("p", { class: "pane-title" }, `The label's decisive words, s. ${clause.section}`), el("p", { class: "statute" }, "“", clause.quote, "”"), el("p", { class: "notice" }, DATA.notice)),
    el("p", { class: "reason" }, el("b", {}, "Why: "), clause.rationale),
    el("h3", {}, "Answers"),
    legend(),
    Object.entries(METHODS).map(([method, info]) => {
      const answers = DATA.models.map((m) => INDEX.answers.get(answerKey(method, m.key, clause.id))).filter(Boolean);
      if (!answers.length) return null;
      return el(
        "div",
        { class: "method-block" },
        el("h4", {}, info.name),
        el("p", { class: "method-note" }, info.note),
        answers.map((a) => {
          const final = a.attempts ? a.attempts[a.attempts.length - 1] : null;
          let quote = final && final.check ? (final.check === "verified" ? "quote passed" : "quote failed") : "";
          if (quote && a.attempts.length > 1) quote += " on retry";
          return el(
            "details",
            { class: "answer" },
            el(
              "summary",
              {},
              el("span", { class: "model" }, INDEX.models.get(a.model).name),
              el("span", {}, outcomeView(a)),
              el("span", { class: "small" }, a.section ? `s. ${a.section}` : "no section"),
              quote ? el("span", { class: "small" }, quote) : null,
            ),
            answerCard(a, { showClause: false, showModel: false }),
          );
        }),
      );
    }),
  );
  return [
    heading(VIEWS.clauses),
    el("div", { class: "set-filter" }, el("label", { for: "set-filter" }, "Clauses:"), select),
    el("div", { class: "clause-browser" }, list, detail),
  ];
}

function handView(filter) {
  const hand = DATA.hand_check;
  const hc = DATA.story.hand_check;
  const rows = DATA.answers
    .filter((a) => a.method === "closedbook" && a.attempts[0].hand_check)
    .filter((a) => filter !== "misleads" || a.attempts[0].hand_check.misleads_here === "yes");
  const yesNo = (value) => (value === "yes" ? "yes" : value === "no" ? "no" : value || "—");
  const table = el(
    "table",
    { class: "grid hand" },
    el(
      "thead",
      {},
      el(
        "tr",
        {},
        ["Model", "Clause", "Quote", "Code's category", "Hand check", "States a rule", "Changes what the Act says", "Misleads on its clause", "Note"].map((h) => el("th", { scope: "col" }, h)),
      ),
    ),
    el(
      "tbody",
      {},
      rows.map((a) => {
        const attempt = a.attempts[0];
        const h = attempt.hand_check;
        const quote = attempt.quote.length > 120 ? `${attempt.quote.slice(0, 118)}…` : attempt.quote;
        return el(
          "tr",
          {},
          el("td", {}, INDEX.models.get(a.model).name),
          el("th", { scope: "row" }, el("a", { href: `#/closed/${a.clause}/${a.model}` }, a.clause)),
          el("td", { class: "quote-cell" }, quote ? `“${quote}”` : "no quote"),
          el("td", {}, h.code_category),
          el("td", {}, h.category === h.code_category ? "agrees" : h.category),
          el("td", {}, h.states_a_rule),
          el("td", {}, h.states_a_rule === "no" ? "—" : yesNo(h.meaning_changed)),
          el("td", {}, h.meaning_changed === "yes" ? yesNo(h.misleads_here) : "—"),
          el("td", { class: "note" }, h.note),
        );
      }),
    ),
  );
  return [
    heading(VIEWS.hand),
    el(
      "p",
      { class: "intro" },
      `The ${hc.quotes} closed-book quotes that failed the word-for-word check, read by hand: does the quote state a rule from the Act, does it change what the Act says, and would it mislead on the clause it was quoted for? Of the ${hc.quotes}, ${hc.rules} state a rule; ${hc.changes} of those change it, and ${hc.misleads} would mislead${hc.unsure ? ` (${hc.unsure} unsure)` : ""}. Not legal advice.${hand ? ` ${hand.labels} ${hand.recheck}` : ""}`,
    ),
    hand ? el("p", { class: "method" }, el("b", {}, "How it was done. "), hand.method, " ", el("a", { href: hand.record }, "The full record"), ", ", el("a", { href: hand.csv }, "the CSV"), ".") : null,
    el(
      "nav",
      { class: "model-picker", "aria-label": "Filter" },
      el("a", { href: "#/hand", "aria-current": filter !== "misleads" ? "true" : null }, el("span", { class: "name" }, `All ${hc.quotes}`)),
      el("a", { href: "#/hand/misleads", "aria-current": filter === "misleads" ? "true" : null }, el("span", { class: "name" }, `The ${hc.misleads} that would mislead`)),
    ),
    el("div", { class: "table-wrap" }, table),
  ];
}

// ---- Routing ----------------------------------------------------------------------------------

function route(event) {
  const [view = "story", a, b] = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const main = document.getElementById("main");
  // What the reader was on, to give it focus again if the new view has it (a picker or filter).
  const active = document.activeElement;
  const was = active && main.contains(active) ? active.getAttribute("href") || active.id : null;
  let nodes;
  let current = view;
  if (view === "exhibit") nodes = exhibitView(a);
  else if (view === "closed") nodes = closedView(a, b);
  else if (view === "clauses") nodes = clausesView(a);
  else if (view === "hand") nodes = handView(a);
  else {
    current = "story";
    nodes = storyView();
  }
  main.replaceChildren(...nodes.flat().filter(Boolean));
  for (const tab of document.querySelectorAll(".tabs a")) {
    if (tab.dataset.view === current) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  document.title = `${VIEWS[current]}: Lease Clause Explorer`;
  if (!event) return; // the first render: leave focus where the browser put it
  document.getElementById("status").textContent = VIEWS[current];
  const again = was && [...main.querySelectorAll("[href], [id]")].find((node) => node.getAttribute("href") === was || node.id === was);
  const target =
    (view === "closed" && a && document.getElementById("selected-heading")) ||
    (view === "clauses" && a && !again && document.getElementById("clause-heading")) ||
    again ||
    main.querySelector(".view-heading");
  target.focus({ preventScroll: true });
  // Scroll only if the target is out of sight, or on a narrow screen where the detail sits
  // below the list.
  const box = target.getBoundingClientRect();
  const hidden = box.top < 0 || box.bottom > window.innerHeight;
  if (hidden || window.matchMedia("(max-width: 820px)").matches) target.scrollIntoView({ block: "start" });
}

function footer() {
  const prompts = Object.entries(DATA.prompts).map(([method, version]) => `${METHODS[method].name}: ${version}`).join("; ");
  const repo = DATA.settings.repository;
  document.getElementById("footer").replaceChildren(
    el(
      "p",
      {},
      `Statute text: ${DATA.notice} Reproduced here: the subsections that answers' quotes line up with, each clause's decisive words and the headings of the sections cited; the models' quotes and reasons also repeat the Act's words. The full Act is on `,
      el("a", { href: DATA.act.url }, "e-Laws"),
      ".",
    ),
    el("p", {}, `Act text retrieved ${DATA.act.retrieved} (SHA-256 ${DATA.act.sha256.slice(0, 12)}…). Prompts: ${prompts}.`),
    el(
      "p",
      {},
      "Built by ",
      el("code", {}, "lcc site"),
      " from the saved answers in the ",
      el("a", { href: repo }, "repository"),
      `: the ${DATA.models.length} models' answers on the main and stress sets.`,
      DATA.dropped_models.length
        ? ` ${andList(DATA.dropped_models)}, run first and then left out of the comparison as a top-tier model, is not shown; its records are kept in the repository.`
        : "",
      " The stress-candidate selection run and the lease-check demo are not shown. Not legal advice.",
    ),
  );
}

async function start() {
  try {
    const response = await fetch("data.json");
    if (!response.ok) throw new Error(`data.json: HTTP ${response.status}`);
    DATA = await response.json();
  } catch (error) {
    document.getElementById("main").replaceChildren(el("p", {}, `Could not load the answers (${error.message}).`));
    return;
  }
  METHODS = methods();
  for (const m of DATA.models) INDEX.models.set(m.key, m);
  for (const c of DATA.clauses) INDEX.clauses.set(c.id, c);
  for (const a of DATA.answers) INDEX.answers.set(answerKey(a.method, a.model, a.clause), a);
  footer();
  route();
  window.addEventListener("hashchange", route);
}

start();
