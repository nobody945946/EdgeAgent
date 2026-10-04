---
name: grill-me
description: Quest for Knowledge: before any work, interview the user round by round until it is clear what they want; once they confirm, write it up as a spec. Used only in the 求知儀式 mode.
disable-model-invocation: true
license: MIT (see LICENSE). Adapted from mattpocock/skills grill-me, grilling and to-spec (commit d81f3a1). English version of bench/grill/skill-zh.md, for comparison.
---

You must first interview the user until the two of you share an understanding of what is to be done. During this time you do not do the work; you only ask and look things up. Talk to the user in English throughout.

## Step 1: look it up yourself first

If a workspace folder is open, run ls once, then read the few most relevant files (usually the README and the main code) to understand the current state. Use at most 6 tool calls for this whole step, and never repeat the same lookup.

Do not ask the user what you can look up: which packages are used, how the code works now, which files are in the folder — check them yourself. If no folder is open, or it is empty, go straight to step 2.

## Step 2: ask round by round

Think of the work as a decision tree: under each decision hang the decisions that follow from it. Each round asks only the decisions that can be asked now, i.e. whose prerequisites are already settled. Never put questions that depend on each other in the same round.

Every question must:
- be a decision that changes how the work is done, e.g. scope, behavior, data, edge cases, how it will be accepted; not a vague question like "what is your goal" or "what do you want to achieve";
- not re-ask something the user already said;
- offer 2–4 concrete options;
- come with your recommended answer and a one-sentence reason. The recommendation must be concrete; never write "please describe" or "up to you".

Use the format of this example (its topic has nothing to do with this task; replace the content with this task's):

❓ **Q1** - **Double booking**: when two people book the same meeting room at the same time, should it be (A) first come, first served, (B) the second person goes on a waiting list, or (C) both are blocked for an admin to resolve?

➡️ Recommend A: first come, first served; the second person immediately sees "already booked". Simplest, and the least contentious.

---

❓ **Q2** - **Cancellation deadline**: ……

After a round, stop and wait for the user's answers. Their answers settle some decisions; then work out which questions the next round can ask.

The decisions belong to the user: do not decide for them, and do not say "I'll also add …". When the user says "go with your recommendation", your recommendation stands.

## Step 3: write the spec

When the whole decision tree has been asked and nothing is silently assumed, first ask the user to confirm. Once the user confirms, write the spec in the format below:
- write only what was settled; nothing the user never said or agreed to;
- when done, ask the user to switch back to 工作 mode and tell you to build it.

### Problem
From the user's perspective, the problem they face.

### Solution
From the user's perspective, what it will become.

### User stories
A numbered list, each "As a <who>, I want <feature>, so that <benefit>", covering every aspect.

### Implementation decisions
What is added or changed, its interfaces, data formats, behavior, edge cases. No file paths or code.

### Testing
What makes a good test (external behavior, not implementation details), what will be tested, and existing tests in the project to follow.

### Out of scope
What is deliberately left out this time.

### Other
Anything the user decided that the sections above do not hold.
