"use strict";
// The explorer: renders data.json (written by `lcc site`), routed by the URL hash:
// #/story, #/exhibit/<model>, #/closed/<clause>/<model>, #/clauses/<clause>, #/hand/<filter>,
// and #/card (the preview image's layout). Nothing is decided here that the export did not
// already decide: verdicts, scores, quote checks, alignments and the hand check come from
// data.json, and every count shown is counted from it.

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
// Each view's tab and its heading.
const VIEWS = {
  story: "Summary",
  exhibit: "Misquotes",
  closed: "Grid",
  clauses: "By clause",
  hand: "Hand check",
};
const HEADINGS = {
  exhibit: "Right verdict, quote not word for word",
  closed: "Without the statute: every verdict and quote check",
  clauses: "Every answer, clause by clause",
  hand: "Hand check of the closed-book quotes that failed",
};

function methods() {
  return {
    baseline: { name: "Plain question", note: "Asked whether the clause is enforceable, with no statute text; the model answers in prose. (The repository calls this the baseline.)" },
    pipeline: {
      name: "With the statute",
      note: `The text of the ${DATA.settings.rule_sections} sections the test set is about in the prompt; the model must quote the section that decides the clause, the quote is checked in code, and a failed one is asked again once. (The repository calls this the pipeline.)`,
    },
    closedbook: {
      name: "Without the statute (closed book)",
      note: "The same question with no statute text in the prompt, so the model quotes from memory; the quote is checked, with no second try.",
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
  for (const child of children.flat(Infinity)) {
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

// A model's name, with a short form ("Opus 5.5", "Flash 3.8") that narrow screens show instead.
function modelName(name) {
  return [el("span", { class: "long-name" }, name), el("span", { class: "short-name" }, name.replace(/^Claude /, "").replace(/^Gemini ([\d.]+) (\w+)$/, "$2 $1"))];
}

// "a", "a and b", "a, b and c".
function andList(items) {
  return items.length < 3 ? items.join(" and ") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

// ---- Marked text ------------------------------------------------------------------------------

// A run of a redline: plain (in both), struck (the Act's, not in the quote) or underlined (the
// quote's own), the marked ones with words a screen reader says.
function marked(kind, text) {
  if (kind === "del") return el("del", {}, sr("[in the Act, not in the quote: "), text, sr("]"));
  if (kind === "ins") return el("ins", {}, sr("[the quote's own words: "), text, sr("]"));
  return el("span", {}, text);
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

// The quote against its whole subsection, the one redline every view and the README show:
// the export gives runs of [kind, start, end] into the subsection's tokens ("same", "del") and
// the quote's ("ins").
function redlineTokens(alignment) {
  const act = DATA.act.sections[alignment.section].subsections[alignment.subsection];
  const tokens = [];
  for (const [kind, start, end] of alignment.redline) {
    const source = kind === "ins" ? alignment.quote : act;
    for (let i = start; i < end; i++) tokens.push([source[i][0], source[i][1], kind]);
  }
  return tokens;
}

function redline(alignment, attempt) {
  const tokens = redlineTokens(alignment);
  const where = `s. ${alignment.section}${alignment.subsection}`;
  let title = `Redline: ${where} → the model's quote`;
  if (attempt && attempt.check === "verified") title = `Word for word in ${where}; struck: the rest of ${where}, which the quote leaves out`;
  else if (attempt && attempt.section !== alignment.section) title = `Redline: ${where}, where the quote's words come from (it cites s. ${attempt.section}) → the model's quote`;
  return el(
    "div",
    { class: "redline" },
    el("p", { class: "redline-title" }, title),
    el("p", { class: "statute" }, renderRuns(tokens, (i) => tokens[i][2])),
    el("p", { class: "notice" }, DATA.notice),
  );
}

function legend() {
  return el(
    "p",
    { class: "legend" },
    "In a redline, ",
    el("del", {}, "struck red"),
    " words are in the Act but not in the quote, ",
    el("ins", {}, "underlined blue"),
    " words are the quote's own, and the rest is in both.",
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
  if (alignment.matched_words === alignment.quote_words && alignment.passages > 1) {
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
  if (attempt.hand_check) parts.push(handCheckLine(attempt.hand_check));
  if (attempt.quote && attempt.check) parts.push(quoteBlock(attempt, clause));
  if (attempt.reason) parts.push(el("p", { class: "reason" }, el("b", {}, "The model's reason: "), attempt.reason));
  return el("div", { class: "attempt" }, parts);
}

function quoteBlock(attempt, clause) {
  const alignment = attempt.alignment;
  if (alignment && (alignment.lined_up || attempt.check === "verified")) return redline(alignment, attempt);
  const quote = el("p", { class: "statute" }, "“", attempt.quote, "”");
  if (alignment) {
    return el(
      "div",
      { class: "redline" },
      el("p", { class: "redline-title" }, "The model's quote"),
      quote,
      el("p", { class: "check-line" }, `Only ${alignment.matched_words} of its ${alignment.quote_words} words line up with s. ${alignment.section}${alignment.subsection}, scattered: too few to show as a redline.`),
    );
  }
  if (attempt.check === "no_such_section") {
    return el("div", { class: "redline" }, el("p", { class: "redline-title" }, "The model's quote"), quote, el("p", { class: "check-line" }, "There is no such section to compare it with."));
  }
  const parts = [
    el("p", { class: "redline-title" }, "The model's quote"),
    quote,
    el("p", { class: "check-line" }, `No run of ${DATA.settings.min_run} or more words lines up with any subsection of the section cited.`),
  ];
  if (clause && attempt.section === clause.section) {
    parts.push(el("p", { class: "redline-title" }, `The label's decisive words, s. ${clause.section}`), el("p", { class: "statute" }, "“", clause.quote, "”"), el("p", { class: "notice" }, DATA.notice));
  }
  return el("div", { class: "redline" }, parts);
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
  const misleads = hand.meaning_changed === "yes" && hand.misleads_here === "yes";
  return el(
    "div",
    { class: misleads ? "callout misleads" : "callout" },
    hand.note ? el("p", { class: "callout-note" }, hand.note) : null,
    el("p", { class: "hand-line" }, parts.join(""), ". Not legal advice."),
  );
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
  if (answer.gave_up) card.append(el("p", { class: "check-line" }, "Both answers failed the check, so the answer counts as unsure."));
  return card;
}

// ---- Views ------------------------------------------------------------------------------------

function heading(text) {
  return el("h2", { tabindex: "-1", class: "view-heading" }, text);
}

// The caveats in full: on the opening view and at the foot of every page; the header has them
// in one line.
const CAVEATS = [
  ["Checks wording, not legal meaning.", "A quote that passes is word for word in the section it cites; that says nothing about whether the reasoning is right."],
  ["Word for word is not complete.", "A quote can pass and still stop before the list item or condition that decides the clause; such quotes are flagged."],
  ["Not an official version.", "The Act's text is a dated copy from Ontario e-Laws."],
  ["Not legal advice.", "These are model outputs scored against test labels, and they may be wrong. For a question about a tenancy, contact a lawyer, a community legal clinic or the Landlord and Tenant Board."],
];

function caveatsList() {
  return el("ul", { class: "caveats" }, CAVEATS.map(([head, text]) => el("li", {}, el("strong", {}, head), " ", text)));
}

// A table in a box that scrolls sideways, with a hint shown when it doesn't fit (`markOverflow`).
function tableBox(table, label) {
  return el(
    "div",
    { class: "table-box" },
    el("p", { class: "scroll-hint", "aria-hidden": "true" }, "Scroll sideways to see every column →"),
    el("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": label }, table),
  );
}

function markOverflow() {
  for (const strip of document.querySelectorAll(".tabs")) strip.classList.toggle("overflows", strip.scrollWidth > strip.clientWidth + 1);
  for (const box of document.querySelectorAll(".table-box")) {
    const wrap = box.querySelector(".table-wrap");
    box.classList.toggle("overflows", wrap.scrollWidth > wrap.clientWidth + 1);
  }
}

function storyView() {
  const story = DATA.story;
  const ex = story.example;
  const clause = INDEX.clauses.get(ex.clause);
  const n = story.clauses;
  const hc = story.hand_check;
  const hand = DATA.hand_check;
  const right = (count, unsure) => [`${count} of ${n}`, unsure ? el("span", { class: "unsure-note" }, ` (${unsure} unsure)`) : null];
  const groups = story.column_groups;
  const table = el(
    "table",
    { class: "grid numbers-table" },
    el(
      "thead",
      {},
      el("tr", {}, el("td", {}), groups.map(([group, headings]) => el("th", { scope: "colgroup", colspan: headings.length }, group))),
      el("tr", {}, el("th", { scope: "col" }, "Model"), groups.map(([, headings]) => headings.map((h) => el("th", { scope: "col" }, h)))),
    ),
    el(
      "tbody",
      {},
      story.rows.map((r) =>
        el(
          "tr",
          {},
          el("th", { scope: "row" }, modelName(r.model)),
          el("td", {}, right(r.with_right, r.with_unsure)),
          el("td", {}, `${r.with_verified} of ${r.with_checked}`),
          el("td", {}, right(r.without_right, r.without_unsure)),
          el("td", {}, el("b", {}, `${r.without_verified} of ${r.without_with_verdict}`)),
        ),
      ),
    ),
  );
  return [
    el("h2", { tabindex: "-1", class: "view-heading lead" }, story.lead),
    el("p", { class: "clause-line" }, el("b", {}, `Clause ${clause.id}: `), `“${clause.text}” `, el("span", { class: "muted" }, "The model had no statute text in its prompt (closed book).")),
    el(
      "article",
      { class: "card story" },
      redline(ex.alignment),
      legend(),
      el("p", { class: "card-links" }, el("a", { href: `#/closed/${ex.clause}/${ex.model}` }, "This answer in the grid"), " · ", el("a", { href: "#/hand/misleads" }, `The ${hc.misleads} quotes that would mislead`)),
    ),
    el("h3", {}, story.table_title),
    tableBox(table, story.table_title),
    el("p", { class: "note" }, story.table_note),
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
    el("h3", {}, "Read before you rely on anything here"),
    caveatsList(),
    el(
      "p",
      { class: "explore" },
      "Explore all answers: ",
      Object.keys(HEADINGS).map((view, i) => [i ? " · " : "", el("a", { href: `#/${view}` }, HEADINGS[view])]),
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
  const unsure = answers.filter((a) => a.outcome === "unsure" || a.outcome === "no_verdict");
  const minor = (label) => failed.filter((a) => a.attempts[0].category === label).length;
  return { total: answers.length, right, failed, unsure, cosmetic: minor("cosmetic"), conventions: minor("passes with quoting conventions") };
}

// How many of a model's misquotes differ only cosmetically or by quoting conventions, which is
// not a wrong statement of the law.
function minorLine(c) {
  const parts = [];
  if (c.cosmetic) parts.push(`${c.cosmetic} cosmetic`);
  if (c.conventions) parts.push(`${c.conventions} only by quoting conventions`);
  return parts.length ? `of which ${andList(parts)}` : "none cosmetic";
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
        el("span", { class: "count" }, el("strong", {}, c.failed.length), " misquoted / ", el("strong", {}, c.right.length), " right verdicts"),
        el("span", { class: "count small" }, minorLine(c)),
        el("span", { class: "count small" }, `of ${c.total} clauses` + (c.unsure.length ? `; ${c.unsure.length} unsure, with no quote` : "")),
      );
    }),
  );
  return [
    heading(HEADINGS.exhibit),
    el(
      "p",
      { class: "intro" },
      "Without the statute text in the prompt (closed book), each model said whether a clause is void and quoted the section that decides it. ",
      "Code then checked the quote against the section it cited. These answers got the verdict right, ",
      "but the quote is not word for word in that section. Not word for word is not always wrong law: ",
      "some differ only cosmetically.",
    ),
    picker,
    el(
      "p",
      { class: "compare" },
      `With the statute in the prompt, ${pipe.verified} of ${pipe.checked} first answers with a verdict on the main set, from the ${DATA.models.length} models, had a quote that passed the same check.`,
    ),
    el("h3", {}, `${model.name}: ${chosen.failed.length} of its ${chosen.right.length} right verdicts had a quote that failed`),
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
      : el("p", { class: "empty" }, "Choose a cell to see the answer and its quote against the Act."),
  );
  const head = el("tr", {}, el("th", { scope: "col" }, "Clause"), models.map((m) => el("th", { scope: "col" }, modelName(m.name))));
  const rows = clauses.map((c) =>
    el(
      "tr",
      {},
      el("th", { scope: "row", class: "clause-cell" }, el("b", {}, c.id), el("span", { class: "snippet" }, c.text.length > 90 ? `${c.text.slice(0, 88)}…` : c.text)),
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
    heading(HEADINGS.closed),
    el(
      "p",
      { class: "intro" },
      `${METHODS.closedbook.note} Each cell shows the verdict (✓ right, ✗ wrong, ? unsure) and whether the quote passed the check. `,
      `The main test set of ${DATA.clauses.filter((c) => c.set === "main").length} clauses only.`,
    ),
    panel,
    tableBox(el("table", { class: "grid" }, el("thead", {}, head), el("tbody", {}, rows)), "Every closed-book answer"),
  ];
}

function setSize(name) {
  return DATA.clauses.filter((c) => c.set === name).length;
}

function clausesView(clauseId) {
  const clause = INDEX.clauses.get(clauseId) || DATA.clauses[0];
  const setName = clause.set;
  const select = el(
    "select",
    { id: "set-filter" },
    el("option", { value: "main", selected: setName === "main" }, `Main test set (${setSize("main")} clauses)`),
    el("option", { value: "stress", selected: setName === "stress" }, `Hard cases (${setSize("stress")} clauses)`),
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
    el("div", { class: "redline" }, el("p", { class: "redline-title" }, `The label's decisive words, s. ${clause.section}`), el("p", { class: "statute" }, "“", clause.quote, "”"), el("p", { class: "notice" }, DATA.notice)),
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
    heading(HEADINGS.clauses),
    el("div", { class: "set-filter" }, el("label", { for: "set-filter" }, "Clauses:"), select),
    el(
      "p",
      { class: "set-note" },
      setName === "main"
        ? `The ${setSize("main")} invented clauses the results are about, labelled and frozen before any model under test answered them.`
        : `${setSize("stress")} more invented clauses, kept because Claude Haiku 5.5's plain-question answer got them wrong, so that model's plain-question score on them is low by design. They are reported apart. (The repository calls them the stress set.)`,
    ),
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
  const columns = ["Model", "Clause", "Quote", "Code's category", "Hand category (if different)", "States a rule", "Changes what the Act says", "Misleads on its clause", "Note"];
  const table = el(
    "table",
    { class: "grid hand" },
    el("thead", {}, el("tr", {}, columns.map((h) => el("th", { scope: "col" }, h)))),
    el(
      "tbody",
      {},
      rows.map((a) => {
        const attempt = a.attempts[0];
        const h = attempt.hand_check;
        const quote = attempt.quote.length > 120 ? `${attempt.quote.slice(0, 118)}…` : attempt.quote;
        const misleads = h.meaning_changed === "yes" && h.misleads_here === "yes";
        return el(
          "tr",
          { class: misleads ? "misleads" : null },
          el("td", {}, modelName(INDEX.models.get(a.model).name)),
          el("th", { scope: "row" }, el("a", { href: `#/closed/${a.clause}/${a.model}` }, a.clause)),
          el("td", { class: "quote-cell" }, quote ? `“${quote}”` : "no quote"),
          el("td", {}, h.code_category),
          el("td", {}, h.category === h.code_category ? "" : h.category),
          el("td", {}, h.states_a_rule),
          el("td", {}, h.states_a_rule === "no" ? "—" : yesNo(h.meaning_changed)),
          el("td", {}, h.meaning_changed !== "yes" ? "—" : misleads ? el("span", { class: "badge" }, "Misleads") : yesNo(h.misleads_here)),
          el("td", { class: "note" }, h.note),
        );
      }),
    ),
  );
  return [
    heading(HEADINGS.hand),
    hand ? el("p", { class: "method" }, el("b", {}, "How it was done. "), hand.method, " ", el("a", { href: hand.record }, "The full record"), ", ", el("a", { href: hand.csv }, "the CSV"), ".") : null,
    hand ? el("p", { class: "method" }, hand.recheck) : null,
    el(
      "p",
      { class: "intro" },
      `For each of the ${hc.quotes} closed-book quotes that failed the word-for-word check: does it state a rule from the Act, does it change what the Act says, and would it mislead on the clause it was quoted for? Of the ${hc.quotes}, ${hc.rules} state a rule; ${hc.changes} of those change it, and ${hc.misleads} would mislead${hc.unsure ? ` (${hc.unsure} unsure)` : ""}; those rows are highlighted. Not legal advice, and not reviewed by a lawyer.`,
    ),
    el(
      "nav",
      { class: "model-picker", "aria-label": "Filter" },
      el("a", { href: "#/hand", "aria-current": filter !== "misleads" ? "true" : null }, el("span", { class: "name" }, `All ${hc.quotes}`)),
      el("a", { href: "#/hand/misleads", "aria-current": filter === "misleads" ? "true" : null }, el("span", { class: "name" }, `The ${hc.misleads} that would mislead`)),
    ),
    tableBox(table, "The hand-checked quotes"),
  ];
}

// The preview image's layout (#/card, not in the tabs): the opening line and redline alone, at
// 1200 by 630 pixels; `make og` saves it as og.png.
function cardView() {
  const story = DATA.story;
  return [
    el("p", { class: "card-eyebrow" }, "Right verdict, misquoted law · Lease Clause Checker"),
    el("h2", { tabindex: "-1", class: "view-heading lead" }, story.lead),
    redline(story.example.alignment),
    el("p", { class: "card-foot" }, el("del", {}, "Struck red"), ": in the Act, not in the quote · ", el("ins", {}, "underlined blue"), ": the quote's own · not legal advice"),
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
  else if (view === "card") nodes = cardView();
  else {
    current = "story";
    nodes = storyView();
  }
  main.replaceChildren(...nodes.flat().filter(Boolean));
  document.body.classList.toggle("card-mode", current === "card");
  markOverflow();
  for (const tab of document.querySelectorAll(".tabs a")) {
    if (tab.dataset.view === current) {
      tab.setAttribute("aria-current", "page");
      const strip = tab.parentElement; // on a phone the tabs scroll sideways: keep this one in sight
      strip.scrollLeft = Math.max(0, tab.offsetLeft - strip.offsetLeft - 16);
    } else tab.removeAttribute("aria-current");
  }
  document.title = `${VIEWS[current] || "Card"} · Right verdict, misquoted law`;
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
    caveatsList(),
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
      `: the ${DATA.models.length} models' answers on the main test set and the hard cases.`,
      DATA.dropped_models.length
        ? ` ${andList(DATA.dropped_models)}, run first and then left out of the comparison as a top-tier model, is not shown; its records are kept in the repository.`
        : "",
      " The run that chose the hard cases and the lease-check demo are not shown. Not legal advice.",
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
  window.addEventListener("resize", markOverflow);
}

start();
