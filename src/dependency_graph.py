"""
KorgKode Dependency Graph & Impact Analyzer — Safe downstream edits.

Performs AST-based static analysis of the codebase to track import graphs
and trace references to modified classes, functions, or variable symbols.

Architecture:
    [Target File to Edit]
            │
            ▼
    [Parse AST of Codebase] ──▶ [Map Imports & Symbol Definitions]
                                        │
                                        ▼
    [Find Downstream Dependents] ──▶ [Locate Symbol References (File, Line, Code)]
                                        │
                                        ▼
                       [Output Structured Impact Report]
"""

import os
import ast
from pathlib import Path
from typing import Dict, List, Set, Optional, Any


class DependencyAnalyzer:
    """Analyzes codebase dependencies using Python's native AST parser."""
    
    def __init__(self, repo_root: str):
        self.repo_root = os.path.abspath(repo_root)
        # file_path → set of files it imports
        self.import_graph: Dict[str, Set[str]] = {}
        # file_path → set of files that import it (reverse deps)
        self.dependent_graph: Dict[str, Set[str]] = {}
        self._scanned = False
    
    def scan_codebase(self) -> None:
        """Scan all Python files in the repo and construct import graphs."""
        if self._scanned:
            return
        
        py_files = [
            str(p.resolve()) for p in Path(self.repo_root).rglob("*.py")
            if "node_modules" not in p.parts and "venv" not in p.parts
            and ".git" not in p.parts and "__pycache__" not in p.parts
        ]
        
        for filepath in py_files:
            rel_path = os.path.relpath(filepath, self.repo_root)
            self.import_graph[rel_path] = set()
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=filepath)
                
                for node in ast.walk(tree):
                    imported_module = None
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imported_module = alias.name
                    elif isinstance(node, ast.ImportFrom):
                        imported_module = node.module
                    
                    if imported_module:
                        imported_rel_file = self._resolve_module_to_file(imported_module)
                        if imported_rel_file and imported_rel_file != rel_path:
                            self.import_graph[rel_path].add(imported_rel_file)
            
            except SyntaxError:
                pass  # Skip files that don't parse
        
        # Build reverse (dependent) graph
        for file_path in self.import_graph:
            self.dependent_graph[file_path] = set()
        for file_path, imports in self.import_graph.items():
            for imported_file in imports:
                if imported_file not in self.dependent_graph:
                    self.dependent_graph[imported_file] = set()
                self.dependent_graph[imported_file].add(file_path)
        
        self._scanned = True
    
    def analyze_impact(self, target_file: str, changed_symbols: List[str] = None) -> dict:
        """
        Analyze the cascading impact of changing a target file.
        
        Args:
            target_file: Relative path to the file being modified.
            changed_symbols: Names of functions, classes, or vars changing.
        
        Returns:
            dict with impact report.
        """
        self.scan_codebase()
        
        target_file = os.path.relpath(
            os.path.join(self.repo_root, target_file), self.repo_root
        )
        changed_symbols = changed_symbols or []
        
        # Immediate dependents
        direct_dependents = list(self.dependent_graph.get(target_file, set()))
        
        impacted_files_report = []
        for dep_file in direct_dependents:
            abs_dep_path = os.path.join(self.repo_root, dep_file)
            symbol_references = {}
            
            if changed_symbols and os.path.exists(abs_dep_path):
                try:
                    with open(abs_dep_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        tree = ast.parse("".join(lines), filename=abs_dep_path)
                    
                    for symbol in changed_symbols:
                        symbol_references[symbol] = []
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Name) and node.id == symbol:
                                line_num = node.lineno
                                line_content = lines[line_num - 1].strip() if line_num <= len(lines) else ""
                                symbol_references[symbol].append({
                                    "line": line_num,
                                    "context": line_content,
                                })
                except Exception:
                    pass
            
            impacted_files_report.append({
                "filepath": dep_file,
                "imports_target": True,
                "referenced_symbols": symbol_references,
            })
        
        # Build full dependency chain (transitive)
        transitive_dependents = set()
        def walk_dependents(file_key):
            for dep in self.dependent_graph.get(file_key, set()):
                if dep not in transitive_dependents:
                    transitive_dependents.add(dep)
                    walk_dependents(dep)
        walk_dependents(target_file)
        
        return {
            "target_file": target_file,
            "direct_dependents_count": len(direct_dependents),
            "transitive_dependents_count": len(transitive_dependents),
            "impacted_files": impacted_files_report,
            "all_transitive_dependents": sorted(transitive_dependents),
            "suggestion": (
                f"Edit {len(transitive_dependents)} file(s) in this commit: "
                f"{target_file} and {len(direct_dependents)} direct dependent(s)."
            ) if direct_dependents else "No downstream dependents found.",
        }
    
    def get_god_nodes(self, min_dependents: int = 3) -> list:
        """Find 'god nodes' — files with the most dependents."""
        self.scan_codebase()
        nodes = []
        for filepath, dependents in self.dependent_graph.items():
            if len(dependents) >= min_dependents:
                nodes.append({
                    "filepath": filepath,
                    "dependents_count": len(dependents),
                    "dependents": sorted(dependents),
                })
        return sorted(nodes, key=lambda n: -n["dependents_count"])
    
    def _resolve_module_to_file(self, module_path: str) -> Optional[str]:
        """Convert dot-notation import (e.g. 'src.auth.service') to relative filepath."""
        if not module_path:
            return None
        
        parts = module_path.split(".")
        
        strategies = [
            os.path.join(*parts) + ".py",
            os.path.join(*parts, "__init__.py"),
        ]
        
        for strat in strategies:
            abs_path = os.path.join(self.repo_root, strat)
            if os.path.isfile(abs_path):
                return os.path.relpath(abs_path, self.repo_root)
        
        return None