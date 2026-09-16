# Working with Claude on this repo

Read by every session — scheduled runs, interactive ones, web. Keep it short;
a long file buries the rules that matter.

Project rules live in `SPEC.md`: section 2 for principles, 5 for what not to
build, 6 for how the daily automation works. This file covers only how to talk.

## Answer format

Three sections, short bullets, nothing else:

- **WHAT'S GOING ON** — one or two sentences: the root cause or the mechanism.
- **WHAT YOU NEED TO DO** — the exact steps or the code.
- **WHAT TO KEEP IN MIND** — edge cases, debt, or a best practice specific to
  this task. Skip the section if there's nothing real to say.

## Tone

- Start with the diagnosis or the code. No "Sure, let's take a look."
- Say it with conviction. No hedging, no buzzwords, no restating the question.
- Tell me what I need to know and stop.

## Where detail goes

The terminal summary is not the record. `docs/runs/YYYY-MM-DD.md` is, and the
PR description is for anything a reviewer would otherwise dig out of the diff.
A summary that repeats either one is a wall of text nobody reads twice — three
bullets and a link.
