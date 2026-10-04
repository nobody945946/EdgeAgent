# Judge rubric: 求知儀式 (grill-me) interviews

Eval only. You judge blind: transcripts are under random keys; you do not know which model or prompt wrote them, and you must not guess.

Each item has:
- `idea`: what the user typed to start;
- `expected_language`: the language the interviewer should talk in;
- `already_stated`: what that idea already says;
- `hidden_intent`: the user's real wishes, decisions `{id, topic, answer}` that the interviewer could not see;
- `workspace_files`: the folder the user opened (path → content; a content in parentheses describes a binary or private file; empty means no files). The interviewer could look at these with read-only tools;
- `rounds`: each round's interviewer text, the tools it called (`looked_at`), and the user's reply.

## Per question

For every question the interviewer asks (each `❓` item, or a clearly separate question if it ignored the format), in every round, give:

- `round`: round number (1-based);
- `title`: a few words;
- `label`, exactly one of:
  - `key`: answering it settles at least one hidden decision (list them in `hits`);
  - `useful`: a real decision that changes what gets built, though not in the hidden list;
  - `already_answered`: the idea or an earlier user reply already settled it;
  - `should_look_up`: a fact the workspace files answer, or a technical detail the interviewer should just decide or recommend without bothering the user (e.g. which retry library, how to name a variable);
  - `filler`: vague, generic or not changing the design ("你的目標是什麼", "要支援哪些作業系統" when it is obvious, "還有什麼想法嗎", asking to restate);
  - `premature`: it depends on another question still open in the same round;
- `hits`: hidden decision ids it settles (empty unless `key`);
- `recommendation`: `good` (specific and sensible for this user), `weak` (vague, generic or a poor fit), or `missing`.

Count a question that covers several hidden decisions once, with all of them in `hits`. A laundry list of sub-questions under one `❓` is one question; label it by its main point.

## Per transcript

- `self_decided`: how many decisions the interviewer settled on its own without asking or offering ("我會順便加上…", silently assuming a choice in a later round);
- `stopped_rounds`: rounds where the interviewer produced nothing or no questions and no spec;
- `spec`: `none` if no spec was written; `faithful` if it was and contains only what the user decided or accepted; `invented` if it asserts things the user never said or accepted;
- `language_ok`: true if every round is in the item's `expected_language` (Traditional Chinese, never Simplified, or English);
- `note`: one sentence on the interview's quality.

## Output

A JSON object `{key: {"questions": [...], "self_decided": n, "stopped_rounds": n, "spec": "...", "language_ok": bool, "note": "..."}}` covering every key in your input. Judge every key by the same standard.
