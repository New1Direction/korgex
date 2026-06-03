---
description: Make a verified restore point — prove the ledger is intact, then snapshot the working tree.
argument-hint: [name]
---
<!-- Adapted from the ECC project (github.com/affaan-m/ECC), MIT — reworked around korgex's verifiable ledger. -->
# Checkpoint

Create a clean, verified restore point named `$ARGUMENTS` (or an auto name from the date if blank).

1. **Verify** the cognition ledger is intact: run `korgex verify`. If it reports tampering, STOP and surface it — don't checkpoint over a broken chain.
2. **Show** what's uncommitted: `git status --short` and a one-line `git diff --stat`.
3. **Snapshot** the working tree as a restore point:
   - if there are changes, either `git add -A && git commit -m "checkpoint: $ARGUMENTS"`, or `git stash push -m "checkpoint: $ARGUMENTS"` if the user would rather not commit;
   - capture the resulting short SHA.
4. **Report** the checkpoint name + SHA, and note they can return to it with `git restore` / `git checkout <sha>` (or `/rewind` for in-session file undo).
