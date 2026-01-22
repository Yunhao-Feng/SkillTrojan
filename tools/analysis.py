import ast
import json
import math
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tools.abs_tools import Tool, ToolCategory, ToolParameter, tool_function


# -----------------------------
# Helper Functions
# -----------------------------

def _read_text_best_effort(p: Path, max_bytes: int = 2_000_000) -> str:
    """Read file content with best effort, handling encoding errors."""
    try:
        data = p.read_bytes()
        if len(data) > max_bytes:
            data = data[:max_bytes]
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _iter_py_files(root: Path, file_pattern: str = "**/*.py", exclude_dirs: Optional[List[str]] = None) -> List[Path]:
    """Iterate Python files in root directory, excluding specified directories."""
    exclude_dirs = exclude_dirs or [".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "site-packages"]
    root = Path(root)
    out: List[Path] = []
    
    for p in root.rglob("*.py"):
        try:
            rel = p.relative_to(root)
        except Exception:
            continue
        
        # Check if any part of the path is in exclude_dirs
        if any(part in exclude_dirs for part in rel.parts):
            continue
        
        if p.is_file():
            out.append(p)
    
    return out


def _module_name_from_path(root: Path, file_path: Path) -> str:
    """
    Convert file path to Python module name.
    Example: pkg/sub/mod.py -> pkg.sub.mod
             pkg/sub/__init__.py -> pkg.sub
    """
    root = Path(root).resolve()
    file_path = Path(file_path).resolve()
    
    try:
        rel = file_path.relative_to(root)
    except Exception:
        rel = Path(file_path.name)

    # Handle __init__.py specially
    if rel.name == "__init__.py":
        parts = list(rel.parts[:-1])
    else:
        parts = list(rel.parts)
        if parts and parts[-1].endswith(".py"):
            parts[-1] = parts[-1][:-3]

    # Remove empty parts
    parts = [p for p in parts if p]
    return ".".join(parts)


def _resolve_import_from(current_module: str, level: int, module: Optional[str]) -> str:
    """
    Resolve relative import to absolute module name.
    
    Args:
        current_module: Current module name (e.g., 'a.b.c')
        level: Number of leading dots in relative import
        module: Module part after dots (can be None)
    
    Returns:
        Resolved absolute module name, or special marker for invalid imports
    """
    cur_parts = current_module.split(".") if current_module else []
    
    # Validate level is not too deep
    if level > len(cur_parts):
        # Invalid relative import (goes above package root)
        return f"<invalid_relative_import:level={level}:module={current_module}>"
    
    # Go up 'level' parts for relative import
    base_parts = cur_parts[:-level] if level > 0 else cur_parts
    mod_parts = module.split(".") if module else []
    
    resolved = ".".join([p for p in (base_parts + mod_parts) if p])
    return resolved if resolved else "<empty_module>"


def _is_probably_test_file(p: Path) -> bool:
    """Check if a file is likely a test file based on naming conventions."""
    name = p.name.lower()
    parts_lower = [x.lower() for x in p.parts]
    
    return (
        name.startswith("test_") or 
        name.endswith("_test.py") or 
        "tests" in parts_lower or
        "test" in parts_lower
    )


def _topological_sort(nodes: List[str], edges: List[Tuple[str, str]]) -> Tuple[List[str], List[List[str]]]:
    """
    Perform topological sort and detect cycles using Tarjan's algorithm.
    
    Returns:
        Tuple of (topological_order, cycles)
        - topological_order: List of nodes in topo order (empty if cycles exist)
        - cycles: List of strongly connected components with size > 1
    """
    g = defaultdict(set)
    indeg = defaultdict(int)
    
    for n in nodes:
        indeg[n] = 0
    
    for a, b in edges:
        if b not in g[a]:
            g[a].add(b)
            indeg[b] += 1

    # Try simple topological sort first
    q = deque([n for n in nodes if indeg[n] == 0])
    topo = []
    
    while q:
        n = q.popleft()
        topo.append(n)
        for nb in g[n]:
            indeg[nb] -= 1
            if indeg[nb] == 0:
                q.append(nb)

    if len(topo) == len(nodes):
        return topo, []

    # Cycles detected, use Tarjan's algorithm to find SCCs
    index = 0
    stack: List[str] = []
    onstack: Set[str] = set()
    idx: Dict[str, int] = {}
    low: Dict[str, int] = {}
    cycles: List[List[str]] = []

    def strongconnect(v: str):
        nonlocal index
        idx[v] = index
        low[v] = index
        index += 1
        stack.append(v)
        onstack.add(v)

        for w in g[v]:
            if w not in idx:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in onstack:
                low[v] = min(low[v], idx[w])

        if low[v] == idx[v]:
            scc = []
            while True:
                w = stack.pop()
                onstack.remove(w)
                scc.append(w)
                if w == v:
                    break
            # Only report cycles (SCCs with more than one node)
            if len(scc) > 1:
                cycles.append(list(reversed(scc)))

    for n in nodes:
        if n not in idx:
            strongconnect(n)

    return topo, cycles


# -----------------------------
# AST Visitors
# -----------------------------

class _ImportCollector(ast.NodeVisitor):
    """Collect all import statements from an AST."""
    
    def __init__(self):
        self.imports: List[Dict[str, Any]] = []

    def visit_Import(self, node: ast.Import):
        """Handle 'import x' statements."""
        for alias in node.names:
            self.imports.append({
                "kind": "import",
                "module": alias.name,
                "name": alias.asname or alias.name,
                "lineno": getattr(node, "lineno", None),
            })

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Handle 'from x import y' statements."""
        mod = node.module  # Can be None for relative imports like "from . import x"
        for alias in node.names:
            self.imports.append({
                "kind": "from",
                "module": mod,
                "level": node.level or 0,
                "name": alias.name,
                "asname": alias.asname,
                "lineno": getattr(node, "lineno", None),
            })


def _tokenize_code_advanced(text: str) -> List[str]:
    """
    高级代码分词，支持驼峰和下划线拆分
    """
    tokens = []
    
    # 提取标识符
    identifiers = re.findall(r'\b[A-Za-z_]\w*\b', text)
    
    for ident in identifiers:
        # 保留原始标识符
        tokens.append(ident.lower())
        
        # 驼峰拆分：MyClassName -> my, class, name
        camel_parts = re.sub('([A-Z][a-z]+)', r' \1', 
                            re.sub('([A-Z]+)', r' \1', ident)).split()
        tokens.extend([p.lower() for p in camel_parts if len(p) > 1])
        
        # 下划线拆分
        if '_' in ident:
            tokens.extend([p.lower() for p in ident.split('_') if p and len(p) > 1])
    
    return tokens

def _extract_docstring(node: ast.AST) -> Optional[str]:
    """提取函数/类的文档字符串"""
    try:
        return ast.get_docstring(node)
    except:
        return None

@dataclass
class CodeChunk:
    """代码片段结构"""
    chunk_id: str
    file_path: str
    module_name: str
    chunk_type: str  # function, class, method, file
    name: Optional[str]
    start_line: int
    end_line: int
    code: str
    docstring: Optional[str]
    signature: Optional[str]  # 函数签名

def _extract_code_chunks(root: Path, file_path: Path, max_chunk_lines: int = 100) -> List[CodeChunk]:
    """
    从文件中提取代码片段（函数、类、方法）
    """
    chunks = []
    text = _read_text_best_effort(file_path)
    
    if not text.strip():
        return chunks
    
    try:
        tree = ast.parse(text, filename=str(file_path))
    except:
        return chunks
    
    module_name = _module_name_from_path(root, file_path)
    lines = text.split('\n')
    
    class ChunkExtractor(ast.NodeVisitor):
        def __init__(self):
            self.current_class = None
            self.chunk_counter = 0
        
        def _get_code_snippet(self, node: ast.AST) -> str:
            start = getattr(node, 'lineno', 1) - 1
            end = getattr(node, 'end_lineno', start + 1)
            return '\n'.join(lines[start:end])
        
        def _get_signature(self, node: ast.FunctionDef) -> str:
            """提取函数签名"""
            args = []
            for arg in node.args.args:
                args.append(arg.arg)
            return f"{node.name}({', '.join(args)})"
        
        def visit_ClassDef(self, node: ast.ClassDef):
            chunk_id = f"{module_name}::{node.name}"
            code = self._get_code_snippet(node)
            docstring = _extract_docstring(node)
            
            chunks.append(CodeChunk(
                chunk_id=chunk_id,
                file_path=str(file_path),
                module_name=module_name,
                chunk_type="class",
                name=node.name,
                start_line=node.lineno,
                end_line=getattr(node, 'end_lineno', node.lineno),
                code=code,
                docstring=docstring,
                signature=f"class {node.name}"
            ))
            
            self.current_class = node.name
            self.generic_visit(node)
            self.current_class = None
        
        def visit_FunctionDef(self, node: ast.FunctionDef):
            if self.current_class:
                chunk_id = f"{module_name}::{self.current_class}.{node.name}"
                chunk_type = "method"
            else:
                chunk_id = f"{module_name}::{node.name}"
                chunk_type = "function"
            
            code = self._get_code_snippet(node)
            docstring = _extract_docstring(node)
            signature = self._get_signature(node)
            
            chunks.append(CodeChunk(
                chunk_id=chunk_id,
                file_path=str(file_path),
                module_name=module_name,
                chunk_type=chunk_type,
                name=node.name,
                start_line=node.lineno,
                end_line=getattr(node, 'end_lineno', node.lineno),
                code=code,
                docstring=docstring,
                signature=signature
            ))
            
            self.generic_visit(node)
        
        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            self.visit_FunctionDef(node)  # type: ignore
    
    extractor = ChunkExtractor()
    extractor.visit(tree)
    
    # 如果文件很小且没有提取到任何函数/类，添加整个文件作为一个chunk
    if not chunks and len(lines) < max_chunk_lines:
        chunks.append(CodeChunk(
            chunk_id=f"{module_name}::__file__",
            file_path=str(file_path),
            module_name=module_name,
            chunk_type="file",
            name=None,
            start_line=1,
            end_line=len(lines),
            code=text,
            docstring=None,
            signature=None
        ))
    
    return chunks



# -----------------------------
# Main Tool Class
# -----------------------------

class AnalysisTools(Tool):
    """
    Code analysis tools for Python projects.
    
    Provides module dependency graph construction with:
    - Static import analysis via AST
    - Cycle detection
    - Topological sorting
    - Internal/external module classification
    
    Limitations:
    - Only detects static imports (not dynamic imports via __import__ or importlib)
    - Cannot resolve imports that depend on runtime sys.path modifications
    - Heuristic-based internal/external classification
    """

    def __init__(
        self,
        item_id: str,
        name: str = "analysis_tools",
        description: str = "Code analysis tools for dependency graphs and impact analysis",
    ):
        super().__init__(name=name, description=description, category=ToolCategory.ANALYSIS)
        self.item_id = item_id
    
    @tool_function(
        description=(
            "Build a Python module dependency graph based on static AST import analysis. "
            "Detects import relationships, cycles, and provides topological ordering. "
            "NOTE: Only captures static imports; dynamic imports are not detected."
        ),
        parameters=[
            ToolParameter("root_path", "string", "Absolute path to project root", required=True),
            ToolParameter("file_pattern", "string", "Glob pattern for files (default: '**/*.py')", required=False, default="**/*.py"),
            ToolParameter("include_tests", "boolean", "Include test files in analysis", required=False, default=False),
            ToolParameter("collapse", "string", "Node granularity: 'module' (file-level) or 'package' (top-level package)", required=False, default="module"),
            ToolParameter("include_external", "boolean", "Include external/stdlib modules as nodes", required=False, default=False),
            ToolParameter("exclude_dirs", "array", "Additional directory names to exclude", required=False),
        ],
        returns=(
            "Dependency graph with nodes, edges, cycles, topological order, and warnings. "
            "Includes metadata about skipped files and duplicate modules."
        ),
        category=ToolCategory.ANALYSIS,
    )
    def analysis_tools__module_dependency_graph(
        self,
        root_path: str,
        file_pattern: str = "**/*.py",
        include_tests: bool = False,
        collapse: str = "module",
        include_external: bool = False,
        exclude_dirs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Build module dependency graph with comprehensive error handling and validation.
        
        Returns a dictionary with:
        - success: bool
        - result: dict containing graph data and warnings
        - error: str (only if success=False)
        """
        try:
            # Validate root path
            root = Path(root_path).resolve()
            if not root.exists():
                return {"success": False, "error": f"root_path does not exist: {root}"}
            if not root.is_dir():
                return {"success": False, "error": f"root_path is not a directory: {root}"}

            # Find all Python files
            files = _iter_py_files(root, file_pattern=file_pattern, exclude_dirs=exclude_dirs)
            
            # Filter test files if needed
            if not include_tests:
                original_count = len(files)
                files = [p for p in files if not _is_probably_test_file(p)]
                test_filtered_count = original_count - len(files)
            else:
                test_filtered_count = 0

            # Build module-to-file mapping with duplicate detection
            module_to_file: Dict[str, str] = {}
            duplicates = []
            
            for f in files:
                mod_name = _module_name_from_path(root, f)
                if mod_name in module_to_file:
                    duplicates.append({
                        "module": mod_name,
                        "files": [module_to_file[mod_name], str(f)]
                    })
                else:
                    module_to_file[mod_name] = str(f)

            # Define collapse function outside loop for efficiency
            if collapse == "package":
                def _collapse(m: str) -> str:
                    parts = m.split(".")
                    return parts[0] if parts else m
            else:
                _collapse = lambda m: m

            # Helper to check if a module is internal to the project
            def is_internal_module(resolved: str) -> bool:
                """Check if resolved module name exists in project."""
                if resolved in module_to_file:
                    return True
                # Check if it's a parent package of any project module
                # e.g., "myapp" is internal if "myapp.utils" exists
                prefix = resolved + "."
                return any(k.startswith(prefix) for k in module_to_file.keys())

            # Collect all nodes and edges
            nodes_set: Set[str] = set(module_to_file.keys())
            edges: List[Tuple[str, str, Dict[str, Any]]] = []
            skipped_files = []
            invalid_imports = []

            # Process each file
            for f in files:
                mod = _module_name_from_path(root, f)
                text = _read_text_best_effort(f)
                
                if not text.strip():
                    skipped_files.append({
                        "file": str(f),
                        "reason": "empty_or_unreadable"
                    })
                    continue
                
                # Parse AST
                try:
                    tree = ast.parse(text, filename=str(f))
                except SyntaxError as e:
                    skipped_files.append({
                        "file": str(f),
                        "reason": "syntax_error",
                        "error": f"Line {e.lineno}: {str(e.msg)[:100]}"
                    })
                    continue
                except Exception as e:
                    skipped_files.append({
                        "file": str(f),
                        "reason": "parse_error",
                        "error": str(e)[:100]
                    })
                    continue

                # Collect imports
                ic = _ImportCollector()
                ic.visit(tree)

                # Process each import
                for it in ic.imports:
                    if it["kind"] == "import":
                        # Simple import: import foo.bar
                        resolved = it["module"]
                    else:
                        # Relative or absolute from-import
                        resolved = _resolve_import_from(
                            mod, 
                            it.get("level", 0), 
                            it.get("module")
                        )
                        
                        # Track invalid imports
                        if resolved.startswith("<invalid_relative_import") or resolved.startswith("<empty_module"):
                            invalid_imports.append({
                                "file": str(f),
                                "line": it.get("lineno"),
                                "module": mod,
                                "import_statement": it,
                                "resolved": resolved
                            })
                            continue  # Skip invalid imports

                    # Apply collapse (module vs package level)
                    src = _collapse(mod)
                    dst = _collapse(resolved)

                    # Filter external modules if requested
                    if not include_external and not is_internal_module(resolved):
                        continue

                    # Add nodes
                    nodes_set.add(src)
                    nodes_set.add(dst)
                    
                    # Add edge with metadata
                    is_self_loop = (src == dst)
                    edges.append((src, dst, {
                        "kind": it["kind"],
                        "lineno": it.get("lineno"),
                        "raw_module": it.get("module"),
                        "level": it.get("level", 0),
                        "file": str(f),
                        "is_self_loop": is_self_loop,
                        "is_internal": is_internal_module(resolved),
                        "resolved_module": resolved,
                    }))

            # Prepare topological sort (exclude self-loops to avoid trivial cycles)
            nodes = sorted(nodes_set)
            edge_pairs = [(a, b) for (a, b, _) in edges if a != b]
            topo, cycles = _topological_sort(nodes, edge_pairs)

            # Count various edge types
            self_loop_count = sum(1 for (a, b, _) in edges if a == b)
            internal_edge_count = sum(1 for (_, _, meta) in edges if meta.get("is_internal", False))
            external_edge_count = len(edges) - internal_edge_count

            # Prepare result
            return {
                "success": True,
                "result": {
                    "root_path": str(root),
                    "file_count": len(files),
                    "collapse": collapse,
                    "nodes": nodes,
                    "edges": [
                        {"from": a, "to": b, "evidence": meta} 
                        for (a, b, meta) in edges
                    ],
                    "cycles": cycles,
                    "topo_order": topo,
                    "module_to_file": module_to_file,
                    "statistics": {
                        "total_nodes": len(nodes),
                        "total_edges": len(edges),
                        "internal_edges": internal_edge_count,
                        "external_edges": external_edge_count,
                        "self_loops": self_loop_count,
                        "cycles_detected": len(cycles),
                        "test_files_filtered": test_filtered_count,
                    },
                    "warnings": {
                        "skipped_files": skipped_files[:50],  # Limit to first 50
                        "skipped_files_total": len(skipped_files),
                        "duplicate_modules": duplicates,
                        "invalid_imports": invalid_imports[:50],  # Limit to first 50
                        "invalid_imports_total": len(invalid_imports),
                    },
                },
            }
            
        except Exception as e:
            import traceback
            return {
                "success": False, 
                "error": str(e),
                "traceback": traceback.format_exc()
            }

    @tool_function(
        description=(
            "Semantic code search using CodeRankEmbed (nomic-ai). "
            "Searches for code snippets by meaning using specialized code embeddings. "
            "Supports natural language queries like 'function that opens a file' or 'database connection handler'. "
            "Query will be automatically prefixed for optimal results."
        ),
        parameters=[
            ToolParameter("root_path", "string", "Absolute path to project root", required=True),
            ToolParameter("query", "string", "Natural language search query", required=True),
            ToolParameter("top_k", "integer", "Number of results to return (default: 10)", required=False, default=10),
            ToolParameter("chunk_type", "string", "Filter by type: all, function, class, method, file", required=False, default="all"),
            ToolParameter("exclude_tests", "boolean", "Exclude test files from results", required=False, default=True),
            ToolParameter("exclude_dirs", "array", "Additional directory names to exclude", required=False),
            ToolParameter("min_similarity", "number", "Minimum cosine similarity (0-1, default: 0.3)", required=False, default=0.3),
            ToolParameter("use_cache", "boolean", "Use cached embeddings if available", required=False, default=True),
            ToolParameter("rebuild_cache", "boolean", "Force rebuild embeddings cache", required=False, default=False),
        ],
        returns=(
            "Ranked search results with similarity scores, code snippets, and metadata. "
            "Results are sorted by semantic similarity to the query."
        ),
        category=ToolCategory.ANALYSIS,
    )
    def analysis_tools__semantic_search(
        self,
        root_path: str,
        query: str,
        top_k: int = 10,
        chunk_type: str = "all",
        exclude_tests: bool = True,
        exclude_dirs: Optional[List[str]] = None,
        min_similarity: float = 0.3,
        use_cache: bool = True,
        rebuild_cache: bool = False,
    ) -> Dict[str, Any]:
        """
        Semantic code search using CodeRankEmbed from nomic-ai.
        
        This specialized code embedding model provides better results for code search
        compared to general-purpose models. It understands code structure and semantics.
        
        Installation:
            pip install sentence-transformers
        
        The query will be automatically prefixed with:
            "Represent this query for searching relevant code: <your query>"
        """
        try:
            # Lazy import to avoid dependency if not used
            try:
                from sentence_transformers import SentenceTransformer
                import numpy as np
            except ImportError:
                return {
                    "success": False,
                    "error": (
                        "sentence-transformers not installed. "
                        "Run: pip install sentence-transformers"
                    )
                }
            
            # Validate root path
            root = Path(root_path).resolve()
            if not root.exists():
                return {"success": False, "error": f"root_path does not exist: {root}"}
            if not root.is_dir():
                return {"success": False, "error": f"root_path is not a directory: {root}"}
            
            # Initialize cache directory
            cache_dir = root / ".agent_cache"
            cache_dir.mkdir(exist_ok=True)
            
            model_name = "nomic-ai/CodeRankEmbed"
            embeddings_cache_path = cache_dir / "code_embeddings.npz"
            chunks_cache_path = cache_dir / "code_chunks.json"
            
            # Force rebuild cache if requested
            if rebuild_cache:
                if embeddings_cache_path.exists():
                    embeddings_cache_path.unlink()
                if chunks_cache_path.exists():
                    chunks_cache_path.unlink()
            
            # Try to load from cache
            chunks: List[CodeChunk] = []
            embeddings = None
            cache_loaded = False
            
            if use_cache and not rebuild_cache:
                if embeddings_cache_path.exists() and chunks_cache_path.exists():
                    try:
                        # Load chunks
                        with open(chunks_cache_path, 'r', encoding='utf-8') as f:
                            chunks_data = json.load(f)
                            chunks = [CodeChunk(**c) for c in chunks_data]
                        
                        # Load embeddings
                        cache_data = np.load(embeddings_cache_path)
                        embeddings = cache_data['embeddings']
                        
                        cache_loaded = True
                    except Exception as e:
                        # Cache corrupted, will rebuild
                        chunks = []
                        embeddings = None
            
            # Extract code chunks if not cached
            if not chunks:
                files = _iter_py_files(root, exclude_dirs=exclude_dirs)
                
                if exclude_tests:
                    files = [f for f in files if not _is_probably_test_file(f)]
                
                if not files:
                    return {
                        "success": False,
                        "error": "No Python files found in project"
                    }
                
                # Extract chunks from all files
                for f in files:
                    file_chunks = _extract_code_chunks(root, f)
                    chunks.extend(file_chunks)
                
                if not chunks:
                    return {
                        "success": False,
                        "error": "No code chunks extracted from project. Files may be empty or have syntax errors."
                    }
                
                # Save chunks to cache
                try:
                    chunks_data = [
                        {
                            "chunk_id": c.chunk_id,
                            "file_path": c.file_path,
                            "module_name": c.module_name,
                            "chunk_type": c.chunk_type,
                            "name": c.name,
                            "start_line": c.start_line,
                            "end_line": c.end_line,
                            "code": c.code,
                            "docstring": c.docstring,
                            "signature": c.signature,
                        }
                        for c in chunks
                    ]
                    with open(chunks_cache_path, 'w', encoding='utf-8') as f:
                        json.dump(chunks_data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    # Non-critical, continue without cache
                    pass
            
            # Load CodeRankEmbed model
            try:
                model = SentenceTransformer(model_name, trust_remote_code=True)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to load model {model_name}: {str(e)}. Ensure sentence-transformers is installed."
                }
            
            # Compute code embeddings if not cached
            if embeddings is None:
                # Prepare code texts for embedding
                code_texts = []
                for chunk in chunks:
                    # Combine signature, docstring, and code for better context
                    text_parts = []
                    
                    if chunk.signature:
                        text_parts.append(chunk.signature)
                    
                    if chunk.docstring:
                        text_parts.append(f'"""{chunk.docstring}"""')
                    
                    # Add code (limit length to avoid memory issues)
                    code_preview = chunk.code[:1000] if chunk.code else ""
                    text_parts.append(code_preview)
                    
                    combined_text = '\n'.join(text_parts)
                    code_texts.append(combined_text)
                
                # Encode all code chunks
                try:
                    embeddings = model.encode(
                        code_texts,
                        show_progress_bar=False,
                        convert_to_numpy=True,
                        batch_size=32
                    )
                except Exception as e:
                    return {
                        "success": False,
                        "error": f"Failed to compute code embeddings: {str(e)}"
                    }
                
                # Save embeddings to cache
                try:
                    np.savez_compressed(embeddings_cache_path, embeddings=embeddings)
                except Exception as e:
                    # Non-critical, continue without cache
                    pass
            
            # Prepare query with recommended prefix
            if not query.startswith("Represent this query"):
                query_with_prefix = f"Represent this query for searching relevant code: {query}"
            else:
                query_with_prefix = query
            
            # Encode query
            try:
                query_embedding = model.encode([query_with_prefix], convert_to_numpy=True)[0]
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to encode query: {str(e)}"
                }
            
            # Compute cosine similarities
            # Cosine similarity = dot product of normalized vectors
            embeddings_norm = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
            query_norm = query_embedding / np.linalg.norm(query_embedding)
            similarities = np.dot(embeddings_norm, query_norm)
            
            # Filter and collect results
            results = []
            for idx, similarity in enumerate(similarities):
                # Skip low similarity results
                if similarity < min_similarity:
                    continue
                
                chunk = chunks[idx]
                
                # Apply chunk type filter
                if chunk_type != "all" and chunk.chunk_type != chunk_type:
                    continue
                
                # Prepare code preview (limit to 500 chars)
                code_preview = chunk.code
                if len(code_preview) > 500:
                    code_preview = code_preview[:500] + "\n... [truncated]"
                
                results.append({
                    "chunk_id": chunk.chunk_id,
                    "similarity": float(similarity),
                    "file_path": chunk.file_path,
                    "module_name": chunk.module_name,
                    "type": chunk.chunk_type,
                    "name": chunk.name,
                    "signature": chunk.signature,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "docstring": chunk.docstring,
                    "code_preview": code_preview,
                })
            
            # Sort by similarity (descending) and take top_k
            results.sort(key=lambda x: x["similarity"], reverse=True)
            results = results[:top_k]
            
            return {
                "success": True,
                "result": {
                    "query": query,
                    "query_with_prefix": query_with_prefix,
                    "total_chunks_searched": len(chunks),
                    "matching_chunks": len(results),
                    "top_k": top_k,
                    "min_similarity": min_similarity,
                    "chunk_type_filter": chunk_type,
                    "model_used": model_name,
                    "cache_used": cache_loaded,
                    "results": results,
                },
            }
            
        except Exception as e:
            import traceback
            return {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            }
