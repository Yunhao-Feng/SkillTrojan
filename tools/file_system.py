"""
File System Tool for SafeFlow - Minimal Utility Operations

Provides only the utility operations that complement windowed_editor:
- Bulk file search (for discovering files before editing)
- Quick path checks (for validation before operations)

For file editing, use windowed_editor.
For task completion, use base_tools__finish_task.
"""

import os
import logging
import ast
import re
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple
from tools.abs_tools import Tool, ToolCategory, ToolParameter, tool_function

# Semantic search has been disabled due to dependency issues
# Use text-based search instead for reliable functionality
SENTENCE_TRANSFORMERS_AVAILABLE = False
NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)


class FileSystemTool(Tool):
    """
    Enhanced File System Utilities for SafeFlow.

    Provides utility operations that complement windowed_editor:
    - Bulk file search across directories
    - Semantic code search using embeddings
    - Quick path existence checks
    - Code structure analysis

    No overlap with windowed_editor or base_tools.
    """

    def __init__(
        self,
        item_id: str,
        name: str = "file_system",
        description: str = "File discovery, semantic search, and path validation utilities",
        read_only: bool = False,
    ):
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.FILE_SYSTEM
        )

        self.item_id = item_id
        self.read_only = read_only

        # Semantic search disabled - use text search instead
        self._code_cache = {}  # Cache for parsed code structures

    def _validate_absolute_path(self, path: str) -> Path:
        """Validate that the path is absolute and return resolved Path object."""
        try:
            p = Path(path)
            if not p.is_absolute():
                raise ValueError(f"Path must be absolute, got: {path}")
            return p.resolve()
        except Exception as e:
            raise ValueError(f"Invalid path '{path}': {e}")

    def _get_semantic_model(self):
        """Semantic search disabled - always return False to use text search."""
        return False

    def _extract_code_elements(self, file_path: Path) -> List[Dict[str, Any]]:
        """Extract functions, classes, and important code elements from Python files."""
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')

            # Handle different file types
            if file_path.suffix == '.py':
                return self._extract_python_elements(content, file_path)
            else:
                return self._extract_text_elements(content, file_path)
        except Exception as e:
            logger.warning(f"Failed to extract elements from {file_path}: {e}")
            return []

    def _extract_python_elements(self, content: str, file_path: Path) -> List[Dict[str, Any]]:
        """Extract Python functions, classes, and docstrings."""
        elements = []
        try:
            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # Extract function info
                    func_info = {
                        'type': 'function',
                        'name': node.name,
                        'line': node.lineno,
                        'file_path': str(file_path),
                        'signature': f"def {node.name}({', '.join(arg.arg for arg in node.args.args)})",
                        'docstring': ast.get_docstring(node) or '',
                        'content_preview': self._get_code_snippet(content, node.lineno, 5)
                    }
                    elements.append(func_info)

                elif isinstance(node, ast.ClassDef):
                    # Extract class info
                    class_info = {
                        'type': 'class',
                        'name': node.name,
                        'line': node.lineno,
                        'file_path': str(file_path),
                        'signature': f"class {node.name}",
                        'docstring': ast.get_docstring(node) or '',
                        'content_preview': self._get_code_snippet(content, node.lineno, 5)
                    }
                    elements.append(class_info)

            # Add file-level docstring if exists
            file_docstring = ast.get_docstring(tree)
            if file_docstring:
                elements.append({
                    'type': 'file_docstring',
                    'name': file_path.name,
                    'line': 1,
                    'file_path': str(file_path),
                    'signature': f"File: {file_path.name}",
                    'docstring': file_docstring,
                    'content_preview': file_docstring[:200] + '...' if len(file_docstring) > 200 else file_docstring
                })

        except SyntaxError:
            # If parsing fails, fall back to text extraction
            return self._extract_text_elements(content, file_path)

        return elements

    def _extract_text_elements(self, content: str, file_path: Path) -> List[Dict[str, Any]]:
        """Extract meaningful text chunks from non-Python files."""
        elements = []
        lines = content.splitlines()

        # Look for comment blocks, TODO items, etc.
        current_block = []
        block_start = 1

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Detect comment blocks or documentation
            if (stripped.startswith('#') or stripped.startswith('//') or
                stripped.startswith('/*') or stripped.startswith('*') or
                'TODO' in stripped or 'FIXME' in stripped or 'NOTE' in stripped):

                if not current_block:
                    block_start = i
                current_block.append(line)
            else:
                # End of comment block
                if current_block and len(current_block) >= 2:  # Only meaningful blocks
                    block_text = '\n'.join(current_block)
                    elements.append({
                        'type': 'comment_block',
                        'name': f"Comment block (line {block_start})",
                        'line': block_start,
                        'file_path': str(file_path),
                        'signature': f"Comment in {file_path.name}",
                        'docstring': block_text,
                        'content_preview': block_text[:200] + '...' if len(block_text) > 200 else block_text
                    })
                current_block = []

        # Add final block if exists
        if current_block:
            block_text = '\n'.join(current_block)
            elements.append({
                'type': 'comment_block',
                'name': f"Comment block (line {block_start})",
                'line': block_start,
                'file_path': str(file_path),
                'signature': f"Comment in {file_path.name}",
                'docstring': block_text,
                'content_preview': block_text[:200] + '...' if len(block_text) > 200 else block_text
            })

        return elements

    def _get_code_snippet(self, content: str, start_line: int, num_lines: int = 5) -> str:
        """Get a snippet of code around a specific line."""
        lines = content.splitlines()
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), start_idx + num_lines)

        snippet_lines = []
        for i in range(start_idx, end_idx):
            snippet_lines.append(f"{i+1:4d}: {lines[i]}")

        return '\n'.join(snippet_lines)

    @tool_function(
        description="Search for files matching a glob pattern. Use this to discover files before editing.",
        parameters=[
            ToolParameter("root_path", "string", "Absolute path to search from", required=True),
            ToolParameter("pattern", "string", "Glob pattern (e.g., '*.py', '**/*.txt')", required=True),
            ToolParameter("max_results", "integer", "Maximum results", required=False, default=100),
        ],
        returns="List of matching files for bulk discovery",
        category=ToolCategory.FILE_SYSTEM,
    )
    def file_system__find_files(
        self,
        root_path: str,
        pattern: str,
        max_results: int = 100
    ) -> Dict[str, Any]:
        """Search for files matching glob pattern. Use for bulk file discovery."""
        try:
            root = self._validate_absolute_path(root_path)

            if not root.exists():
                return {"success": False, "error": f"Root directory not found: {root}"}

            if not root.is_dir():
                return {"success": False, "error": f"Root path is not a directory: {root}"}

            matches = []
            count = 0

            for match in root.rglob(pattern):
                if count >= max_results:
                    break

                try:
                    stat = match.stat()
                    match_info = {
                        "name": match.name,
                        "absolute_path": str(match.resolve()),
                        "relative_path": str(match.relative_to(root)),
                        "type": "directory" if match.is_dir() else "file",
                        "size": stat.st_size if match.is_file() else None,
                    }

                    if match.is_file():
                        match_info["extension"] = match.suffix

                    matches.append(match_info)
                    count += 1

                except (OSError, PermissionError):
                    continue

            result = {
                "matches": matches,
                "pattern": pattern,
                "root_path": str(root),
            }

            return {"success": True, "result": result}

        except ValueError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Search failed: {e}"}

    @tool_function(
        description="Quick check if a path exists. Use before opening files in windowed_editor.",
        parameters=[
            ToolParameter("path", "string", "Absolute path to check", required=True)
        ],
        returns="Path existence and basic info",
        category=ToolCategory.FILE_SYSTEM,
    )
    def file_system__path_info(self, path: str) -> Dict[str, Any]:
        """Quick path existence check. Use before opening files."""
        try:
            path_obj = self._validate_absolute_path(path)

            exists = path_obj.exists()
            result = {
                "exists": exists,
                "path": str(path_obj),
            }

            if exists:
                try:
                    stat = path_obj.stat()
                    result.update({
                        "type": "directory" if path_obj.is_dir() else "file",
                        "size": stat.st_size if path_obj.is_file() else None,
                    })

                    if path_obj.is_file():
                        result["extension"] = path_obj.suffix

                except (OSError, PermissionError) as e:
                    result["metadata_error"] = str(e)

            return {"success": True, "result": result}

        except ValueError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Path check failed: {e}"}

    @tool_function(
        description="Text-based search for code elements (functions, classes). Uses keyword matching instead of semantic embeddings.",
        parameters=[
            ToolParameter("root_path", "string", "Absolute path to search from", required=True),
            ToolParameter("query", "string", "Search query with keywords (e.g., 'authentication function login')", required=True),
            ToolParameter("file_pattern", "string", "Glob pattern for files to search (default: '**/*.py')", required=False, default="**/*.py"),
            ToolParameter("max_results", "integer", "Maximum number of results", required=False, default=10),
            ToolParameter("include_preview", "boolean", "Include code preview in results", required=False, default=True),
        ],
        returns="Text-matched code elements with relevance scores",
        category=ToolCategory.FILE_SYSTEM,
    )
    def file_system__semantic_search(
        self,
        root_path: str,
        query: str,
        file_pattern: str = "**/*.py",
        max_results: int = 10,
        include_preview: bool = True
    ) -> Dict[str, Any]:
        """Search for code elements using text-based matching."""
        try:
            root = self._validate_absolute_path(root_path)

            if not root.exists():
                return {"success": False, "error": f"Root directory not found: {root}"}

            # Use text-based search directly (semantic search disabled)
            return self._fallback_text_search(root, query, file_pattern, max_results, include_preview)

        except Exception as e:
            return {"success": False, "error": f"Semantic search failed: {e}"}

    def _fallback_text_search(
        self,
        root: Path,
        query: str,
        file_pattern: str,
        max_results: int,
        include_preview: bool
    ) -> Dict[str, Any]:
        """Fallback text-based search when semantic search is not available."""
        try:
            all_elements = []
            query_lower = query.lower()
            query_words = set(query_lower.split())

            for file_path in root.rglob(file_pattern):
                if file_path.is_file() and file_path.stat().st_size < 1024 * 1024:
                    elements = self._extract_code_elements(file_path)
                    all_elements.extend(elements)

            # Score elements based on text matching
            scored_results = []
            for element in all_elements:
                search_text = f"{element['name']} {element['signature']} {element['docstring']}".lower()

                # Calculate simple text similarity score
                score = 0.0

                # Exact query match gets high score
                if query_lower in search_text:
                    score += 0.8

                # Word matches
                element_words = set(search_text.split())
                word_matches = len(query_words & element_words)
                if query_words:
                    score += 0.6 * (word_matches / len(query_words))

                # Name similarity
                if query_lower in element['name'].lower():
                    score += 0.4

                if score > 0.1:
                    scored_results.append((element, score))

            # Sort and format results
            scored_results.sort(key=lambda x: x[1], reverse=True)

            matches = []
            for element, score in scored_results[:max_results]:
                result = {
                    "type": element["type"],
                    "name": element["name"],
                    "file_path": element["file_path"],
                    "line": element["line"],
                    "signature": element["signature"],
                    "similarity_score": float(score),
                    "docstring": element["docstring"][:200] + "..." if len(element["docstring"]) > 200 else element["docstring"]
                }

                if include_preview:
                    result["code_preview"] = element["content_preview"]

                matches.append(result)

            return {
                "success": True,
                "result": {
                    "matches": matches,
                    "query": query,
                    "total_searched": len(all_elements),
                    "search_method": "text_matching"
                }
            }

        except Exception as e:
            return {"success": False, "error": f"Text search failed: {e}"}

    @tool_function(
        description="Analyze code structure and extract all functions, classes, and important elements from files.",
        parameters=[
            ToolParameter("root_path", "string", "Absolute path to analyze", required=True),
            ToolParameter("file_pattern", "string", "Glob pattern for files (default: '**/*.py')", required=False, default="**/*.py"),
            ToolParameter("group_by_file", "boolean", "Group results by file", required=False, default=True),
        ],
        returns="Structured analysis of code elements",
        category=ToolCategory.FILE_SYSTEM,
    )
    def file_system__analyze_code_structure(
        self,
        root_path: str,
        file_pattern: str = "**/*.py",
        group_by_file: bool = True
    ) -> Dict[str, Any]:
        """Analyze and extract code structure from files."""
        try:
            root = self._validate_absolute_path(root_path)

            if not root.exists():
                return {"success": False, "error": f"Root directory not found: {root}"}

            analysis_result = {}
            total_elements = 0

            for file_path in root.rglob(file_pattern):
                if file_path.is_file() and file_path.stat().st_size < 1024 * 1024:  # Skip large files
                    try:
                        elements = self._extract_code_elements(file_path)
                        if elements:
                            relative_path = str(file_path.relative_to(root))

                            if group_by_file:
                                analysis_result[relative_path] = {
                                    "file_path": str(file_path),
                                    "elements": elements,
                                    "element_count": len(elements)
                                }
                            else:
                                for element in elements:
                                    element["relative_path"] = relative_path

                            total_elements += len(elements)
                    except Exception as e:
                        logger.warning(f"Failed to analyze {file_path}: {e}")
                        continue

            if not group_by_file:
                # Flatten all elements
                all_elements = []
                for file_data in analysis_result.values():
                    all_elements.extend(file_data["elements"])

                # Group by type
                by_type = {}
                for element in all_elements:
                    elem_type = element["type"]
                    if elem_type not in by_type:
                        by_type[elem_type] = []
                    by_type[elem_type].append(element)

                analysis_result = {
                    "by_type": by_type,
                    "all_elements": all_elements
                }

            return {
                "success": True,
                "result": {
                    "analysis": analysis_result,
                    "total_files": len(analysis_result) if group_by_file else len(set(e.get("relative_path", "") for e in analysis_result.get("all_elements", []))),
                    "total_elements": total_elements,
                    "root_path": str(root),
                    "pattern": file_pattern
                }
            }

        except Exception as e:
            return {"success": False, "error": f"Code structure analysis failed: {e}"}

    @tool_function(
        description="Search for symbol definitions, references, and usages using AST analysis. More precise than text search.",
        parameters=[
            ToolParameter("root_path", "string", "Absolute path to search from", required=True),
            ToolParameter("symbol_name", "string", "Symbol name to search for (function, class, variable, etc.)", required=True),
            ToolParameter("symbol_type", "string", "Type of symbol: 'function', 'class', 'variable', 'import', 'attribute', or 'any'", required=False, default="any"),
            ToolParameter("search_type", "string", "Search type: 'definition', 'reference', 'usage', or 'all'", required=False, default="all"),
            ToolParameter("file_pattern", "string", "Glob pattern for files to search", required=False, default="**/*.py"),
            ToolParameter("include_builtin", "boolean", "Include built-in and imported symbols", required=False, default=False),
            ToolParameter("max_results", "integer", "Maximum number of results", required=False, default=50),
        ],
        returns="Symbol definitions, references, and usage locations with context",
        category=ToolCategory.FILE_SYSTEM,
    )
    def file_system__symbol_search(
        self,
        root_path: str,
        symbol_name: str,
        symbol_type: str = "any",
        search_type: str = "all",
        file_pattern: str = "**/*.py",
        include_builtin: bool = False,
        max_results: int = 50
    ) -> Dict[str, Any]:
        """Search for symbol definitions and references using AST analysis."""
        try:
            root = self._validate_absolute_path(root_path)

            if not root.exists():
                return {"success": False, "error": f"Root directory not found: {root}"}

            symbol_results = {
                "definitions": [],
                "references": [],
                "usages": [],
                "imports": []
            }

            total_files_searched = 0

            for file_path in root.rglob(file_pattern):
                if file_path.is_file() and file_path.stat().st_size < 2 * 1024 * 1024:  # Skip files > 2MB
                    try:
                        matches = self._analyze_file_for_symbols(
                            file_path, symbol_name, symbol_type, search_type, include_builtin
                        )

                        if matches:
                            for match_type, match_list in matches.items():
                                if match_list:
                                    symbol_results[match_type].extend(match_list)

                        total_files_searched += 1

                        # Limit total results
                        total_results = sum(len(v) for v in symbol_results.values())
                        if total_results >= max_results:
                            break

                    except Exception as e:
                        logger.warning(f"Failed to analyze {file_path}: {e}")
                        continue

            # Sort results by relevance (definitions first, then references, then usages)
            all_matches = []
            if search_type in ["definition", "all"]:
                all_matches.extend([(m, "definition", 3) for m in symbol_results["definitions"]])
            if search_type in ["reference", "all"]:
                all_matches.extend([(m, "reference", 2) for m in symbol_results["references"]])
            if search_type in ["usage", "all"]:
                all_matches.extend([(m, "usage", 1) for m in symbol_results["usages"]])

            # Add import information
            all_matches.extend([(m, "import", 2.5) for m in symbol_results["imports"]])

            # Sort by priority and limit results
            all_matches.sort(key=lambda x: (x[2], x[0].get("confidence", 0)), reverse=True)
            limited_matches = all_matches[:max_results]

            # Group results by type
            final_results = {
                "definitions": [m[0] for m in limited_matches if m[1] == "definition"],
                "references": [m[0] for m in limited_matches if m[1] == "reference"],
                "usages": [m[0] for m in limited_matches if m[1] == "usage"],
                "imports": [m[0] for m in limited_matches if m[1] == "import"]
            }

            return {
                "success": True,
                "result": {
                    "symbol_name": symbol_name,
                    "symbol_type": symbol_type,
                    "search_type": search_type,
                    "matches": final_results,
                    "total_matches": len(limited_matches),
                    "files_searched": total_files_searched,
                    "root_path": str(root),
                    "search_method": "ast_analysis"
                }
            }

        except Exception as e:
            return {"success": False, "error": f"Symbol search failed: {e}"}

    def _analyze_file_for_symbols(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_type: str,
        search_type: str,
        include_builtin: bool
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Analyze a single file for symbol occurrences using AST."""
        results = {
            "definitions": [],
            "references": [],
            "usages": [],
            "imports": []
        }

        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')

            # Try to parse as Python
            if file_path.suffix == '.py':
                try:
                    tree = ast.parse(content)
                    results = self._ast_symbol_search(
                        tree, content, file_path, symbol_name, symbol_type, search_type, include_builtin
                    )
                except SyntaxError:
                    # Fall back to text-based search for invalid Python
                    results = self._text_symbol_search(
                        content, file_path, symbol_name, symbol_type
                    )
            else:
                # Non-Python files: use text-based search
                results = self._text_symbol_search(
                    content, file_path, symbol_name, symbol_type
                )

        except Exception as e:
            logger.warning(f"Error analyzing {file_path}: {e}")

        return results

    def _ast_symbol_search(
        self,
        tree: ast.AST,
        content: str,
        file_path: Path,
        symbol_name: str,
        symbol_type: str,
        search_type: str,
        include_builtin: bool
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Perform AST-based symbol search."""
        results = {
            "definitions": [],
            "references": [],
            "usages": [],
            "imports": []
        }

        lines = content.splitlines()

        for node in ast.walk(tree):
            # Function definitions
            if isinstance(node, ast.FunctionDef) and (symbol_type in ["function", "any"]):
                if node.name == symbol_name:
                    results["definitions"].append({
                        "type": "function_definition",
                        "name": node.name,
                        "file_path": str(file_path),
                        "line": node.lineno,
                        "column": getattr(node, 'col_offset', 0),
                        "context": self._get_code_snippet(content, node.lineno, 3),
                        "signature": f"def {node.name}({', '.join(arg.arg for arg in node.args.args)})",
                        "docstring": ast.get_docstring(node) or "",
                        "confidence": 1.0
                    })

            # Class definitions
            elif isinstance(node, ast.ClassDef) and (symbol_type in ["class", "any"]):
                if node.name == symbol_name:
                    results["definitions"].append({
                        "type": "class_definition",
                        "name": node.name,
                        "file_path": str(file_path),
                        "line": node.lineno,
                        "column": getattr(node, 'col_offset', 0),
                        "context": self._get_code_snippet(content, node.lineno, 3),
                        "signature": f"class {node.name}",
                        "docstring": ast.get_docstring(node) or "",
                        "confidence": 1.0
                    })

            # Variable assignments
            elif isinstance(node, ast.Assign) and (symbol_type in ["variable", "any"]):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == symbol_name:
                        results["definitions"].append({
                            "type": "variable_assignment",
                            "name": target.id,
                            "file_path": str(file_path),
                            "line": node.lineno,
                            "column": getattr(target, 'col_offset', 0),
                            "context": self._get_code_snippet(content, node.lineno, 2),
                            "signature": f"{target.id} = ...",
                            "confidence": 0.9
                        })

            # Import statements
            elif isinstance(node, (ast.Import, ast.ImportFrom)) and (symbol_type in ["import", "any"]):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_name = alias.asname if alias.asname else alias.name
                        if imported_name == symbol_name or alias.name == symbol_name:
                            results["imports"].append({
                                "type": "import",
                                "name": imported_name,
                                "original_name": alias.name,
                                "file_path": str(file_path),
                                "line": node.lineno,
                                "column": getattr(node, 'col_offset', 0),
                                "context": self._get_code_snippet(content, node.lineno, 1),
                                "signature": f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else ""),
                                "confidence": 1.0
                            })
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        imported_name = alias.asname if alias.asname else alias.name
                        if imported_name == symbol_name or alias.name == symbol_name:
                            module = node.module or ""
                            results["imports"].append({
                                "type": "from_import",
                                "name": imported_name,
                                "original_name": alias.name,
                                "module": module,
                                "file_path": str(file_path),
                                "line": node.lineno,
                                "column": getattr(node, 'col_offset', 0),
                                "context": self._get_code_snippet(content, node.lineno, 1),
                                "signature": f"from {module} import {alias.name}" + (f" as {alias.asname}" if alias.asname else ""),
                                "confidence": 1.0
                            })

            # Name references (function calls, variable usage, etc.)
            elif isinstance(node, ast.Name) and (symbol_type in ["reference", "usage", "any"]):
                if node.id == symbol_name:
                    # Determine if it's a reference or usage based on context
                    context_type = "reference"
                    if isinstance(node.ctx, ast.Load):
                        context_type = "usage"
                    elif isinstance(node.ctx, ast.Store):
                        context_type = "assignment"

                    results["usages"].append({
                        "type": context_type,
                        "name": node.id,
                        "file_path": str(file_path),
                        "line": node.lineno,
                        "column": getattr(node, 'col_offset', 0),
                        "context": self._get_code_snippet(content, node.lineno, 1),
                        "confidence": 0.8
                    })

            # Attribute access (for method/property references)
            elif isinstance(node, ast.Attribute) and (symbol_type in ["attribute", "any"]):
                if node.attr == symbol_name:
                    results["references"].append({
                        "type": "attribute_access",
                        "name": node.attr,
                        "file_path": str(file_path),
                        "line": node.lineno,
                        "column": getattr(node, 'col_offset', 0),
                        "context": self._get_code_snippet(content, node.lineno, 1),
                        "confidence": 0.7
                    })

        return results

    def _text_symbol_search(
        self,
        content: str,
        file_path: Path,
        symbol_name: str,
        symbol_type: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fallback text-based symbol search for non-Python files or parsing errors."""
        results = {
            "definitions": [],
            "references": [],
            "usages": [],
            "imports": []
        }

        lines = content.splitlines()

        for line_num, line in enumerate(lines, 1):
            # Look for symbol occurrences in the line
            if symbol_name in line:
                # Try to determine context
                stripped = line.strip()
                context_type = "usage"

                # Simple heuristics for different contexts
                if stripped.startswith(f"def {symbol_name}") or f"function {symbol_name}" in line:
                    context_type = "definition"
                    results["definitions"].append({
                        "type": "function_definition",
                        "name": symbol_name,
                        "file_path": str(file_path),
                        "line": line_num,
                        "context": self._get_code_snippet(content, line_num, 2),
                        "signature": stripped,
                        "confidence": 0.6
                    })
                elif stripped.startswith(f"class {symbol_name}") or f"class {symbol_name}" in line:
                    context_type = "definition"
                    results["definitions"].append({
                        "type": "class_definition",
                        "name": symbol_name,
                        "file_path": str(file_path),
                        "line": line_num,
                        "context": self._get_code_snippet(content, line_num, 2),
                        "signature": stripped,
                        "confidence": 0.6
                    })
                elif "import" in line and symbol_name in line:
                    results["imports"].append({
                        "type": "import",
                        "name": symbol_name,
                        "file_path": str(file_path),
                        "line": line_num,
                        "context": line.strip(),
                        "signature": stripped,
                        "confidence": 0.7
                    })
                else:
                    results["usages"].append({
                        "type": "text_reference",
                        "name": symbol_name,
                        "file_path": str(file_path),
                        "line": line_num,
                        "context": line.strip(),
                        "confidence": 0.5
                    })

        return results

    def get_tool_info(self) -> Dict[str, Any]:
        """Get information about this tool"""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "item_id": self.item_id,
            "functions": len(self.functions) if hasattr(self, 'functions') else 0,
        }