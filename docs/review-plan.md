# Reviewing Plans & Giving Feedback

Once you start a task, Seluj generates a **plan** before writing any code. This gives you visibility into Seluj's approach and lets you iterate before any changes are made.

## Reviewing the Plan

After exploring your codebase, Seluj presents its plan. You'll see:

- A natural language description of what Seluj intends to do
- Step-by-step breakdowns
- Any assumptions or setup steps

Each step includes:
- What file(s) will be modified
- What the change intends to accomplish
- How the result will be verified

**To approve:** Signal approval when you're ready for Seluj to begin executing.

**To revise:** Provide feedback through the chat interface. Seluj will update the plan and present it again.

## Giving Feedback

At any point during execution, you can provide feedback:

- Ask Seluj to revise a step
- Point out something it missed
- Clarify your original request
- Answer questions Seluj may have

Seluj will respond and adjust its approach accordingly.

## Mid-Task Steering

You can intervene mid-execution:

- **Change approach:** "Use a different library for parsing"
- **Revise code:** "Make the function async instead"
- **Clarify logic:** "Only apply this to authenticated users"

Seluj will incorporate your feedback and continue.

## Summary & Review

When Seluj finishes, it provides:

- ✅ Files changed
- ⏱ Total runtime
- ➕ Lines of code added/changed/removed
- 🌿 The branch name and commit message

You can review the diff, make additional changes, or merge the branch.