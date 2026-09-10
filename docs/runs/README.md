# Run log

One file per day the automation runs: `YYYY-MM-DD.md`.

These are written straight to `master` without review (SPEC.md §7.6), so the
record lands whether or not anyone reviews a PR that day. That is the point —
if you have been away since Friday, this folder is where you find out what
happened without opening five branches.

## Read this before merging a stack

**Merge the oldest open PR first.** Every completed task ticks a box in
SPEC.md section 5, and when the automation stacks a task on top of an
unmerged one, the branches share that file. Merging out of order means
resolving conflicts by hand for no reason.

Each entry names its PR's base branch, so the order is always recoverable
from the log: a PR based on `master` merges whenever, a PR based on another
`claude/*` branch waits for that one.

## What is authoritative here, and what isn't

Nothing. This folder is a report, not state.

GitHub holds the truth about what is open, merged, or failing. A run decides
what to do by querying GitHub and then writes down what it did. If an entry
disagrees with GitHub — a PR listed open that is actually merged, say — the
entry is stale and GitHub is right. Never fix the discrepancy by editing
history here; just note it in the next day's entry.

## Format

```markdown
# YYYY-MM-DD

**Outcome:** one line — what a person needs to know if they read nothing else.

## Queue at start of run
Open automation PRs found on GitHub, oldest first, with CI state.

## What I did
Prose. The task taken, or why none was. Verification actually run, with
its result — not "tests pass" but "86 passed, 1 skipped".

## PR opened
Number, title, branch, and what it was branched from. "None" is a valid
entry and needs a reason.

## Judgment calls
Anything a reviewer would otherwise have to reverse-engineer from the diff.
Scope cut, an assumption made, a Backlog entry added.

## Needs you
What is blocked on a human, and what exactly you have to supply. "Nothing"
is a valid entry.
```

Keep entries short. This is a log, not a report card — if an entry needs more
than a screen, the detail belongs in the PR description.
