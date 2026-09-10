# Run log

One file per day the automation runs: `YYYY-MM-DD.md`.

These go straight to `master` without review (SPEC.md §7.6), so the record
lands whether or not anyone reviews a PR that day. If you've been away since
Friday, this is where you find out what happened.

## Merging a stack

**Merge the oldest open PR first.** Every task ticks a box in SPEC.md section
5, so when the automation stacks a task on an unmerged one, both branches touch
that file. Out-of-order merges mean resolving conflicts for no reason.

Each entry names its PR's base branch, so the order is recoverable: a PR based
on `master` merges whenever, one based on another `claude/*` branch waits for
that branch.

## This folder is a report, not state

GitHub holds the truth about what's open, merged or failing. A run queries
GitHub, then writes down what it did. If an entry disagrees with GitHub, the
entry is stale — note it in the next day's entry rather than editing history
here.

## Format

```markdown
# YYYY-MM-DD

**Outcome:** one line — what to know if you read nothing else.

## Queue at start of run
Open automation PRs found on GitHub, oldest first, with CI state.

## What I did
The task taken, or why none was. Verification actually run, with its result —
not "tests pass" but "86 passed, 1 skipped".

## PR opened
Number, title, branch, and what it was branched from. "None" is valid and
needs a reason.

## Judgment calls
Anything a reviewer would otherwise reverse-engineer from the diff: a scope
cut, an assumption, a Backlog entry added.

## Needs you
What's blocked on a human and what exactly to supply. "Nothing" is valid.
```

Keep entries short. Detail belongs in the PR description.
