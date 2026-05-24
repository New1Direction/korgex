"""
Korgex AST Context Compressor — High-fidelity token savings.

Prunes the body implementation of unrelated functions and classes in massive
Python files, delivering a highly compressed, semantic code skeleton.

Architecture:
    [Massive Python File]
            │
            ▼
     [Parse to AST] ──▶ [Walk AST Nodes]
                             │
                             ▼
  [Is Node in Focus List?] ──▶ NO  ──▶ [Prune Body to Placeholder]
            │
           YES
            ▼
   [Keep Full Body] ──▶ [unparse() back to Source Code]
                               │
                               ▼
                      [Return Clean Skeleton]
"""

import ast
import os
from typing import List, Optional, Set


class ASTCompressor(ast.NodeTransformer):
    """Transforms a Python AST by pruning non-focus symbols while preserving
    structure, signatures, docstrings, and decorators."""

    def __init__(self, focus_symbols: Optional[List[str]] = None):
        """
        Args:
            focus_symbols: List of class or function names to keep FULLY expanded.
                           All other function/method bodies will be pruned.
        """
        self.focus_symbols: Set[str] = set(focus_symbols or [])

    def compress(self, filepath: str) -> str:
        """Reads a file, prunes it, and returns the compressed source code."""
        if not os.path.exists(filepath):
            return f"Error: File not found: {filepath}"

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            tree = ast.parse(content, filename=filepath)

            # Run AST transformations
            pruned_tree = self.visit(tree)
            ast.fix_missing_locations(pruned_tree)

            # Convert back to clean python source code
            return ast.unparse(pruned_tree)
        except Exception as e:
            return f"Compression Error: Failed to parse AST: {str(e)}"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._prune_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._prune_function(node)

    def _prune_function(self, node: ast.AST) -> ast.AST:
        """Prunes the body of functions/methods unless they are in the focus list."""
        if node.name in self.focus_symbols:
            # Keep the entire function intact
            return self.generic_visit(node)

        # Retain the docstring if it exists
        docstring = ast.get_docstring(node)
        new_body = []

        if docstring:
            # Preserve docstring node as first element
            new_body.append(ast.Expr(value=ast.Constant(value=docstring)))

        # Append a placeholder comment/expression showing pruning statistics
        line_count = len(node.body)
        placeholder_text = f"... [Implementation Pruned: {line_count} nodes] ..."
        new_body.append(ast.Expr(value=ast.Constant(value=placeholder_text)))

        # Replace the function body
        node.body = new_body
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        """Visit class definitions — if in focus, keep full; otherwise keep
        only the class signature and inline functions/methods in focus."""
        if node.name in self.focus_symbols:
            return self.generic_visit(node)

        # If not in focus, still recurse into children — methods might be
        # individually in the focus list
        self.generic_visit(node)

        # If all methods got pruned and there's no remaining meaningful body,
        # leave just the class signature
        if all(
            self._is_pruned_stub(item) for item in node.body
        ):
            # Keep docstring if present
            docstring = ast.get_docstring(node)
            new_body = []
            if docstring:
                new_body.append(ast.Expr(value=ast.Constant(value=docstring)))
            line_count = len(node.body)
            new_body.append(
                ast.Expr(
                    value=ast.Constant(
                        value=f"... [Class Implementation Pruned: {line_count} nodes] ..."
                    )
                )
            )
            node.body = new_body

        return node

    @staticmethod
    def _is_pruned_stub(node: ast.AST) -> bool:
        """Check if an AST node is a pruned stub (placeholder constant only)."""
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            val = node.value.value
            if isinstance(val, str) and "... [Implementation Pruned" in val:
                return True
            if isinstance(val, str) and "... [Class Implementation Pruned" in val:
                return True
        return False