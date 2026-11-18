"use strict";
// The explorer: renders data.json (written by `lcc site`), routed by the URL hash. Four numbered
// chapters and an appendix:
//   #/story                          1 Finding
//   #/pattern[/with][/<clause>/<model>]  2 Pattern: every answer as one square
//   #/misquotes[/<model>[/<clause>/<model>]]  3 Misquotes: the failed quotes, worst first
//   #/method[/caveats]               4 Method
//   #/clauses/<clause>               All answers (the appendix)
// and #/card (the preview image's layout). Older links still work: #/closed is #/pattern,
// #/exhibit and #/hand are #/misquotes. Nothing is decided here that the export did not already
// decide: verdicts, scores, quote checks, alignments and the review come from data.json, and
// every count shown is counted from it.

let DATA;
let METHODS;
const INDEX = { answers: new Map(), clauses: new Map(), models: new Map() };
const OUTCOMES = {
  correct: ["right", "right"],
  false_alarm: ["wrong: the label says the clause is allowed", "wrong"],
  missed_void: ["wrong: the label says the clause is void", "wrong"],
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
// Each view: its tab (with its chapter number), and the reader's question it answers.
const VIEWS = {
  story: { n: "1", tab: "Finding", question: "What's the finding?" },
  pattern: { n: "2", tab: "Pattern", question: "Is it a pattern, or one cherry-pick?" },
  misquotes: { n: "3", tab: "Misquotes", question: "How bad are the wrong quotes?" },
  method: { n: "4", tab: "Method", question: "How was it checked, and what can't it tell you?" },
  clauses: { n: null, tab: "All answers", question: "Every saved answer, clause by clause" },
};
const NARROW = window.matchMedia("(max-width: 700px)");

function methods() {
  return {
    baseline: { name: "Plain question", note: "Asked whether the clause is enforceable, with no statute text; the model answers in prose, and no quote is asked for. (The repository calls this the baseline.)" },
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

// "Flash" for "Gemini 3.8 Flash", "Opus" for "Claude Opus 5.5".
function shortModel(name) {
  return name.split(" ").filter((word) => !/^(Claude|Gemini|[\d.]+)$/.test(word)).join(" ");
}

function shortOf(key) {
  return shortModel(INDEX.models.get(key).name);
}

// "a", "a and b", "a, b and c".
function andList(items) {
  return items.length < 3 ? items.join(" and ") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

// "Four" for 4 at the start of a sentence; figures from 11 up.
function numberWord(n) {
  const words = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
  return words[n] || String(n);
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

// ---- Answers, counted ---------------------------------------------------------------------------

function mainAnswers(method) {
  return DATA.answers.filter((a) => a.set === "main" && a.method === method);
}

const isRight = (a) => a.outcome === "correct";
const isUnsure = (a) => a.outcome === "unsure" || a.outcome === "no_verdict";

// One answer as one mark: "pass" (word for word), "early" (word for word, but may stop early),
// "fail" (not word for word), "mislead" (not word for word, and the review found it would mislead
// on its clause) or "unsure" (no verdict, so no quote). Verdicts are final answers; quotes are
// first tries.
function quoteState(answer) {
  if (isUnsure(answer)) return "unsure";
  const attempt = answer.attempts[0];
  if (attempt.check === "verified") return attempt.stops_early ? "early" : "pass";
  return tierOf(attempt.hand_check) === "mislead" ? "mislead" : "fail";
}

const failedState = (state) => state === "fail" || state === "mislead";

// ---- One vocabulary -----------------------------------------------------------------------------

// The code's quote categories (`quotediff.LABELS`), in plain words.
function codeTerms() {
  return {
    cosmetic: "across the stretch of the Act it lines up with, it differs only in articles, punctuation, capitals, singular or plural, or paragraph markers. The code doesn't count the Act's words before or after that stretch.",
    "passes with quoting conventions": "word for word once the usual marks of a quotation are read as marks: an ellipsis for a cut, cosmetic changes in square brackets, a changed first capital.",
    "content words differ": "it lines up with the Act, but a word that carries meaning is changed, added or dropped.",
    "wrong section cited": "its words come from another section than the one it cites.",
    "not in the Act": "no stretch of it lines up with the Act: paraphrased or invented.",
    "too short": `under ${DATA.settings.min_quote_words} words: too short to pass the check or to state a rule.`,
    "heading only": "found only in a section's heading, not in the Act's operative text.",
    "drops a negation": "its words are in the Act, but only right after a “no”, “not”, “neither” or “nor” that it leaves out.",
    "no quote": "the answer gave none.",
  };
}

const MAY_STOP_EARLY = "it ends before a list item, or mid-phrase, with more of the subsection after it, so it may leave out the words that decide the clause";

// The review's four tiers (a fifth, unsure, only if some quote has it), worst first.
function tiers() {
  return {
    mislead: { name: "Would mislead on its clause", short: "would mislead on this clause", def: `States a rule, changes what the Act says, and would mislead on the clause it was quoted for: ${DATA.story.base_rate.means}.` },
    unsure: { name: "Changes the rule; unsure whether it would mislead", short: "changes the rule; unsure whether it would mislead here", def: "States a rule and changes what the Act says; the review could not decide whether it would mislead on this clause." },
    changes: { name: "Changes the rule, but would not mislead here", short: "changes the rule, but would not mislead here", def: "States a rule and changes what the Act says, but not in a way that would mislead on this clause." },
    same: { name: "Says what the Act says", short: "says what the Act says", def: "Not word for word, but it states the Act's rule without changing it." },
    norule: { name: "States no rule from the Act", short: "states no rule from the Act", def: "" },
  };
}
const TIER_ORDER = ["mislead", "unsure", "changes", "same", "norule"];

// `tierOf`, `reviewRows` and `reviewCounts` are in counts.js, loaded before this file.

// "code: cosmetic · review: would mislead on this clause", with the review's own category when
// it differs from the code's.
function codeAndReview(hand) {
  const review = tiers()[tierOf(hand)].short;
  return [
    el("span", { class: "who-said" }, "code: "),
    hand.code_category,
    " · ",
    el("span", { class: "who-said" }, "review: "),
    hand.category === hand.code_category ? review : `${hand.category}; ${review}`,
  ];
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
    gapLine(alignment, attempt),
    el("p", { class: "redline-title" }, title),
    el("p", { class: "statute" }, renderRuns(tokens, (i) => tokens[i][2])),
    el("p", { class: "notice" }, legendShort(), " ", DATA.notice),
  );
}

// Where the code lined a reviewed quote up with another provision than the review names (a known
// gap, listed in the export), one line says so above the redline, which still shows.
function gapLine(alignment, attempt) {
  const gap = attempt && attempt.hand_check && attempt.hand_check.realigned;
  if (!gap) return null;
  return el(
    "p",
    { class: "check-line warning gap-line" },
    `The code lined this quote up with s.\u00a0${alignment.section}${alignment.subsection}; the review reads it as s.\u00a0${gap.review}. Known gap: issue `,
    el("a", { href: gap.issue_url }, `#${gap.issue}`),
    ".",
  );
}

// The one legend for every redline on the site.
function legendShort() {
  return [el("del", {}, "Struck red"), ": in the Act, not in the quote · ", el("ins", {}, "underlined blue"), ": the quote's own words · the rest is in both."];
}

// ---- One answer -------------------------------------------------------------------------------

function checkText(attempt) {
  const s = attempt.section;
  const where = s ? `s. ${s}` : "the cited section";
  switch (attempt.check) {
    case "verified":
      return attempt.stops_early ? `The quote is word for word in ${where}, but it may stop early: ${MAY_STOP_EARLY}.` : `The quote is word for word in ${where}.`;
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
    ),
  );
  const line = checkText(attempt) + alignmentText(attempt);
  if (line) parts.push(el("p", { class: attempt.stops_early ? "check-line warning" : "check-line" }, line));
  if (attempt.found_in && attempt.found_in.length) {
    const list = attempt.found_in.map((n) => `s. ${n}`).join(", ");
    parts.push(el("p", { class: "check-line" }, `These words are word for word in ${list}, not in the section cited.`));
  }
  if (attempt.hand_check) parts.push(reviewNote(attempt.hand_check));
  else if (attempt.category) parts.push(el("p", { class: "check-line" }, el("span", { class: "who-said" }, "code: "), attempt.category));
  if (attempt.quote && attempt.check) parts.push(quoteBlock(attempt, clause));
  if (attempt.reason) parts.push(el("details", { class: "reason" }, el("summary", {}, "The model's reason"), el("p", {}, attempt.reason)));
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

// The review of a failed closed-book quote: how the code and the review sorted it, the note, and
// who made the label.
function reviewNote(hand) {
  return el(
    "div",
    { class: tierOf(hand) === "mislead" ? "review-note" : "review-note quiet" },
    el("p", { class: "review-line" }, codeAndReview(hand)),
    hand.note ? el("p", {}, hand.note) : null,
    el("small", {}, "Review label: drafted by Claude, reviewed blind by Claude Opus, approved by the author; not reviewed by a lawyer. Not legal advice."),
  );
}

// The review's written rules, beside its record in the repository.
function rulesLink(hand) {
  return `${hand.record.replace("/tree/", "/blob/")}/rules.md`;
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
      title = i === 0 ? (attempt.readable ? "First answer (failed the check)" : "First answer (not readable)") : "Asked again";
    }
    card.append(attemptView(attempt, clause, title));
  });
  if (answer.gave_up) card.append(el("p", { class: "check-line" }, "Both answers failed the check, so the answer counts as unsure."));
  return card;
}

// ---- Shared parts of a view -------------------------------------------------------------------

const CHAPTERS = Object.values(VIEWS).filter((v) => v.n).length;

function eyebrow(view) {
  const v = VIEWS[view];
  return v.n ? `${v.n} of ${CHAPTERS} · ${v.question}` : `Appendix · ${v.question}`;
}

// A view's opening: its place in the sequence, a sentence headline and one paragraph.
function viewTop(view, headline, dek, aside) {
  return el(
    "section",
    { class: aside ? "view-top split" : "view-top" },
    el("div", {}, el("p", { class: "eyebrow" }, eyebrow(view)), el("h1", { class: "view-heading", tabindex: "-1" }, headline), dek ? el("p", { class: "dek" }, dek) : null),
    aside,
  );
}

// The card that ends a view: the next question, and its answer before the click.
function nextCard(view, answer, href = `#/${view}`) {
  const v = VIEWS[view];
  const kicker = v.n ? `Next · ${v.n} of ${CHAPTERS}` : "Appendix";
  return el(
    "a",
    { class: "next", href },
    el("span", {}, el("span", { class: "k" }, kicker), el("span", { class: "q" }, v.n ? v.question : `${v.tab}: ${v.question.toLowerCase()}`), el("span", { class: "a" }, answer)),
    el("span", { class: "arr", "aria-hidden": "true" }, "→"),
  );
}

// The caveats in full: on Finding and Method, and at the foot of every other page; the header
// has them in one line.
const CAVEATS = [
  ["Checks wording, not legal meaning.", "A quote that passes is word for word in the section it cites; that says nothing about whether the reasoning is right."],
  ["Word for word is not complete.", "A quote can pass and still stop before the list item or condition that decides the clause; such quotes are flagged."],
  ["Not an official version.", "The Act's text is a dated copy from Ontario e-Laws."],
  ["Not legal advice.", "These are model outputs scored against test labels, and they may be wrong. For a question about a tenancy, contact a lawyer, a community legal clinic or the Landlord and Tenant Board."],
];

function caveatsList() {
  return el("ol", { class: "caveats" }, CAVEATS.map(([head, text]) => el("li", {}, el("div", {}, el("strong", {}, head), " ", text))));
}

function markOverflow() {
  for (const strip of document.querySelectorAll(".chapters")) {
    strip.classList.toggle("overflows", strip.scrollWidth > strip.clientWidth + 1);
    strip.classList.toggle("scrolled", strip.scrollLeft > 4);
  }
}

// How the saved answers were asked for, from the settings read off the saved calls; `brief` is
// the short form for the strip on Finding.
function runSettings(brief = false) {
  const runs = DATA.runs;
  const models = DATA.models.map((m) => ({ ...m, ...runs.models[m.key] }));
  const parts = [];
  if (models.every((m) => m.temperature === null)) parts.push(brief ? "no temperature set" : "no temperature set: each provider's default sampling (Claude Opus 5.5 and Haiku 5.5 reject a custom temperature, and these Gemini models ignore one)");
  else parts.push(andList(models.map((m) => `${m.name} at temperature ${m.temperature === null ? "default" : m.temperature}`)));
  const efforts = new Map();
  for (const m of models.filter((m) => m.effort)) efforts.set(m.effort, [...(efforts.get(m.effort) || []), m.name]);
  for (const [effort, names] of efforts) parts.push(brief ? `effort ${effort} for ${andList(names.map(shortModel))}` : `${andList(names)} at effort ${effort}`);
  if (models.every((m) => m.thinking === null)) parts.push(brief ? "default thinking" : "every model at its provider's default thinking");
  const caps = [...new Set(models.map((m) => m.max_output_tokens))];
  if (caps.length === 1 && caps[0]) parts.push(brief ? `${caps[0].toLocaleString("en-CA")} output tokens at most` : `up to ${caps[0].toLocaleString("en-CA")} output tokens, thinking included`);
  return parts.join("; ");
}

// The short account of how the results were made, near the top of Finding.
function madeStrip() {
  const hc = DATA.story.hand_check;
  const main = DATA.clauses.filter((c) => c.set === "main").length;
  const stress = DATA.clauses.filter((c) => c.set === "stress").length;
  const once = DATA.runs.answers_per_question === 1 ? "One answer each, not averaged over repeated runs" : `Up to ${DATA.runs.answers_per_question} answers each`;
  const items = [
    ["Clauses", `${main} invented lease clauses, each labelled void or allowed and frozen before any model under test answered. The author reviewed every label by hand, with LLM assistance. ${numberWord(stress)} hard cases, chosen later from Claude Haiku 5.5's plain-question mistakes, are reported apart.`],
    ["Models", `${DATA.models.length} models × ${Object.keys(METHODS).length} ways of asking: a plain question, with the statute in the prompt, and closed book.${DATA.dropped_models.length ? ` ${andList(DATA.dropped_models)} was run first and dropped as a top-tier model; its records are kept.` : ""} No result on this page includes the hard cases.`],
    ["Runs", `${once}. From the saved calls: ${runSettings(true)}.`],
    ["Quote check", `Each quote checked word for word by code against the section it cites, in a copy of the Act from Ontario e-Laws retrieved ${DATA.act.retrieved}.`],
    ["Review", `The ${hc.quotes} closed-book quotes that failed were sorted by a review: drafted by Claude, reviewed by Claude Opus, approved by the author. Not reviewed by a lawyer.${DATA.hand_check ? ` ${DATA.hand_check.recheck}` : ""}`],
  ];
  return el(
    "section",
    { class: "made", "aria-labelledby": "made-title" },
    el("h2", { class: "made-title", id: "made-title" }, "How this was made"),
    el("ol", { class: "made-steps" }, items.map(([head, text]) => el("li", {}, el("b", {}, head), el("span", {}, text)))),
    el("p", { class: "made-more" }, el("a", { href: "#/method" }, "The method in full")),
  );
}

// ---- Exhibits: a quote shown finished, with its edits to replay ----------------------------------

// The export's edits (`exhibits.replay` in Python) turn a copy of the Act's subsection into the
// model's quote: each drops the Act's words, adds the quote's own, or both. Each exhibit opens on
// the finished quote, with the words it leaves out marked in the Act; the replay plays the edits
// only when the reader asks. The page decides nothing about what changed.
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
const STOP = Symbol("stop");
let PLAYERS = [];
let ACTIVE = null;
let OBSERVERS = [];

// The text of tokens [text, before], with the space or line break before each but the first
// when `first`.
function tokenNodes(tokens, first, unstruck = false) {
  const out = [];
  tokens.forEach(([text, before], i) => {
    if (!(first && i === 0)) {
      // A dropped stretch's opening space folds with it but is not struck through.
      if (before === "\n") out.push(el("br"));
      else if (before) out.push(unstruck && i === 0 ? el("span", { class: "sp" }, " ") : " ");
    }
    out.push(text);
  });
  return out;
}

// An edit's words cut to their first five and last three if long, for the status line.
function shortened(said) {
  return said.replace(/‘([^’]*)’/g, (whole, words) => {
    const list = words.split(" ");
    return list.length > 10 ? `‘${list.slice(0, 5).join(" ")} … ${list.slice(-3).join(" ")}’` : whole;
  });
}

function tokenText(tokens, first) {
  return tokens.map(([text, before], i) => (first && i === 0 ? "" : before ? " " : "") + text).join("");
}

class Player {
  constructor(ex) {
    this.ex = ex;
    this.edits = ex.replay.edits.map((edit) => ({ ...edit, lost: [] }));
    this.who = shortModel(INDEX.models.get(ex.model).name);
    this.pos = this.edits.length;
    this.run = 0;
    this.animating = null;
    const alignment = ex.alignment;
    this.act = DATA.act.sections[alignment.section].subsections[alignment.subsection];
    this.actBox = this.buildAct();
    this.quoteBox = this.buildQuote();
  }

  // The Act box: every token its own span, so an edit can mark the words it drops. The marks
  // stay on: the Act box always shows what the quote leaves out.
  buildAct() {
    const [start, end] = this.ex.replay.act;
    const tag = this.ex.replay.tag;
    const owner = new Map();
    this.edits.forEach((edit, k) => {
      if (edit.drop) for (let i = edit.drop[0]; i < edit.drop[1]; i++) owner.set(i, k);
    });
    const p = el("p", { class: "statute" });
    let span = null;
    for (let i = start; i < end; i++) {
      const [text, before] = this.act[i];
      const tagged = tag && i >= tag[0] && i < tag[1];
      const k = owner.has(i) ? owner.get(i) : -1;
      // A token joins the span before it if the same edit drops both, so a mark is unbroken.
      const joins = span && span.dataset.edit === String(k) && k >= 0 && before !== "\n" && span.dataset.tagged === String(Boolean(tagged));
      if (!joins) {
        if (i > start) p.append(before === "\n" ? el("br") : before ? " " : "");
        if (tag && i === tag[0]) p.append(el("span", { class: "item-tag" }, this.ex.replay.tag_note));
        span = el("span", { class: k >= 0 ? "tok lost" : "tok", "data-edit": String(k), "data-tagged": String(Boolean(tagged)) });
        if (tagged) span.classList.add("tagged");
        p.append(span);
        if (k >= 0) {
          this.edits[k].lost.push(span);
          span.append(sr("[left out of the quote: "));
        }
        span.append(text);
        if (k >= 0) span.append(sr("]"));
      } else span.insertBefore(document.createTextNode((before ? " " : "") + text), span.lastChild);
    }
    return p;
  }

  // The quote box: the Act's words, with each edit's dropped words and (empty) added words.
  buildQuote() {
    const quote = this.ex.alignment.quote;
    const p = el("p", { class: "statute" });
    this.ex.replay.box.forEach(([kind, a, b], j) => {
      if (kind === "same") {
        p.append(...tokenNodes(this.act.slice(a, b), j === 0));
        return;
      }
      const edit = this.edits[a];
      edit.del = el("del", {}, edit.drop ? tokenNodes(this.act.slice(edit.drop[0], edit.drop[1]), j === 0, true) : []);
      edit.ins = el("ins");
      const added = edit.add ? tokenText(quote.slice(edit.add[0], edit.add[1]), edit.add[0] === 0) : "";
      edit.full = added.trimStart();
      // The space before added words is not underlined; it shows only with them.
      edit.space = el("span", { hidden: true }, added === edit.full ? "" : " ");
      p.append(edit.del, edit.space, edit.ins);
    });
    return p;
  }

  attach(article) {
    this.figure = this.quoteBox.closest(".quote-box");
    this.caption = this.figure.querySelector("figcaption b");
    this.finished = this.caption.textContent;
    this.status = article.querySelector(".replay-status");
    this.nextButton = article.querySelector(".next-change");
    this.watchButton = article.querySelector(".watch");
    this.watchButton.addEventListener("click", () => this.play());
    this.nextButton.addEventListener("click", () => this.next());
    article.addEventListener("focusin", () => (ACTIVE = this));
    article.addEventListener("pointerdown", () => (ACTIVE = this));
    this.render(this.edits.length);
  }

  setEdit(k, done) {
    const edit = this.edits[k];
    edit.del.className = done ? "gone" : "";
    edit.ins.textContent = done ? edit.full : "";
    edit.space.hidden = !done;
    edit.ins.classList.remove("typing");
    for (const span of edit.lost) span.classList.remove("now");
  }

  say(k) {
    const n = this.edits.length;
    if (k >= n) {
      this.status.textContent = `The quote as given. ${plural(n, "edit turns", "edits turn")} the Act's words into it; red in the Act marks the words it leaves out.`;
      return;
    }
    if (k < 0) {
      this.status.textContent = `The Act's words, before the ${plural(n, "edit", "edits")}.`;
      return;
    }
    const said = this.edits[k].said;
    this.status.replaceChildren(el("span", { "aria-hidden": "true" }, `Edit ${k + 1} of ${n}: ${shortened(said)}`), sr(`Edit ${k + 1} of ${n}: ${said}`));
  }

  render(pos) {
    this.pos = pos;
    this.animating = null;
    this.edits.forEach((_, k) => this.setEdit(k, k < pos));
    const n = this.edits.length;
    // While the edits play, the box holds the Act's words on their way to the quote, and says so.
    const replaying = pos < n;
    this.figure.classList.toggle("replaying", replaying);
    this.caption.textContent = replaying ? `The Act's words, edited toward ${this.who}'s quote` : this.finished;
    this.say(pos >= n ? n : pos - 1);
  }

  wait(ms, run) {
    return new Promise((resolve, reject) => setTimeout(() => (run === this.run ? resolve() : reject(STOP)), ms));
  }

  async animate(k, run) {
    const edit = this.edits[k];
    this.animating = k;
    this.say(k);
    for (const span of edit.lost) span.classList.add("now");
    if (edit.drop) {
      edit.del.className = "striking";
      await this.wait(800, run);
      edit.del.className = "striking fading";
      await this.wait(350, run);
      const before = this.quoteBox.offsetHeight;
      edit.del.className = "gone";
      const after = this.quoteBox.offsetHeight;
      if (before !== after) {
        this.quoteBox.animate([{ height: `${before}px` }, { height: `${after}px` }], { duration: 400, easing: "ease-out" });
        await this.wait(400, run);
      }
    }
    if (edit.full) {
      edit.space.hidden = false;
      edit.ins.classList.add("typing");
      const step = Math.min(45, 900 / edit.full.length);
      for (let i = 1; i <= edit.full.length; i++) {
        edit.ins.textContent = edit.full.slice(0, i);
        await this.wait(step, run);
      }
      edit.ins.classList.remove("typing");
    }
    this.render(k + 1);
  }

  // From the Act's words to the quote, one edit at a time; without motion if the reader asks.
  async play() {
    const run = ++this.run;
    this.render(0);
    try {
      await this.wait(700, run);
      for (let k = 0; k < this.edits.length; k++) {
        if (REDUCED.matches) {
          this.render(k + 1);
          await this.wait(1400, run);
        } else {
          await this.animate(k, run);
          await this.wait(550, run);
        }
      }
    } catch (error) {
      if (error !== STOP) throw error;
    }
  }

  // One edit on (finishing the one in progress, if any); from the finished quote, the first.
  next() {
    const doing = this.animating;
    this.run++;
    if (doing !== null) return this.render(doing + 1);
    if (this.pos >= this.edits.length) this.render(0);
    if (REDUCED.matches) return this.render(this.pos + 1);
    const run = this.run;
    this.animate(this.pos, run).catch((error) => {
      if (error !== STOP) throw error;
    });
  }

  prev() {
    this.run++;
    this.render(Math.max(0, (this.animating !== null ? this.animating + 1 : this.pos) - 1));
  }

  stop() {
    this.run++;
  }
}

function exhibitArticle(ex, n, lead) {
  const clause = INDEX.clauses.get(ex.clause);
  const model = INDEX.models.get(ex.model);
  const who = shortModel(model.name);
  const alignment = ex.alignment;
  const where = `s. ${alignment.section}${alignment.subsection}`;
  const player = new Player(ex);
  // What follows the boxes. The lead keeps it in a block of its own, which a wide screen sets
  // under the clause, beside the boxes, so that the punchline is on the first screen.
  const after = [
    el("p", { class: "punchline" }, ex.punchline),
    ex.note ? el("p", { class: "exhibit-note" }, ex.note) : null,
    el("details", { class: "exhibit-reason" }, el("summary", {}, "The model's reason"), el("blockquote", {}, ex.reason)),
    el("p", { class: "more" }, el("a", { href: `#/pattern/${ex.clause}/${ex.model}` }, "This answer on the pattern grid"), el("a", { href: `#/misquotes/${ex.model}` }, `${model.name}'s other misquotes`)),
  ];
  const article = el(
    "article",
    { class: lead ? "exhibit lead" : "exhibit", "aria-labelledby": `exhibit-${n}` },
    el(
      "div",
      { class: "exhibit-text" },
      el("p", { class: "label" }, `Exhibit ${n} · `, el("b", {}, model.name), ` · clause ${clause.id} · closed book`),
      el("h2", { id: `exhibit-${n}`, tabindex: "-1" }, ex.label),
      el("blockquote", { class: "clause" }, `“${clause.text}”`),
      el("p", { class: "verdict-line" }, `${who}'s verdict: `, el("b", {}, ex.verdict), ",", el("span", { class: "tag ok" }, "right")),
    ),
    el(
      "div",
      { class: "exhibit-boxes" },
      // The buttons sit above the boxes, so they stay put while the quote box changes height.
      el(
        "div",
        { class: "replay-bar" },
        el("button", { type: "button", class: "watch" }, "Watch how the quote was changed"),
        el("button", { type: "button", class: "next-change", "aria-keyshortcuts": "ArrowRight" }, "Next change"),
        el("p", { class: "replay-status", "aria-live": "polite" }),
      ),
      el("figure", { class: "box act-box" }, el("figcaption", {}, el("b", {}, `The Act, ${where}`), el("span", { class: "heading" }, DATA.act.sections[alignment.section].heading)), player.actBox),
      el("figure", { class: "box quote quote-box" }, el("figcaption", {}, el("b", {}, `What ${who} quoted`), el("span", {}, "from memory")), player.quoteBox),
      ...(lead ? [] : after),
    ),
    lead ? el("div", { class: "exhibit-after" }, ...after) : null,
  );
  player.attach(article);
  PLAYERS.push(player);
  return { article, player };
}

// The arrow keys step the exhibit the reader last used, or else the one most in view.
function watchExhibits() {
  const ratios = new Map();
  const view = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) ratios.set(entry.target, entry.intersectionRatio);
    },
    { threshold: [0, 0.25, 0.5, 0.75, 1] },
  );
  for (const player of PLAYERS) view.observe(player.quoteBox);
  OBSERVERS = [view];
  activePlayer.ratios = ratios;
}

function activePlayer() {
  const ratios = activePlayer.ratios || new Map();
  if (ACTIVE && PLAYERS.includes(ACTIVE) && (ratios.get(ACTIVE.quoteBox) || 0) > 0) return ACTIVE;
  let best = null;
  for (const player of PLAYERS) {
    const ratio = ratios.get(player.quoteBox) || 0;
    if (ratio > 0 && (!best || ratio > (ratios.get(best.quoteBox) || 0))) best = player;
  }
  return best;
}

function onKey(event) {
  if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  const target = event.target;
  if (target instanceof Element && target.closest("input, select, textarea, [contenteditable], .matrix")) return;
  const player = activePlayer();
  if (!player) return;
  event.preventDefault();
  if (event.key === "ArrowRight") player.next();
  else player.prev();
}

function stopExhibits() {
  for (const player of PLAYERS) player.stop();
  for (const observer of OBSERVERS) observer.disconnect();
  PLAYERS = [];
  OBSERVERS = [];
  ACTIVE = null;
}

// ---- 1 Finding --------------------------------------------------------------------------------

function numbersTable(story) {
  const groups = story.column_groups;
  const short = groups.map(([group]) => group.split(" ")[0]);
  const label = (g, h) => `${short[g]}: ${groups[g][1][h].toLowerCase()}`;
  const cell = (count, of, unsure, g, h, cls) =>
    el(
      "td",
      { class: cls, "data-label": label(g, h) },
      el("span", { class: "n" }, `${count} of ${of}`),
      unsure ? el("span", { class: "unsure" }, `${unsure} unsure`) : null,
      g === 1 ? el("span", { class: "bar", "aria-hidden": "true" }, el("i", { style: `width:${of ? ((100 * count) / of).toFixed(1) : 0}%` })) : null,
    );
  const n = story.clauses;
  return el(
    "table",
    { class: "numbers" },
    el("caption", { class: "sr-only" }, story.table_title),
    el(
      "thead",
      {},
      el("tr", { class: "groups" }, el("td"), groups.map(([group, headings], g) => el("th", { scope: "colgroup", colspan: headings.length, class: g === 1 ? "closed" : null }, el("span", {}, group)))),
      el("tr", {}, el("th", { scope: "col" }, "Model"), groups.map(([, headings]) => headings.map((h) => el("th", { scope: "col" }, h)))),
    ),
    el(
      "tbody",
      {},
      story.rows.map((r) =>
        el(
          "tr",
          {},
          el("th", { scope: "row" }, r.model),
          cell(r.with_right, n, r.with_unsure, 0, 0, null),
          cell(r.with_verified, r.with_checked, 0, 0, 1, null),
          cell(r.without_right, n, r.without_unsure, 1, 0, "closed-col verdict-col"),
          cell(r.without_verified, r.without_with_verdict, 0, 1, 1, "closed-col key"),
        ),
      ),
    ),
  );
}

// The closed-book quotes the review found would mislead, by model: "Haiku 3, Opus 3, …".
function misleadingSplit() {
  const misleading = mainAnswers("closedbook").filter((a) => quoteState(a) === "mislead");
  return { misleading, split: DATA.models.map((m) => `${shortModel(m.name)} ${misleading.filter((a) => a.model === m.key).length}`).join(", ") };
}

function storyView() {
  const story = DATA.story;
  const hc = story.hand_check;
  const hand = DATA.hand_check;
  const pipe = DATA.pipeline_first_checks;
  const rate = story.base_rate;
  const sum = (key) => story.rows.reduce((total, row) => total + row[key], 0);
  const exhibits = story.exhibits.map((ex, i) => exhibitArticle(ex, i + 1, i === 0).article);
  const { misleading, split } = misleadingSplit();
  const closedCount = mainAnswers("closedbook").length;
  const pattern = patternCounts();
  return [
    el(
      "section",
      { class: "view-top finding-top" },
      el("p", { class: "eyebrow" }, eyebrow("story")),
      el("h1", { class: "view-heading", tabindex: "-1" }, "Right verdict, misquoted law"),
      el("p", { class: "dek" }, `${numberWord(DATA.models.length)} language models judged invented Ontario lease clauses and quoted the Residential Tenancies Act, 2006 to back each verdict. `, el("dfn", {}, "Closed book"), " means without the statute's text in the prompt: the model quotes from memory. Their saved answers, replayed: no model is called here."),
      el(
        "p",
        { class: "lesson" },
        el("b", {}, "The lesson: a right verdict doesn't vouch for its quote. "),
        `At least ${rate.misleading} of the ${rate.right} right closed-book verdicts came with a quote that `,
        el("dfn", {}, "would mislead"),
        `; with the statute in the prompt and a code check, ${pipe.verified} of ${pipe.checked} first quotes were word for word.`,
      ),
      el("p", { class: "base-rate" }, el("b", {}, "Would mislead: "), `${rate.means}. Only the ${rate.judged} closed-book quotes that failed the word-for-word check were judged; quotes that passed, and those given with the statute, were not reviewed for meaning.`),
    ),
    madeStrip(),
    el("section", { class: "exhibits", "aria-label": "Three answers: the Act, the quote, and what changed" }, exhibits),
    el(
      "p",
      { class: "exhibits-note" },
      `These are ${story.exhibits.length} of the ${misleading.length} closed-book quotes that the review found would mislead (${split}), chosen by the author; each came with a right verdict. `,
      el("a", { href: "#/misquotes" }, `All ${misleading.length}`),
      ". The edits are computed from the word-by-word difference between the Act's subsection and the quote: they show what differs, not how the model wrote it. In the Act, ",
      el("span", { class: "lost-key" }, "red"),
      " marks the words the quote leaves out; in the quote, ",
      el("ins", {}, "blue"),
      ` words are its own. ${DATA.notice}`,
    ),
    el(
      "section",
      { class: "band", "aria-labelledby": "numbers-title" },
      el(
        "div",
        { class: "numbers-head" },
        el("div", {}, el("p", { class: "label" }, "The numbers"), el("h2", { class: "section-title", id: "numbers-title" }, story.table_title)),
        el(
          "div",
          { class: "contrast" },
          el("p", { class: "fig quiet" }, el("span", { class: "v" }, `${sum("with_verified")} `, el("small", {}, `of ${sum("with_checked")}`)), el("span", { class: "k" }, "quotes word for word with the statute in the prompt (first try)")),
          el("p", { class: "fig" }, el("span", { class: "v" }, `${sum("without_verified")} `, el("small", {}, `of ${sum("without_with_verdict")}`)), el("span", { class: "k" }, "quotes word for word without it (closed book)")),
        ),
      ),
      numbersTable(story),
      el("p", { class: "table-note" }, story.table_note),
      el("p", { class: "see-all" }, el("a", { href: "#/pattern" }, `See all ${closedCount} closed-book answers at once →`)),
      el(
        "div",
        { class: "funnel-wrap" },
        el(
          "div",
          {},
          el("h3", { class: "sub-title" }, `The ${hc.quotes} closed-book quotes that failed, ${story.failed_from}`),
          el("p", { class: "method" }, "For each one: does it state a rule from the Act, does it change what the Act says, and would it mislead on the clause it was quoted for?"),
        ),
        el(
          "div",
          {},
          el(
            "ol",
            { class: "funnel" },
            el("li", {}, el("b", {}, hc.quotes), el("span", {}, "quotes failed the word-for-word check")),
            el("li", {}, el("b", {}, hc.rules), el("span", {}, "state a real provision of the Act")),
            el("li", {}, el("b", {}, hc.changes), el("span", {}, "of those change the rule")),
            el("li", { class: "hit" }, el("b", {}, hc.misleads), el("span", {}, `of those ${hc.changes} would mislead about the clause they were quoted for${hc.unsure ? ` (${hc.unsure} unsure)` : ""}`)),
          ),
          el("p", { class: "method" }, hand ? [el("strong", {}, hand.labels), " ", hand.recheck, " "] : null, el("a", { href: "#/misquotes" }, "See the review"), " · ", el("a", { href: "#/method" }, "How it was done")),
        ),
      ),
    ),
    el(
      "section",
      { class: "caveats-sec", id: "caveats", tabindex: "-1", "aria-labelledby": "cv-title" },
      el("p", { class: "label" }, "Notes to the reader"),
      el("h2", { class: "section-title", id: "cv-title" }, "Read before you rely on anything here"),
      caveatsList(),
    ),
    nextCard("pattern", `Not one cherry-pick: ${pattern.rightFailed.length} of the ${pattern.right} right closed-book verdicts came with a quote that was not word for word.`),
  ];
}

// ---- 2 Pattern ----------------------------------------------------------------------------------

let PATTERN_SORT = "fails";

const MODES = { without: "closedbook", with: "pipeline" };

function patternAnswer(mode, model, clause) {
  return INDEX.answers.get(answerKey(MODES[mode], model, clause));
}

function patternCounts() {
  const cb = mainAnswers("closedbook");
  const rightFailed = cb.filter((a) => isRight(a) && failedState(quoteState(a)));
  return { right: cb.filter(isRight).length, rightFailed };
}

function perModel(mode, model, ids) {
  const answers = ids.map((c) => patternAnswer(mode, model, c)).filter(Boolean);
  const states = answers.map(quoteState);
  return {
    withVerdict: states.filter((s) => s !== "unsure").length,
    wfw: states.filter((s) => s === "pass" || s === "early").length,
  };
}

function describeAnswer(a) {
  const state = quoteState(a);
  if (state === "unsure") return "unsure, so no quote";
  const clause = INDEX.clauses.get(a.clause);
  const verdict = isRight(a) ? `right verdict (${a.verdict})` : `wrong verdict (${a.verdict}; the label is ${clause.label})`;
  const quote = {
    pass: "quote word for word",
    early: "quote word for word, but it may stop early",
    fail: a.attempts[0].check === "no_quote" ? "no quote" : "quote not word for word",
    mislead: "quote not word for word, and the review found it would mislead on this clause",
  }[state];
  return `${verdict}; ${quote}`;
}

function showTip(target, a) {
  const tip = document.getElementById("tip");
  const clause = INDEX.clauses.get(a.clause);
  tip.replaceChildren(el("b", {}, `${a.clause} · ${shortOf(a.model)} · ${sectionName(clause.section)}`), `“${clause.text.length > 110 ? `${clause.text.slice(0, 108)}…` : clause.text}”`, el("br"), el("span", { class: "tip-state" }, `${describeAnswer(a)}.`));
  tip.hidden = false;
  const box = target.getBoundingClientRect();
  const width = Math.min(300, window.innerWidth - 24);
  tip.style.width = `${width}px`;
  tip.style.left = `${Math.max(12, Math.min(box.left + box.width / 2 - width / 2, window.innerWidth - width - 12))}px`;
  tip.style.top = `${box.top > 160 ? box.top - tip.offsetHeight - 8 : box.bottom + 8}px`;
}

function hideTip() {
  const tip = document.getElementById("tip");
  if (tip) tip.hidden = true;
}

function patternView(parts) {
  const mode = parts[0] === "with" ? "with" : "without";
  const [clauseId, modelKey] = mode === "with" ? parts.slice(1) : parts;
  const ids = DATA.clauses.filter((c) => c.set === "main").map((c) => c.id);
  const all = (m) => DATA.models.flatMap((model) => ids.map((c) => patternAnswer(m, model.key, c))).filter(Boolean);
  const answers = all(mode);
  const { right, rightFailed } = patternCounts();
  const clausesHit = new Set(rightFailed.map((a) => a.clause)).size;
  const modelsHit = new Set(rightFailed.map((a) => a.model)).size;
  const pipe = all("with").filter((a) => quoteState(a) !== "unsure");
  const pipeWfw = pipe.filter((a) => ["pass", "early"].includes(quoteState(a))).length;
  // Models by their share of closed-book quotes word for word, best first, in both modes.
  const order = DATA.models
    .map((m) => m.key)
    .sort((x, y) => {
      const a = perModel("without", x, ids);
      const b = perModel("without", y, ids);
      return b.wfw / b.withVerdict - a.wfw / a.withVerdict;
    });
  const failsAt = (c) => order.filter((m) => {
    const a = patternAnswer(mode, m, c);
    return a && failedState(quoteState(a));
  }).length;
  const columns = PATTERN_SORT === "id" ? ids : [...ids].sort((x, y) => failsAt(y) - failsAt(x) || x.localeCompare(y));
  // The chosen answer: from the address, or else Finding's first exhibit (closed book), or the
  // first failed quote (with the statute).
  const lead = DATA.story.exhibits[0];
  let chosen = clauseId && modelKey ? patternAnswer(mode, modelKey, clauseId) : mode === "without" ? patternAnswer(mode, lead.model, lead.clause) : null;
  if (!chosen) {
    const want = mode === "without" ? "mislead" : "fail";
    for (const m of order) {
      chosen = columns.map((c) => patternAnswer(mode, m, c)).find((a) => a && quoteState(a) === want);
      if (chosen) break;
    }
  }
  const prefix = mode === "with" ? "#/pattern/with" : "#/pattern";
  const kinds = [
    ["pass", "Right verdict, quote word for word", (a) => isRight(a) && quoteState(a) === "pass"],
    ["early", ["Word for word, but ", el("dfn", {}, "may stop early"), `: ${MAY_STOP_EARLY}`], (a) => isRight(a) && quoteState(a) === "early"],
    ["fail", "Right verdict, quote not word for word", (a) => isRight(a) && quoteState(a) === "fail"],
    ["mislead", "… and the review found it would mislead on this clause", (a) => isRight(a) && quoteState(a) === "mislead"],
    ["unsure", "Unsure, so no quote", (a) => quoteState(a) === "unsure"],
    ["wrong", "Wrong verdict (the square still shows the quote check)", (a) => !isRight(a) && quoteState(a) !== "unsure"],
  ];
  const legendList = el(
    "ul",
    { class: "pattern-legend" },
    kinds.map(([kind, text, test]) => {
      const n = answers.filter(test).length;
      const swatch = kind === "wrong" ? el("span", { class: "cell s-pass", "aria-hidden": "true" }, el("span", { class: "x" }, "✗")) : el("span", { class: `cell s-${kind}`, "aria-hidden": "true" }, kind === "unsure" ? "?" : "");
      return n ? el("li", {}, swatch, el("span", { class: "c" }, n), el("span", {}, text)) : null;
    }),
  );
  const cellFor = (a, row, col) => {
    const state = quoteState(a);
    const wrong = !isRight(a) && state !== "unsure";
    const current = chosen === a;
    const link = el(
      "a",
      { class: `cell s-${state}`, href: `${prefix}/${a.clause}/${a.model}`, "aria-current": current ? "true" : null, "aria-label": `${INDEX.models.get(a.model).name}, ${a.clause}: ${describeAnswer(a)}`, tabindex: current ? "0" : "-1", "data-row": row, "data-col": col },
      state === "unsure" ? "?" : wrong ? el("span", { class: "x", "aria-hidden": "true" }, "✗") : null,
    );
    link.addEventListener("mouseenter", () => showTip(link, a));
    link.addEventListener("focus", () => showTip(link, a));
    link.addEventListener("mouseleave", hideTip);
    link.addEventListener("blur", hideTip);
    return link;
  };
  const matrix = el("div", { class: "matrix", role: "group", "aria-label": "Every answer: models by clauses. Use the arrow keys to move between squares." });
  const kids = [];
  if (!NARROW.matches) {
    matrix.style.gridTemplateColumns = `200px repeat(${columns.length}, minmax(0, 1fr))`;
    kids.push(el("div", { class: "corner" }, "Quotes failed"), columns.map((c) => {
      const n = failsAt(c);
      return el("div", { class: "count", title: `${n} of ${order.length} quotes failed` }, n ? el("i", { style: `height:${(16 * n) / order.length}px` }) : "");
    }));
    kids.push(el("div", { class: "corner" }, "Clause"), columns.map((c) => el("div", { class: failsAt(c) === order.length ? "colhead col-hot" : "colhead" }, c.slice(1))));
    order.forEach((m, r) => {
      const p = perModel(mode, m, ids);
      kids.push(el("div", { class: "mlabel" }, el("b", {}, INDEX.models.get(m).name), el("span", {}, `${p.wfw} of ${p.withVerdict} word for word`)));
      kids.push(columns.map((c, k) => cellFor(patternAnswer(mode, m, c), r, k)));
    });
  } else {
    matrix.style.gridTemplateColumns = `64px repeat(${order.length}, minmax(0, 1fr))`;
    kids.push(el("div", { class: "corner" }, "Clause"), order.map((m) => {
      const p = perModel(mode, m, ids);
      return el("div", { class: "colhead" }, shortOf(m), el("small", {}, `${p.wfw} of ${p.withVerdict}`));
    }));
    columns.forEach((c, r) => {
      const n = failsAt(c);
      kids.push(el("div", { class: "rowhead" }, c, n ? el("i", { style: `width:${3 * n}px`, title: `${n} failed` }) : null));
      kids.push(order.map((m, k) => cellFor(patternAnswer(mode, m, c), r, k)));
    });
  }
  matrix.append(...kids.flat());
  if (!chosen) matrix.querySelector(".cell")?.setAttribute("tabindex", "0");
  matrix.addEventListener("keydown", (event) => moveInMatrix(event, matrix));
  const axis = NARROW.matches
    ? `Rows: clauses${PATTERN_SORT === "fails" ? ", the most failed quotes first (red tick: how many)" : " in order"}. Columns: models, with quotes word for word out of answers with a verdict. Tap a square for the answer.`
    : `Columns: clauses ${ids[0]}–${ids[ids.length - 1]}${PATTERN_SORT === "fails" ? ", the ones with the most failed quotes first" : " in order"}. Rows: models, the most word-for-word first. Hover or focus a square for the clause; choose it for the answer.`;
  const sortButton = (key, text) => {
    const b = el("button", { type: "button", "aria-pressed": String(PATTERN_SORT === key), "data-sort": key }, text);
    b.addEventListener("click", () => {
      PATTERN_SORT = key;
      route();
      document.querySelector(`[data-sort="${key}"]`).focus();
    });
    return b;
  };
  return [
    viewTop(
      "pattern",
      ["Not one cherry-pick: ", el("em", {}, `${rightFailed.length} of the ${right}`), " right closed-book verdicts came with a quote that was not word for word."],
      mode === "without"
        ? [
            `${modelsHit === DATA.models.length ? "Every model" : `${numberWord(modelsHit)} of the ${DATA.models.length} models`} did it, and ${clausesHit} of the ${ids.length} clauses had at least one. Each square is one answer given `,
            el("b", {}, "without the statute in the prompt"),
            ": the model judged the clause and quoted the Act from memory, and code checked the quote. With the statute in front of them, ",
            el("b", {}, `${pipeWfw} of ${pipe.length}`),
            " first quotes were word for word. Switch it on to compare.",
          ]
        : [
            "Each square is now one answer given ",
            el("b", {}, "with the statute in the prompt"),
            `: the text of the ${DATA.settings.rule_sections} sections the test set is about. `,
            el("b", {}, `${pipeWfw} of ${pipe.length}`),
            " first quotes were word for word; they were not reviewed for meaning. The headline counts the closed-book answers: switch the statute off to compare.",
          ],
    ),
    el(
      "div",
      { class: "ctl-row" },
      el("div", { class: "ctl" }, el("span", { class: "ctl-label", id: "mode-label" }, "Statute in the prompt"), el("div", { class: "seg", role: "group", "aria-labelledby": "mode-label" }, el("a", { href: chosen ? `#/pattern/${chosen.clause}/${chosen.model}` : "#/pattern", "aria-current": mode === "without" ? "true" : null }, "Without it"), el("a", { href: chosen ? `#/pattern/with/${chosen.clause}/${chosen.model}` : "#/pattern/with", "aria-current": mode === "with" ? "true" : null }, "With it"))),
      el("div", { class: "ctl" }, el("span", { class: "ctl-label", id: "sort-label" }, "Clauses"), el("div", { class: "seg", role: "group", "aria-labelledby": "sort-label" }, sortButton("fails", "Most misquoted first"), sortButton("id", `${ids[0]} to ${ids[ids.length - 1]}`))),
    ),
    el("p", { class: "mode-note" }, "Showing: ", el("b", {}, mode === "with" ? "with the statute in the prompt" : "without the statute (closed book)"), mode === "with" ? ` (the text of the ${DATA.settings.rule_sections} sections the test set is about). Verdicts are final answers; quotes are first tries.` : ". Quotes from memory, checked once, no second try."),
    el("figure", { class: "chart" }, legendList, matrix, el("p", { class: "axis-note" }, axis), patternNotes(mode, ids, answers, order)),
    chosen ? patternCard(chosen, mode) : null,
    nextCard("misquotes", `Of the ${DATA.story.hand_check.quotes} quotes that failed, ${DATA.story.hand_check.changes} change what the Act says; ${DATA.story.hand_check.misleads} of those would mislead about the clause.`),
  ];
}

// Arrow keys move between squares; the square in reach of Tab is the one last chosen.
function moveInMatrix(event, matrix) {
  const moves = { ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 0], ArrowUp: [-1, 0] };
  const move = moves[event.key];
  const from = event.target.closest(".cell");
  if (!move || !from) return;
  const row = Number(from.dataset.row) + move[0];
  const col = Number(from.dataset.col) + move[1];
  const to = matrix.querySelector(`.cell[data-row="${row}"][data-col="${col}"]`);
  if (!to) return;
  event.preventDefault();
  from.setAttribute("tabindex", "-1");
  to.setAttribute("tabindex", "0");
  to.focus();
}

function patternNotes(mode, ids, answers, order) {
  const failsWith = (c) => order.filter((m) => failedState(quoteState(patternAnswer(mode, m, c)))).length;
  if (mode === "with") {
    const withVerdict = answers.filter((a) => quoteState(a) !== "unsure");
    const failed = withVerdict.filter((a) => failedState(quoteState(a)));
    return el(
      "div",
      { class: "notes" },
      el("span", {}, el("b", {}, `${failed.length} of ${withVerdict.length}`), ` first quotes failed${failed.length ? ` (${failed.map((a) => `${shortOf(a.model)} ${a.clause}`).join(", ")})` : ""}; a failed quote is asked again once.`),
      el("span", {}, el("b", {}, answers.filter((a) => quoteState(a) === "early").length), " pass but may stop early: word for word is not complete."),
      el("span", {}, "These answers were not reviewed for meaning."),
    );
  }
  const counts = new Map(ids.map((c) => [c, failsWith(c)]));
  const all = ids.filter((c) => counts.get(c) === order.length);
  const most = Math.max(...counts.values());
  const next = all.length ? ids.filter((c) => counts.get(c) === order.length - 1) : [];
  const clean = ids.filter((c) => !order.some((m) => {
    const a = patternAnswer(mode, m, c);
    return isRight(a) && failedState(quoteState(a));
  }));
  return el(
    "div",
    { class: "notes" },
    all.length ? el("span", {}, el("b", {}, all.join(", ")), all.length === 1 ? ` (${sectionName(INDEX.clauses.get(all[0]).section)})` : "", `: all ${order.length} quotes failed.`) : el("span", {}, `At most ${most} of ${order.length} quotes failed on one clause.`),
    next.length ? el("span", {}, el("b", {}, next.join(", ")), `: ${order.length - 1} of ${order.length}.`) : null,
    el("span", {}, `${clean.length} clauses had no right verdict with a failed quote.`),
    el("span", {}, `Only the ${DATA.story.hand_check.quotes} quotes that failed were reviewed for meaning: a grey square says the words match, not that the quote is right (caveats 01 and 02).`),
  );
}

function patternCard(a, mode) {
  const clause = INDEX.clauses.get(a.clause);
  const attempt = a.attempts[0];
  const state = quoteState(a);
  const hand = attempt.hand_check;
  const lead =
    state === "unsure"
      ? "Unsure, so there is no quote to check."
      : `${isRight(a) ? "Right verdict" : "Wrong verdict"}, ${["pass", "early"].includes(state) ? "quote word for word" : attempt.check === "no_quote" ? "no quote" : "quote not word for word"}${state === "mislead" ? ", and it would mislead on this clause." : state === "early" ? ", but it may stop early." : "."}`;
  const ledger = el(
    "dl",
    { class: "ledger" },
    el("div", {}, el("dt", {}, "Verdict"), el("dd", {}, state === "unsure" ? "unsure " : [el("b", {}, a.verdict), " "], state === "unsure" ? el("span", { class: "tag mute" }, "unsure") : isRight(a) ? el("span", { class: "tag ok" }, "right") : el("span", { class: "tag bad" }, "wrong"), el("span", { class: "muted" }, ` · label: ${clause.label}, ${sectionName(clause.section)}`))),
    state === "unsure" || !attempt.section ? null : el("div", {}, el("dt", {}, "Cites"), el("dd", {}, sectionLink(attempt.section))),
    state === "unsure" ? null : el("div", {}, el("dt", {}, "Quote check"), el("dd", {}, checkText(attempt) + alignmentText(attempt), a.attempts.length > 1 ? " Asked again once; the second answer is below." : "")),
    hand ? el("div", {}, el("dt", {}, "Sorted"), el("dd", {}, codeAndReview(hand), codeTerms()[hand.code_category] ? el("span", { class: "term-def" }, `The code's “${hand.code_category}”: ${codeTerms()[hand.code_category]}`) : null)) : null,
  );
  return el(
    "section",
    { class: "answer", id: "answer", "aria-labelledby": "answer-h" },
    el("p", { class: "label" }, `${INDEX.models.get(a.model).name} · clause ${a.clause} · ${mode === "with" ? "with the statute" : "closed book"}`),
    el(
      "div",
      { class: "answer-grid" },
      el(
        "div",
        {},
        el("h2", { tabindex: "-1", id: "answer-h" }, lead),
        el("blockquote", { class: "clause-q" }, `“${clause.text}”`),
        ledger,
        attempt.reason ? el("details", { class: "reason" }, el("summary", {}, "The model's reason"), el("p", {}, attempt.reason)) : null,
      ),
      el(
        "div",
        {},
        state === "unsure" || !attempt.quote ? el("p", { class: "muted" }, "No quote was given.") : quoteBlock(attempt, clause),
        hand ? reviewNote(hand) : null,
        a.attempts.length > 1 ? el("details", { class: "reason" }, el("summary", {}, "The second answer"), attemptView(a.attempts[1], clause)) : null,
        el("p", { class: "links" }, el("a", { href: `#/clauses/${a.clause}` }, `Every answer for ${a.clause}`), hand ? el("a", { href: `#/misquotes/all/${a.clause}/${a.model}` }, "This quote on Misquotes") : null),
      ),
    ),
  );
}

// ---- 3 Misquotes --------------------------------------------------------------------------------

let OPEN_ROWS = new Set();
let OPEN_TIERS = new Set(["mislead"]);

function rowKey(a) {
  return `${a.model}|${a.clause}`;
}

function reviewDetail(a) {
  const clause = INDEX.clauses.get(a.clause);
  const attempt = a.attempts[0];
  const hand = attempt.hand_check;
  return el(
    "div",
    { class: "detail" },
    el(
      "div",
      {},
      el("blockquote", { class: "clause-q" }, `“${clause.text}”`),
      el(
        "dl",
        { class: "ledger" },
        el("div", {}, el("dt", {}, "Verdict"), el("dd", {}, el("b", {}, a.verdict), " ", isRight(a) ? el("span", { class: "tag ok" }, "right") : el("span", { class: "tag bad" }, "wrong"), el("span", { class: "muted" }, ` · label: ${clause.label}, ${sectionName(clause.section)}`))),
        attempt.section ? el("div", {}, el("dt", {}, "Cites"), el("dd", {}, sectionLink(attempt.section))) : null,
        el("div", {}, el("dt", {}, "Quote check"), el("dd", {}, checkText(attempt) + alignmentText(attempt))),
      ),
      attempt.reason ? el("details", { class: "reason" }, el("summary", {}, "The model's reason"), el("p", {}, attempt.reason)) : null,
      el("p", { class: "links" }, el("a", { href: `#/pattern/${a.clause}/${a.model}` }, "This answer on the pattern grid"), el("a", { href: `#/clauses/${a.clause}` }, `Every answer for ${a.clause}`)),
    ),
    el("div", {}, attempt.quote ? quoteBlock(attempt, clause) : el("p", { class: "muted" }, "The answer gave no quote.")),
  );
}

// One failed quote as one line, opening to the clause, the checks and the quote against the Act.
// `showSorted` adds the code's and the review's sorting to the line (where rows are not grouped by
// tier).
function reviewRow(a, showSorted) {
  const key = rowKey(a);
  const clause = INDEX.clauses.get(a.clause);
  const hand = a.attempts[0].hand_check;
  const open = OPEN_ROWS.has(key);
  const li = el("li", { class: "row", "data-open": String(open), id: `row-${a.model}-${a.clause}` });
  const button = el(
    "button",
    { type: "button", "aria-expanded": String(open) },
    el("span", { class: "chev", "aria-hidden": "true" }, "›"),
    el("span", { class: "who" }, shortOf(a.model)),
    el("span", { class: "where" }, el("b", {}, a.clause), el("span", {}, sectionName(clause.section))),
    el("span", { class: "gist" }, el("span", { class: "sorted" }, codeAndReview(hand)), hand.note || (a.attempts[0].check === "no_quote" ? "The answer gave no quote." : "")),
    el("span", { class: "tagcol" }, isRight(a) ? null : el("span", { class: "tag bad" }, "wrong verdict")),
  );
  if (!showSorted) button.querySelector(".sorted").remove();
  button.addEventListener("click", () => {
    const now = !OPEN_ROWS.has(key);
    if (now) OPEN_ROWS.add(key);
    else OPEN_ROWS.delete(key);
    li.dataset.open = String(now);
    button.setAttribute("aria-expanded", String(now));
    const detail = li.querySelector(".detail");
    if (now && !detail) li.append(reviewDetail(a));
    else if (!now && detail) detail.remove();
  });
  li.append(button);
  if (open) li.append(reviewDetail(a));
  return li;
}

function misquotesView(parts) {
  const rows = reviewRows(DATA);
  const models = DATA.models.map((m) => m.key);
  const filter = models.includes(parts[0]) ? parts[0] : "all";
  const [openClause, openModel] = parts.slice(1);
  const hc = DATA.story.hand_check;
  const hand = DATA.hand_check;
  const TIERS = tiers();
  const tierRows = (t, list = rows) => list.filter((a) => tierOf(a.attempts[0].hand_check) === t);
  const counts = reviewCounts(DATA);
  const opened = rows.find((a) => a.clause === openClause && a.model === openModel);
  if (opened) {
    OPEN_ROWS.add(rowKey(opened));
    OPEN_TIERS.add(tierOf(opened.attempts[0].hand_check));
  }
  const shown = rows.filter((a) => filter === "all" || a.model === filter);
  const noRule = [...new Set(tierRows("norule").map((a) => a.attempts[0].hand_check.category))];
  TIERS.norule.def = `Its words state no rule from the Act's operative text: ${andList(noRule)}.`;
  const usedTiers = TIER_ORDER.filter((t) => t !== "unsure" || tierRows("unsure").length);
  const prov = hand
    ? el(
        "aside",
        { class: "prov", "aria-label": "How these labels were made" },
        el("p", { class: "label" }, "How these labels were made"),
        el("p", {}, el("strong", {}, hand.labels)),
        el("p", {}, hand.recheck, " ", el("a", { href: "#review-in-full" }, "The review in full"), " · ", el("a", { href: hand.record }, "the full record"), " · ", el("a", { href: hand.csv }, "the CSV"), "."),
      )
    : null;
  const units = el(
    "div",
    { class: "units" },
    usedTiers.map((t) => {
      const list = tierRows(t);
      const inFilter = list.filter((a) => filter === "all" || a.model === filter).length;
      const group = el(
        "a",
        { class: `ugroup t-${t}`, href: `#tier-${t}`, style: `flex:${Math.max(list.length, 1)} 1 0` },
        el("span", { class: "sq", style: `grid-template-columns:repeat(${Math.max(list.length, 1)}, minmax(0,1fr))`, "aria-hidden": "true" }, list.map((a) => el("i", { class: filter === "all" || a.model === filter ? null : "dim", title: `${shortOf(a.model)} ${a.clause}` }, isRight(a) ? "" : "✗"))),
        el("span", { class: "cap" }, el("b", {}, filter === "all" ? counts.tiers[t] : `${inFilter} of ${counts.tiers[t]}`), TIERS[t].name[0].toLowerCase() + TIERS[t].name.slice(1)),
      );
      group.addEventListener("click", (event) => {
        event.preventDefault();
        OPEN_TIERS.add(t);
        const tier = document.getElementById(`tier-${t}`);
        tier.open = true;
        tier.scrollIntoView({ block: "start" });
        tier.querySelector("summary").focus({ preventScroll: true });
      });
      return group;
    }),
  );
  const cosmeticMisleads = tierRows("mislead").filter((a) => a.attempts[0].hand_check.code_category === "cosmetic");
  const terms = codeTerms();
  const usedTerms = Object.keys(terms).filter((t) => rows.some((a) => [a.attempts[0].hand_check.code_category, a.attempts[0].hand_check.category].includes(t)));
  const chips = el(
    "nav",
    { class: "filters", "aria-label": "Filter by model" },
    el("span", { class: "ctl-label" }, "Model"),
    el("a", { class: "chip", href: "#/misquotes", "aria-current": filter === "all" ? "true" : null }, `All ${rows.length}`),
    models.map((m) => el("a", { class: "chip", href: `#/misquotes/${m}`, "aria-current": filter === m ? "true" : null }, shortOf(m), el("span", { class: "k" }, counts.byModel[m]))),
  );
  const vocab = el(
    "section",
    { class: "vocab", "aria-labelledby": "vocab-title" },
    el("h2", { id: "vocab-title", class: "vocab-title" }, "How to read a row"),
    el("p", {}, "Each row is one failed quote: the model, the clause and the section that decides it, how it was sorted, and the review's note. Open a row for the quote against the Act, the clause and the checks. ", el("b", {}, "code:"), " how the code sorted the quote's wording; ", el("b", {}, "review:"), " what the review found it does to the rule, with the review's own category where it differs from the code's."),
    el("dl", {}, usedTerms.map((term) => el("div", {}, el("dt", {}, term), el("dd", {}, terms[term])))),
  );
  const tierBlocks = usedTiers.map((t) => {
    const list = tierRows(t, shown);
    const block = el(
      "details",
      { class: `tier t-${t}`, id: `tier-${t}`, open: OPEN_TIERS.has(t) },
      el(
        "summary",
        {},
        el("span", { class: "n" }, list.length),
        el("div", {}, el("h2", {}, TIERS[t].name), el("p", {}, TIERS[t].def)),
        el("span", { class: "tog", "aria-hidden": "true" }, el("span", { class: "closed" }, `Show ${list.length} ↓`), el("span", { class: "open" }, "Hide ↑")),
      ),
      list.length ? el("ul", { class: "rows" }, list.map((a) => reviewRow(a, true))) : el("p", { class: "muted" }, "None from this model."),
    );
    block.addEventListener("toggle", () => (block.open ? OPEN_TIERS.add(t) : OPEN_TIERS.delete(t)));
    return block;
  });
  return [
    viewTop(
      "misquotes",
      [`Of the ${counts.quotes} quotes that failed, ${counts.changes} change what the Act says, and `, el("em", {}, `${counts.misleads} of those would mislead`), " about the clause they were quoted for."],
      [
        `Failing the word-for-word check is not the same as stating the law wrongly: some failed quotes are only reworded. So every closed-book quote that failed, ${DATA.story.failed_from}, was sorted by what it does to the rule, worst first. `,
        el("b", {}, `${rows.filter((a) => !isRight(a)).length} of the ${counts.quotes}`),
        " came with a wrong verdict; the rest with a right one.",
      ],
      prov,
    ),
    el(
      "figure",
      { class: "units-wrap" },
      units,
      el("figcaption", { class: "units-note" }, "One square per quote that failed: the red squares of the pattern grid, sorted. ", el("b", {}, "✗"), " marks a wrong verdict. ", `${counts.rules} of the ${counts.quotes} state a real provision of the Act; ${counts.changes} of those change the rule; ${counts.misleads} of those ${counts.changes} would mislead${counts.unsure ? ` (${counts.unsure} unsure)` : ""}.`),
    ),
    vocab,
    cosmeticMisleads.length
      ? el(
          "p",
          { class: "finding-callout" },
          el("b", {}, `${cosmeticMisleads.length} of the ${counts.misleads} that would mislead were sorted “cosmetic” by the code: `),
          andList(cosmeticMisleads.map((a) => `${shortOf(a.model)} ${a.clause}`)),
          ". The code compares a quote with the stretch of the Act it lines up with, so it doesn't count the Act's words left out before or after it; these two leave out the conditions that limit the rule. The code's sorting alone would have called them harmless.",
        )
      : null,
    chips,
    el("div", { class: "tiers" }, tierBlocks),
    hand
      ? el(
          "section",
          { class: "method-full", id: "review-in-full", "aria-labelledby": "review-full-title" },
          el("p", { class: "label" }, "The review, in full"),
          el("h2", { id: "review-full-title" }, "How the labels were assigned"),
          el("p", {}, "For each quote: does it state a rule from the Act, does it change what the Act says, and would it mislead on the clause it was quoted for?"),
          el("p", {}, hand.method),
          el("p", {}, "Notes that cite a rule refer to ", el("a", { href: rulesLink(hand) }, "the written rules"), "."),
        )
      : null,
    nextCard("method", "Code checks the wording only. The review labels were drafted by Claude and reviewed blind; no lawyer reviewed them."),
  ];
}

// ---- 4 Method -----------------------------------------------------------------------------------

function methodView() {
  const hc = DATA.story.hand_check;
  const hand = DATA.hand_check;
  const n = DATA.clauses.filter((c) => c.set === "main").length;
  const terms = codeTerms();
  const card = (method, index) => {
    const answers = mainAnswers(method);
    const right = answers.filter(isRight).length;
    const unsure = answers.filter(isUnsure).length;
    const withVerdict = answers.filter((a) => !isUnsure(a));
    const quotes = method === "baseline" ? null : withVerdict.filter((a) => a.attempts[0].check === "verified").length;
    const byModel = DATA.models.map((m) => answers.filter((a) => a.model === m.key).length);
    return el(
      "li",
      { class: "method-card" },
      el("span", { class: "num" }, String(index + 1)),
      el("h3", {}, METHODS[method].name),
      el("p", {}, METHODS[method].note),
      el(
        "p",
        { class: "totals" },
        `Main set, ${DATA.models.length} models (${byModel[0]} answers each): verdicts right ${right} of ${answers.length}${unsure ? ` (${unsure} unsure)` : ""}`,
        quotes === null ? "; no quote asked for." : `; quotes word for word ${quotes} of ${withVerdict.length}${method === "pipeline" ? " (first try)" : ""}.`,
      ),
    );
  };
  const early = mainAnswers("closedbook").find((a) => isRight(a) && a.attempts[0].stops_early && a.attempts[0].alignment);
  const reviewList = el("ul", { class: "rows" }, reviewRows(DATA).map((a) => reviewRow(a, true)));
  return [
    viewTop(
      "method",
      `Code checked the wording of every quote; only the ${hc.quotes} closed-book quotes that failed were reviewed for meaning, and none of it was reviewed by a lawyer.`,
      "How the answers were asked for, how each quote was checked, and how the ones that failed were reviewed. Every number on this site is counted from the saved answers in the repository.",
    ),
    el("h2", { class: "section-head" }, "Three ways of asking"),
    el("ol", { class: "method-cards" }, ["baseline", "pipeline", "closedbook"].map(card)),
    el("p", { class: "method-text" }, `The test set: ${n} invented lease clauses, each labelled void or allowed under the Act, with the section that decides it, and frozen before any model under test answered. The author reviewed every label by hand, with LLM assistance. ${DATA.runs.answers_per_question === 1 ? "One saved answer per clause, model and way of asking; nothing is averaged over repeated runs." : ""} With the statute, a quote that failed the check was asked for again once: ${plural(mainAnswers("pipeline").filter((a) => a.attempts.length > 1).length, "answer", "answers")} on the main set. From the saved calls: ${runSettings()}.`),
    el("h2", { class: "section-head" }, "The word-for-word check"),
    el(
      "p",
      { class: "method-text" },
      `A quote passes if it appears word for word in the text of the section it cites and is at least ${DATA.settings.min_quote_words} words long. Spacing, and curly against straight quotation marks and apostrophes, are ignored; capitals, punctuation and every word must match. A quote found only in a heading fails, and so does one whose words are in the Act only right after a “no”, “not”, “neither” or “nor” it leaves out. The check is code, not a model: it gives the same answer every time. A redline lines a quote up with the subsection it matches best, using runs of at least ${DATA.settings.min_run} words in a row.`,
    ),
    el("p", { class: "method-text" }, el("b", {}, "Word for word is not complete. "), `A quote can pass and still stop early: ${MAY_STOP_EARLY}. Code flags these as `, el("dfn", {}, "may stop early"), ". It is a rough rule: it flags a quote that ends inside a list after the item that applies, so it over-counts, and it misses a quote that ends on a complete statement before a condition that is not a list item."),
    early ? el("div", { class: "method-example" }, el("p", { class: "label" }, `For example: ${INDEX.models.get(early.model).name} on ${early.clause}, closed book`), redline(early.attempts[0].alignment, early.attempts[0])) : null,
    el("h2", { class: "section-head" }, "Words used here"),
    el(
      "dl",
      { class: "glossary" },
      el("div", {}, el("dt", {}, "closed book"), el("dd", {}, "without the statute's text in the prompt: the model quotes from memory.")),
      el("div", {}, el("dt", {}, "word for word"), el("dd", {}, "passes the check above.")),
      el("div", {}, el("dt", {}, "may stop early"), el("dd", {}, `word for word, but ${MAY_STOP_EARLY}.`)),
      el("div", {}, el("dt", {}, "would mislead"), el("dd", {}, `${DATA.story.base_rate.means}.`)),
      el("p", { class: "glossary-head" }, "How the code sorts a quote that fails (its category):"),
      Object.entries(terms).map(([term, text]) => el("div", {}, el("dt", {}, term), el("dd", {}, text))),
    ),
    hand
      ? [
          el("h2", { class: "section-head", id: "review" }, "The review"),
          el("p", { class: "method-text" }, `Only the ${hc.quotes} closed-book quotes that failed the check were reviewed, with three questions: does it state a rule from the Act, does it change what the Act says, and would it mislead on the clause it was quoted for? Quotes that passed, including those that may stop early, were not reviewed, so the counts of misleading quotes are a minimum.`),
          el("p", { class: "method-text" }, hand.method, " Notes that cite a rule refer to ", el("a", { href: rulesLink(hand) }, "the written rules"), "."),
          el("p", { class: "method-text" }, el("strong", {}, hand.labels)),
          el("p", { class: "method-text" }, hand.recheck, " ", el("a", { href: hand.record }, "The full record"), " · ", el("a", { href: hand.csv }, "the CSV"), "."),
          el("details", { class: "review-table" }, el("summary", {}, `The review, row by row (${hc.quotes} rows)`), reviewList),
        ]
      : null,
    el("section", { class: "caveats-sec", id: "caveats", tabindex: "-1", "aria-labelledby": "cv-title" }, el("p", { class: "label" }, "Notes to the reader"), el("h2", { class: "section-title", id: "cv-title" }, "Read before you rely on anything here"), caveatsList()),
    nextCard("clauses", `Look up any answer: all ${DATA.answers.length}.`, "#/clauses"),
    el("p", { class: "back" }, el("a", { href: "#/story" }, "Back to 1 · Finding")),
  ];
}

// ---- Appendix: All answers ----------------------------------------------------------------------

function setSize(name) {
  return DATA.clauses.filter((c) => c.set === name).length;
}

// A clause's closed-book answers as four small squares, in the pattern grid's marks.
function strip(clauseId) {
  const answers = DATA.models.map((m) => INDEX.answers.get(answerKey("closedbook", m.key, clauseId))).filter(Boolean);
  if (!answers.length) return null;
  return el("span", { class: "strip", "aria-hidden": "true" }, answers.map((a) => el("i", { class: `cell s-${quoteState(a)}` })));
}

function clausesView(parts) {
  const clause = INDEX.clauses.get(parts[0]) || DATA.clauses[0];
  const setName = clause.set;
  const inSet = DATA.clauses.filter((c) => c.set === setName);
  const at = inSet.indexOf(clause);
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
  const picker = el("select", { id: "clause-pick", "aria-label": "Clause" }, inSet.map((c) => el("option", { value: c.id, selected: c === clause }, `${c.id}: ${c.text.length > 60 ? `${c.text.slice(0, 58)}…` : c.text}`)));
  picker.addEventListener("change", () => (location.hash = `#/clauses/${picker.value}`));
  const step = (to, text) => (to ? el("a", { class: "step", href: `#/clauses/${to.id}` }, text) : el("span", { class: "step off" }, text));
  const list = el(
    "ul",
    { class: "clause-list" },
    inSet.map((c) => el("li", {}, el("a", { href: `#/clauses/${c.id}`, "aria-current": c.id === clause.id ? "true" : null }, strip(c.id), el("b", {}, c.id), c.text.length > 70 ? `${c.text.slice(0, 68)}…` : c.text))),
  );
  const accepted = clause.also_accept.length ? `; also accepted: ${clause.also_accept.map((n) => `s. ${n}`).join(", ")}` : "";
  const detail = el(
    "section",
    { "aria-label": `Answers for ${clause.id}` },
    el("div", { class: "clause-steps" }, picker, el("div", { class: "steps" }, step(inSet[at - 1], "← Previous"), step(inSet[at + 1], "Next →"))),
    el("h2", { tabindex: "-1", id: "clause-heading", class: "clause-heading" }, `${clause.id}: ${clause.label}`),
    el("p", { class: "clause-text" }, clause.text),
    el("p", { class: "meta" }, el("span", {}, "Decided by ", sectionLink(clause.section), accepted)),
    el("div", { class: "redline" }, el("p", { class: "redline-title" }, `The label's decisive words, s. ${clause.section}`), el("p", { class: "statute" }, "“", clause.quote, "”"), el("p", { class: "notice" }, DATA.notice)),
    el("p", { class: "reason-line" }, el("b", {}, "Why: "), clause.rationale),
    el("h3", {}, "Answers"),
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
          let quote = final && final.check ? (final.check === "verified" ? "quote word for word" : "quote not word for word") : "";
          if (quote && a.attempts.length > 1) quote += " on the second try";
          const hand = a.attempts && a.attempts[0].hand_check;
          return el(
            "details",
            { class: "answer-row" },
            el(
              "summary",
              {},
              el("span", { class: "model" }, INDEX.models.get(a.model).name),
              el("span", {}, outcomeView(a)),
              el("span", { class: "small" }, a.section ? `s. ${a.section}` : "no section"),
              quote ? el("span", { class: "small" }, quote) : null,
              hand && tierOf(hand) === "mislead" ? el("span", { class: "tag solid" }, "would mislead") : null,
            ),
            answerCard(a, { showClause: false, showModel: false }),
          );
        }),
      );
    }),
  );
  return [
    viewTop("clauses", `${DATA.answers.length} saved answers: ${DATA.clauses.length} clauses, ${Object.keys(METHODS).length} ways of asking, ${DATA.models.length} models.`, "A lookup: pick a clause to see every model's answer to it, each way of asking. The squares by each clause are its closed-book answers, in the pattern grid's marks."),
    el("div", { class: "set-filter" }, el("label", { for: "set-filter" }, "Clauses:"), select),
    el(
      "p",
      { class: "set-note" },
      setName === "main"
        ? `The ${setSize("main")} invented clauses the results are about, labelled and frozen before any model under test answered them. The author reviewed every label by hand, with LLM assistance.`
        : `${setSize("stress")} more invented clauses, kept because Claude Haiku 5.5's plain-question answer got them wrong, so that model's plain-question score on them is low by design. They are reported apart, and have no closed-book answers. (The repository calls them the stress set.)`,
    ),
    el("div", { class: "clause-browser" }, list, detail),
    nextCard("pattern", "Back to the pattern: every closed-book answer as one square.", "#/pattern"),
  ];
}

// ---- The preview image --------------------------------------------------------------------------

// The preview image's layout (#/card, not in the tabs): the first exhibit's quote against the
// Act as one redline, at 1200 by 630 pixels; `make og` saves it as og.png.
function cardView() {
  const ex = DATA.story.exhibits[0];
  const model = INDEX.models.get(ex.model);
  return [
    el("p", { class: "card-eyebrow" }, `Lease Clause Checker · ${model.name} · clause ${ex.clause} · closed book`),
    el("h2", { tabindex: "-1", class: "card-title view-heading" }, "Right verdict, misquoted law"),
    el("div", { class: "redline" }, el("p", { class: "redline-title" }, `Redline: s. ${ex.alignment.section}${ex.alignment.subsection} → the model's quote`), el("p", { class: "statute" }, (() => {
      const tokens = redlineTokens(ex.alignment);
      return renderRuns(tokens, (i) => tokens[i][2]);
    })()), el("p", { class: "notice" }, DATA.notice)),
    el("p", { class: "card-foot" }, ex.punchline, el("span", { class: "small" }, el("del", {}, "Struck red"), ": in the Act, not in the quote · ", el("ins", {}, "underlined blue"), ": the quote's own · not legal advice")),
  ];
}

// Shrinks the card's type, in steps, until the whole card fits its 630 pixels: a long subsection
// would otherwise run off the image. Measured once the card's three fonts have loaded.
const CARD_FONTS = ['500 20px "IBM Plex Mono"', '600 54px "Source Serif 4"', '400 18px "IBM Plex Sans"'];
function fitCard(main) {
  const padding = parseFloat(getComputedStyle(main).paddingTop) + parseFloat(getComputedStyle(main).paddingBottom);
  const room = main.clientHeight - padding;
  const used = () => main.lastElementChild.getBoundingClientRect().bottom - main.firstElementChild.getBoundingClientRect().top;
  let scale = 1;
  main.style.setProperty("--card-scale", scale);
  while (used() > room && scale > 0.5) {
    scale = Math.round((scale - 0.05) * 100) / 100;
    main.style.setProperty("--card-scale", scale);
  }
  if (used() > room) console.warn("The card does not fit 1200 by 630 pixels even at half size.");
}

// ---- Routing ----------------------------------------------------------------------------------

// The address as [view, ...parts], older addresses mapped to the views that replaced them.
function parseRoute() {
  const [view = "story", ...parts] = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (view === "closed") return ["pattern", parts];
  if (view === "exhibit") return ["misquotes", parts.length ? [parts[0]] : []];
  if (view === "hand") return ["misquotes", []];
  if (VIEWS[view] || view === "card") return [view, parts];
  return ["story", []];
}

function canonical(view, parts) {
  return `#/${[view, ...parts].join("/")}`;
}

function route(event) {
  const [view, parts] = parseRoute();
  if (location.hash && location.hash !== canonical(view, parts) && /^#\/(closed|exhibit|hand)\b/.test(location.hash)) {
    history.replaceState(null, "", canonical(view, parts));
  }
  const main = document.getElementById("main");
  // What the reader was on, to give it focus again if the new view has it (a filter or square).
  const active = document.activeElement;
  const was = active && main.contains(active) ? active.getAttribute("href") || active.id : null;
  const wasClass = active && main.contains(active) ? active.className : null;
  const scrollWas = window.scrollY;
  stopExhibits();
  hideTip();
  let nodes;
  if (view === "pattern") nodes = patternView(parts);
  else if (view === "misquotes") nodes = misquotesView(parts);
  else if (view === "method") nodes = methodView();
  else if (view === "clauses") nodes = clausesView(parts);
  else if (view === "card") nodes = cardView();
  else nodes = storyView();
  main.replaceChildren(...nodes.flat().filter(Boolean));
  document.body.classList.toggle("card-mode", view === "card");
  document.body.dataset.view = view;
  if (view === "story") watchExhibits();
  if (view === "card") {
    Promise.all(CARD_FONTS.map((font) => document.fonts.load(font)))
      .then(() => document.fonts.ready)
      .then(() => {
        if (document.body.dataset.view === "card") fitCard(main);
      });
  } else main.style.removeProperty("--card-scale");
  for (const tab of document.querySelectorAll(".tabs a")) {
    if (tab.dataset.view === view) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  markOverflow();
  // On a phone the tabs may scroll sideways: only then bring the current one into sight.
  const tabs = document.querySelector(".chapters");
  const tab = tabs.querySelector('[aria-current="page"]');
  if (tab && tabs.scrollWidth > tabs.clientWidth + 1) tabs.scrollLeft = Math.max(0, tab.offsetLeft - tabs.offsetLeft - 16);
  else tabs.scrollLeft = 0;
  document.title = `${VIEWS[view] ? VIEWS[view].tab : "Card"} · Right verdict, misquoted law`;
  if (view === "clauses") {
    const item = document.querySelector('.clause-list [aria-current="true"]');
    if (item) item.closest(".clause-list").scrollTop = item.offsetTop - 80;
  }
  const caveats = view === "method" && parts[0] === "caveats" ? document.getElementById("caveats") : null;
  const opened = view === "misquotes" && parts.length > 2 ? document.getElementById(`row-${parts[2]}-${parts[1]}`) : null;
  const chosen = view === "pattern" && parts.filter((x) => x !== "with").length >= 2 && NARROW.matches ? document.getElementById("answer") : null;
  // The first render leaves focus where the browser put it, unless the address points into the
  // view: the caveats, an opened row, or (on a phone) a chosen square's answer.
  if (!event && !caveats && !opened && !chosen) return;
  if (event) document.getElementById("status").textContent = VIEWS[view] ? VIEWS[view].tab : "";
  const matches = was ? [...main.querySelectorAll("[href], [id]")].filter((node) => node.getAttribute("href") === was || node.id === was) : [];
  const again = matches.find((node) => node.className === wasClass) || matches[0];
  const target =
    caveats ||
    (opened && opened.querySelector("button")) ||
    (view === "clauses" && parts[0] && !again && document.getElementById("clause-heading")) ||
    again ||
    main.querySelector(".view-heading, h2");
  target.focus({ preventScroll: true });
  if (again && view === "pattern") {
    // A square chosen: the grid stays where it was, and the answer comes up from below into
    // view (on a phone, to the top).
    window.scrollTo(0, scrollWas);
    const answer = document.getElementById("answer");
    if (answer && NARROW.matches) answer.scrollIntoView({ block: "start" });
    else if (answer && answer.getBoundingClientRect().top > window.innerHeight - 160) window.scrollBy(0, answer.getBoundingClientRect().top - window.innerHeight * 0.4);
    return;
  }
  if (chosen) {
    chosen.scrollIntoView({ block: "start" });
    return;
  }
  const box = target.getBoundingClientRect();
  const hidden = box.top < 0 || box.bottom > window.innerHeight;
  if (caveats || opened || (again && hidden)) target.scrollIntoView({ block: "start" });
  else if (!again) window.scrollTo(0, 0);
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
  document.body.append(el("div", { class: "tip", id: "tip", role: "tooltip", hidden: true }));
  footer();
  route();
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", markOverflow);
  NARROW.addEventListener("change", () => {
    if (document.body.dataset.view === "pattern") route();
  });
  document.addEventListener("keydown", onKey);
  document.querySelector(".chapters").addEventListener("scroll", markOverflow, { passive: true });
  // Links within a view ("#review-in-full", the skip link) scroll to their target; only "#/…"
  // addresses change the view.
  document.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    const link = event.target instanceof Element && event.target.closest('a[href^="#"]:not([href^="#/"])');
    if (!link || link.classList.contains("ugroup")) return;
    const target = document.getElementById(link.getAttribute("href").slice(1));
    if (!target) return;
    event.preventDefault();
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "start" });
  });
}

start();
