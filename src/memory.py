"""
Korgex Memory System — file-based persistent memory inspired by Claude Code.

Architecture:
  memory_dir/
  ├── MEMORY.md              ← Index file (loaded every conversation)
  ├── user_role.md           ← User preferences (type: user)
  ├── feedback_testing.md    ← Corrections (type: feedback)
  ├── project_deadlines.md   ← Project context (type: project)
  └── linear_tickets.md      ← External pointers (type: reference)

Each memory file:
  ---
  name: short-kebab-case-slug
  description: one-line summary — used to decide relevance
  metadata:
    type: user | feedback | project | reference
  ---

  Body content. Link [[other-memories]] with double-brackets.

Key design rules (from Claude Code's architecture):
- MEMORY.md is always loaded — lines after 200 are truncated
- Individual files are loaded on-demand when relevance is suspected
- Immutable: never edit in-place, delete and recreate
- Four types: user, feedback, project, reference
- Staleness detection: always verify before acting on memory
"""

import os
import re
import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional

MEMORY_DIR = None  # Set during init


def init_memory(project_root: str = None):
    """Initialize the memory directory structure."""
    global MEMORY_DIR
    if project_root:
        MEMORY_DIR = os.path.join(project_root, ".korgex", "memory")
    else:
        MEMORY_DIR = os.path.join(os.path.expanduser("~"), ".korgex", "memory")
    
    os.makedirs(MEMORY_DIR, exist_ok=True)
    
    # Create MEMORY.md index if it doesn't exist
    index_path = os.path.join(MEMORY_DIR, "MEMORY.md")
    if not os.path.exists(index_path):
        with open(index_path, "w") as f:
            f.write("# Memory Index\n\n_This index is loaded every conversation. Keep entries under 150 chars._\n\n")
    
    return MEMORY_DIR


# ── Memory Types ────────────────────────────────────────────────────────

MEMORY_TYPES = ["user", "feedback", "project", "reference"]

TYPE_DESCRIPTIONS = {
    "user": """
<type>
    <name>user</name>
    <description>Information about the user's role, goals, responsibilities, and knowledge.</description>
    <when_to_save>When you learn any details about the user's role, preferences, or knowledge.</when_to_save>
    <how_to_use>Tailor responses to the user's expertise level and perspective.</how_to_use>
</type>
""",
    "feedback": """
<type>
    <name>feedback</name>
    <description>Guidance the user has given about how to approach work — both what to avoid and what to keep doing.</description>
    <when_to_save>When the user corrects your approach OR confirms a non-obvious approach worked.</when_to_save>
    <how_to_use>Let guidance shape behavior so the user doesn't need to repeat themselves.</how_to_use>
</type>
""",
    "project": """
<type>
    <name>project</name>
    <description>Ongoing work, goals, deadlines, or decisions not derivable from code or git history.</description>
    <when_to_save>When you learn who is doing what, why, or by when.</when_to_save>
    <how_to_use>Use to understand broader context behind the user's requests.</how_to_use>
</type>
""",
    "reference": """
<type>
    <name>reference</name>
    <description>Pointers to where information can be found in external systems.</description>
    <when_to_save>When you learn about resources in external systems and their purpose.</when_to_save>
    <how_to_use>Check these when the user references an external system.</how_to_use>
</type>
""",
}


# ── Memory CRUD ─────────────────────────────────────────────────────────

def _validate_frontmatter(name: str, description: str, mem_type: str) -> Optional[str]:
    """Validate memory frontmatter fields."""
    if not re.match(r'^[a-z0-9_-]+$', name):
        return "Name must be lowercase kebab-case (a-z, 0-9, hyphens, underscores)"
    if not description or len(description) < 10:
        return "Description must be at least 10 characters"
    if mem_type not in MEMORY_TYPES:
        return f"Type must be one of: {', '.join(MEMORY_TYPES)}"
    return None


def save_memory(name: str, description: str, mem_type: str, body: str,
                source: str = None) -> dict:
    """Save a memory to its own file. Immutable: never edit in-place.

    If `source` is given (a file path, or "fact:<text>"), the memory is anchored
    to a sha256 baseline of that source at write time. memory_drift.scan() can
    then detect when the source has moved on and flag the memory as drifted.
    """
    error = _validate_frontmatter(name, description, mem_type)
    if error:
        return {"success": False, "error": error}
    
    if not MEMORY_DIR:
        init_memory()
    
    # Check for duplicates (search existing files for matching description)
    existing = search_memory(description[:30])
    if existing:
        return {"success": False, "error": f"Similar memory already exists: {existing[0]['file']}"}
    
    filename = f"{name}.md"
    filepath = os.path.join(MEMORY_DIR, filename)
    
    if os.path.exists(filepath):
        return {"success": False, "error": f"Memory '{name}' already exists. Delete it first (immutable design)."}
    
    anchor = ""
    if source:
        from src.memory_drift import compute_baseline
        source_sha = compute_baseline(source)
        anchor = f'source: "{source}"\nsource_sha: {source_sha}\n'

    content = f"""---
name: {name}
description: {description}
metadata:
  type: {mem_type}
created: {datetime.now().isoformat()}
{anchor}---

{body}

---
_Links: [[{name}]]_
"""

    with open(filepath, "w") as f:
        f.write(content)
    
    # Update MEMORY.md index
    _update_index(name, description, mem_type)
    
    return {"success": True, "file": filename, "path": filepath}


def delete_memory(name: str) -> dict:
    """Delete a memory file by its slug name (immutable design)."""
    if not MEMORY_DIR:
        return {"success": False, "error": "Memory not initialized"}
    
    filepath = os.path.join(MEMORY_DIR, f"{name}.md")
    if not os.path.exists(filepath):
        return {"success": False, "error": f"Memory '{name}' not found"}
    
    os.remove(filepath)
    _remove_from_index(name)
    
    return {"success": True, "deleted": f"{name}.md"}


def read_memory(name: str) -> Optional[dict]:
    """Read a single memory file and parse its frontmatter + body."""
    if not MEMORY_DIR:
        return None
    
    filepath = os.path.join(MEMORY_DIR, f"{name}.md")
    if not os.path.exists(filepath):
        return None
    
    return _parse_memory_file(filepath)


def list_memories(mem_type: str = None) -> list[dict]:
    """List all memories, optionally filtered by type."""
    if not MEMORY_DIR:
        return []
    
    memories = []
    for fname in os.listdir(MEMORY_DIR):
        if not fname.endswith(".md") or fname == "MEMORY.md":
            continue
        mem = _parse_memory_file(os.path.join(MEMORY_DIR, fname))
        if mem:
            if mem_type and mem.get("type") != mem_type:
                continue
            memories.append(mem)
    
    return sorted(memories, key=lambda m: m.get("created", ""), reverse=True)


def search_memory(query: str) -> list[dict]:
    """Search memory descriptions and bodies for a query string."""
    results = []
    for mem in list_memories():
        if query.lower() in (mem.get("description", "") + mem.get("body", "")).lower():
            results.append(mem)
    return results


def get_memory_index() -> str:
    """Return the MEMORY.md index content."""
    if not MEMORY_DIR:
        return ""
    index_path = os.path.join(MEMORY_DIR, "MEMORY.md")
    if os.path.exists(index_path):
        with open(index_path) as f:
            return f.read()
    return ""


# ── Internal Helpers ────────────────────────────────────────────────────

def _parse_memory_file(filepath: str) -> Optional[dict]:
    """Parse a memory file, extracting frontmatter and body."""
    with open(filepath) as f:
        content = f.read()
    
    # Parse YAML frontmatter
    match = re.match(r'^---\n(.*?)\n---\n(.*)', content, re.DOTALL)
    if not match:
        return None
    
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    
    body = match.group(2).strip()
    
    return {
        "name": frontmatter.get("name"),
        "description": frontmatter.get("description", ""),
        "type": frontmatter.get("metadata", {}).get("type", "unknown"),
        "created": frontmatter.get("created", ""),
        "source": frontmatter.get("source"),
        "source_sha": frontmatter.get("source_sha"),
        "body": body,
        "file": os.path.basename(filepath),
    }


def _update_index(name: str, description: str, mem_type: str):
    """Add a memory entry to the MEMORY.md index."""
    index_path = os.path.join(MEMORY_DIR, "MEMORY.md")
    entry = f"- [{name}]({name}.md) — {description} _({mem_type})_\n"
    
    if os.path.exists(index_path):
        with open(index_path, "r+") as f:
            content = f.read()
            if entry not in content:
                f.write(entry)
    else:
        with open(index_path, "w") as f:
            f.write(f"# Memory Index\n\n{entry}")


def _remove_from_index(name: str):
    """Remove a memory entry from the MEMORY.md index."""
    index_path = os.path.join(MEMORY_DIR, "MEMORY.md")
    if not os.path.exists(index_path):
        return
    
    with open(index_path) as f:
        lines = f.readlines()
    
    lines = [l for l in lines if f"]({name}.md)" not in l]
    
    with open(index_path, "w") as f:
        f.writelines(lines)


def generate_memory_system_prompt() -> str:
    """Generate the memory system prompt section (injected into system prompt)."""
    if not MEMORY_DIR or not os.path.exists(MEMORY_DIR):
        return ""
    
    index = get_memory_index()
    if not index.strip() or index.strip() == "# Memory Index":
        return ""
    
    type_descriptions_text = "\n".join(TYPE_DESCRIPTIONS.values())
    
    return f"""
# Memory

You have a persistent, file-based memory system at `{MEMORY_DIR}`.
This directory already exists — write to it directly.

You should build up this memory system over time so that future conversations
can have a complete picture of who the user is and how to work with them.

## Types of memory

{type_descriptions_text}

## Current Memory Index

{index[:3000]}  # truncated past 3000 chars as Claude Code does
"""
