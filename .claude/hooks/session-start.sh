#!/bin/bash
# Stamp every commit made in this repo with the repo owner's identity.
#
# WHY: agent sessions run in a throwaway container whose global gitconfig is
# preset to "Claude <noreply@anthropic.com>". Git's author field is just that
# config value — it records nothing about who actually wrote a line, so an
# agent asked to commit work stamps its own name on it regardless. On this
# repo that had two consequences: the work didn't count toward the owner's
# GitHub contribution graph (the graph only counts commits whose author email
# is linked to the account), and the history misrepresented authorship of a
# project its owner is directing.
#
# Authorship of the commit and credit for the writing are different claims.
# The author field answers "whose work is this?" — the owner's. Where an agent
# did the typing, the commit message says so in a Co-Authored-By trailer.
#
# --local, so this only ever touches this repository's .git/config: running it
# on a real machine won't disturb anyone's global git identity.
set -euo pipefail

git -C "${CLAUDE_PROJECT_DIR:-.}" config --local user.name "Daniel Islam"
git -C "${CLAUDE_PROJECT_DIR:-.}" config --local user.email "126803859+dislam7991@users.noreply.github.com"
