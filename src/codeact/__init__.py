"""CodeAct — a persistent, fuel-metered Python kernel as the agent's action space.

The agent writes Python that COMPOSES governed tools (read_file/write_file/edit/
bash/glob/grep/web_*/Retrieve/call_tool) instead of calling them one at a time.
Code runs in a long-lived subprocess with a persistent namespace (vars/imports/
defs survive across actions), metered for wall-time/memory/output, and bridged so
every in-code tool call round-trips back through the parent's governed,
ledger-recorded tool layer. A timeout or crash is recoverable — the parent resets
the kernel and reports what happened.

Public surface:
  - ``KernelHandle`` — parent-side handle (lazy spawn, ``.exec``, ``.reset``).
  - ``resolve_fuel`` — read the KORGEX_CODEACT_* knobs into a fuel dict.
  - ``protocol`` — the wire message-shape constants/builders shared by both ends.
"""

from .kernel import KernelHandle, resolve_fuel
from . import protocol

__all__ = ["KernelHandle", "resolve_fuel", "protocol"]
