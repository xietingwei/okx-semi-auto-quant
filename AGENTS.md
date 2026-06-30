# Repository working rules

- Any requested source-code, configuration, documentation, or test change must be committed to Git after verification.
- Before finishing a change, run `git diff --check`, the relevant tests, and `git status --short`.
- Do not leave completed requested changes only in the working tree.
- Preserve unrelated user changes and include only files belonging to the current task in the commit.
- Report the resulting commit hash in the final response.
- For a new conversation or handoff, read `docs/AGENT_MEMORY.md` and
  `docs/PROJECT_STRUCTURE.md` before making architectural or product changes.
