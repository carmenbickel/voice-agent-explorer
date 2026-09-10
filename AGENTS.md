# Voice Agent Explorer - Agent Instructions

## General
- Implement only the current GitHub Issue.
- Keep the architecture simple and readable.
- Do not implement future issues early.
- Do not add frameworks or infrastructure unless required by the issue.
- Prefer clear code over abstraction.

## Workflow
- Determine the current issue number from the current branch name.
- Read the GitHub Issue using GitHub CLI.
- Implement all "What to implement" items.
- Validate all "Done / Acceptance criteria".
- Run relevant tests/checks after changes.
- Fix failures before stopping.

## Git
- Do not merge to master.
- Do not push unless explicitly requested.
- Do not commit unless explicitly requested.
- Leave changes in the working tree for review.

## Reporting
At the end, summarize:
- files changed
- what was implemented
- tests/checks run
- acceptance criteria status
- anything that still needs manual verification