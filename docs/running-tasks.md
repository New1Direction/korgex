# Running Tasks with Korgex

Once Korgex is installed and connected to your repository, you're ready to start coding. This guide walks through the key steps of running a task — from writing your prompt to reviewing the final diff.

## Write a Clear Prompt

Korgex works best when your prompt is specific and scoped. Use plain language — no need for perfect grammar or code.

**✅ Good prompts**

- Add a loading spinner while `fetchUserProfile` runs
- Fix the 500 error when submitting the feedback form
- Document the `useCache` hook with JSDoc
- Bump `next` from `10.2.3` to `15.4.5` and migrate to the app directory

**🚫 Avoid**

- Fix everything
- Optimize code
- Make this better

If Korgex needs more clarity, it will ask you for feedback before writing code.

## How Korgex Processes a Task

### Step 1: Exploration

Korgex reads your codebase — listing directories, examining file contents, and checking `AGENTS.md` and `README.md` for project conventions.

```bash
# Under the hood, Korgex runs:
list_files(".")
read_file("AGENTS.md")
read_file("README.md")
read_file("package.json")
```

### Step 2: Planning

Korgex formulates a structured plan with numbered steps. Each step describes what will be done and what will be verified.

Example plan:

```
1. *Add a new function `is_prime` in `lib/math.py`.*
   - Accepts an integer, returns a boolean
2. *Add a test for the new function in `tests/test_math.py`.*
   - Checks prime identification and edge cases
3. *Complete pre-commit steps*
   - Ensure proper testing, verification, review, and reflection are done
4. *Finalize the change.*
   - Create a descriptive commit message
```

### Step 3: Approval

Korgex presents the plan and waits for approval before writing any code. This is your chance to course-correct.

### Step 4: Execution

Once approved, Korgex executes each step sequentially:

- **Edit files** using `write_file` or `replace_with_git_merge_diff`
- **Run commands** in a bash session (`run_in_bash_session`)
- **Verify** each change by re-reading files
- **Run tests** to confirm nothing is broken

After every modification, Korgex confirms the change was applied correctly before marking the step complete.

### Step 5: Pre-commit Checks

Before submitting, Korgex runs pre-commit verification:

1. Run the test suite
2. Run linters
3. Type-check the codebase
4. Verify no debug artifacts remain

### Step 6: Submission

Korgex creates a branch, commits the changes, and presents a summary:

- ✅ Files changed
- Total lines added/removed
- Branch name and commit message

## Watching Korgex Work

You'll see a real-time activity feed as each step completes, with inline explanations of each change and a mini diff preview for each file.

## Giving Feedback Mid-Task

You can send feedback to Korgex while it's working:

- Ask Korgex to change its approach
- Revise specific code
- Clarify logic

Korgex will respond and, if needed, replan or revise the task. You're in control at every step.

## Starting Tasks from GitHub Issues

You can trigger Korgex from a GitHub issue by adding the label `korgex` (case insensitive). Korgex will comment on the issue with its plan and, upon completion, provide a link to the pull request.

## Pausing Korgex

You can pause Korgex at any time. When paused, it won't do any work and will wait for your next instructions. You can prompt it again, unpause it, or cancel the task.