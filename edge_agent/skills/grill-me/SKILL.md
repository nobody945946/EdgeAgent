---
name: grill-me
description: A relentless interview that sharpens a plan or design into a spec the user confirms. Used only in the 求知儀式 mode, never on its own.
disable-model-invocation: true
license: MIT (see LICENSE). Adapted from mattpocock/skills (grill-me, grilling, to-spec) at commit d81f3a1.
---

Interview the user relentlessly until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the user's answers before the next round.

Format a round like so:

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>

---

❓ **Q2** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (the files in the workspace, the web), look it up yourself with your read-only tools (read, grep, find, ls, web search) before you ask the round; don't ask the user for anything you could look up yourself. The _decisions_ are the user's: put each to them and wait. Never answer a decision yourself.

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. Do not act on it until the user confirms you have reached a shared understanding. You cannot change files in this mode; do not start building.

## The spec

When the user confirms the shared understanding, write the spec in the conversation, in the user's language. It records only what was decided: anything it asserts that the user never said is a defect. Then tell the user to switch to 工作 mode and ask you to build it.

Use this template:

## Problem Statement

The problem that the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A numbered list of user stories covering every aspect of the feature, each in the format: As an <actor>, I want a <feature>, so that <benefit>.

## Implementation Decisions

The implementation decisions that were made: the modules that will be built or modified, their interfaces, technical clarifications from the user, architectural decisions, schema changes, API contracts, specific interactions. No file paths or code snippets; they go out of date quickly.

## Testing Decisions

What makes a good test here (test external behavior, not implementation details), which modules will be tested, and prior art for the tests in the codebase.

## Out of Scope

What this spec deliberately leaves out.

## Further Notes

Anything else the user decided that the sections above do not hold.
