# CLAUDE.md

At the start of every conversation, read the following files before doing anything else:

1. `llms.md` — codebase navigation guide for AI agents (where to find what, key patterns, config reference, pipeline steps)
2. `README.md` — user-facing documentation (project overview, configuration, pipeline behavior)

## Testing

Do not automatically run test. Only the user can run test. You can adapt the test code base, but not actually running them. You need to wait for the user response with the test results.

## Doc

When user ask about updating the doc. You need to update the main Readme.md at the project root, the llms.md (which serve as architectural doc for AI agent to understand the code), and eventually the tests/Readme.md which handle tests doc.