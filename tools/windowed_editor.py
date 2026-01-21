"""
Windowed File Editor Tool for SafeFlow - Atomic Operations Edition

Provides SWE-agent style windowed file operations as pure atomic functions.
No internal state - LLM manages file paths, line positions, and window parameters.
"""

import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple
from tools.abs_tools import Tool, ToolCategory, ToolParameter, tool_function

logger = logging.getLogger(__name__)


class WindowedEditorTool(Tool):
    """
    Atomic Windowed File Editor - Stateless Operations.

    Every function is atomic and requires explicit parameters.
    LLM manages current file, window position, and window size.
    """

    def __init__(
        self,
        item_id: str,
        name: str = "windowed_editor",
        description: str = "Atomic windowed file operations (stateless)",
    ):
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.WINDOWED_EDITOR
        )

        self.item_id = item_id

    def _validate_absolute_path(self, path: str) -> Path:
        """Validate absolute path and return resolved Path object."""
        p = Path(path)
        if not p.is_absolute():
            raise ValueError(f"Path must be absolute, got: {path}")
        return p.resolve()

    def _get_window_content(
        self,
        file_path: Path,
        start_line: int,
        window_size: int
    ) -> str:
        """Get formatted window content with line numbers and context."""
        if not file_path.exists():
            return f"File does not exist: {file_path}"

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()

        if not lines:
            lines = [""]

        total_lines = len(lines)

        # Ensure start_line is valid
        start_line = max(0, min(start_line, total_lines - 1))
        end_line = min(start_line + window_size - 1, total_lines - 1)

        window_lines = lines[start_line:end_line + 1]

        output_lines = []

        # File header
        output_lines.append(f"[File: {file_path} ({total_lines} lines total)]")

        # Lines above indicator
        if start_line > 0:
            output_lines.append(f"({start_line} more lines above)")

        # Window content with line numbers
        for i, line in enumerate(window_lines):
            line_num = start_line + i + 1  # 1-indexed for display
            output_lines.append(f"{line_num:4d}:{line}")

        # Lines below indicator
        if end_line < total_lines - 1:
            remaining = total_lines - end_line - 1
            output_lines.append(f"({remaining} more lines below)")

        return "\n".join(output_lines)

    @tool_function(
        description='Shows a window view of the file. Creates file if it does not exist.',
        parameters=[
            ToolParameter("path", "string", "Absolute path to the file", required=True),
            ToolParameter("start_line", "integer", "Starting line of window (0-indexed)", required=False, default=0),
            ToolParameter("window_size", "integer", "Number of lines in window", required=False, default=50),
        ],
        returns="Window view of file content",
        category=ToolCategory.WINDOWED_EDITOR,
    )
    def windowed_editor__view(
        self,
        path: str,
        start_line: int = 0,
        window_size: int = 50
    ) -> Dict[str, Any]:
        """Show window view of file. Creates file if it doesn't exist."""
        try:
            file_path = self._validate_absolute_path(path)

            # Create file if it doesn't exist
            if not file_path.exists():
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text("", encoding="utf-8")

            if file_path.is_dir():
                return {"success": False, "error": f"Path is a directory: {file_path}"}

            content = self._get_window_content(file_path, start_line, window_size)

            result = {
                "content": content,
                "file_path": str(file_path),
                "window_info": {
                    "start_line": start_line,
                    "window_size": window_size,
                    "total_lines": len(file_path.read_text(encoding="utf-8", errors="replace").splitlines()),
                }
            }

            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": f"Failed to view file: {e}"}

    @tool_function(
        description="Replace lines in a file and return updated window view.",
        parameters=[
            ToolParameter("path", "string", "Absolute path to the file", required=True),
            ToolParameter("start_line", "integer", "Line to start edit (1-indexed)", required=True),
            ToolParameter("end_line", "integer", "Line to end edit (1-indexed, inclusive)", required=True),
            ToolParameter("replacement_text", "string", "Text to replace lines with", required=True),
            ToolParameter("view_start", "integer", "Starting line for return view (0-indexed)", required=False),
            ToolParameter("view_size", "integer", "Window size for return view", required=False, default=50),
        ],
        returns="Edit result with updated window view",
        category=ToolCategory.WINDOWED_EDITOR,
    )
    def windowed_editor__edit_lines(
        self,
        path: str,
        start_line: int,
        end_line: int,
        replacement_text: str,
        view_start: Optional[int] = None,
        view_size: int = 50
    ) -> Dict[str, Any]:
        """Replace line range with new text."""
        try:
            file_path = self._validate_absolute_path(path)

            if not file_path.exists():
                return {"success": False, "error": f"File does not exist: {file_path}"}

            content = file_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()

            # Convert to 0-indexed
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines) - 1, end_line - 1)

            if start_idx > len(lines):
                return {"success": False, "error": f"Start line {start_line} beyond file length {len(lines)}"}

            # Replace lines
            replacement_lines = replacement_text.split('\n')
            new_lines = lines[:start_idx] + replacement_lines + lines[end_idx + 1:]

            file_path.write_text('\n'.join(new_lines), encoding="utf-8")

            # Determine view window (auto-position around edit if not specified)
            if view_start is None:
                view_start = max(0, start_idx - 5)

            content = self._get_window_content(file_path, view_start, view_size)

            result = {
                "content": content,
                "edit_info": {
                    "start_line": start_line,
                    "end_line": end_line,
                    "lines_replaced": end_idx - start_idx + 1,
                    "new_lines_count": len(replacement_lines),
                },
                "window_info": {
                    "start_line": view_start,
                    "window_size": view_size,
                }
            }

            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": f"Edit failed: {e}"}

    @tool_function(
        description="Search and replace text in entire file, return window view.",
        parameters=[
            ToolParameter("path", "string", "Absolute path to the file", required=True),
            ToolParameter("search", "string", "Text to search for", required=True),
            ToolParameter("replace", "string", "Text to replace with", required=True),
            ToolParameter("replace_all", "boolean", "Replace all occurrences", required=False, default=True),
            ToolParameter("view_start", "integer", "Starting line for return view (0-indexed)", required=False),
            ToolParameter("view_size", "integer", "Window size for return view", required=False, default=50),
        ],
        returns="Replace result with updated window view",
        category=ToolCategory.WINDOWED_EDITOR,
    )
    def windowed_editor__str_replace(
        self,
        path: str,
        search: str,
        replace: str,
        replace_all: bool = True,
        view_start: Optional[int] = None,
        view_size: int = 50
    ) -> Dict[str, Any]:
        """Search and replace in entire file."""
        try:
            file_path = self._validate_absolute_path(path)

            if not file_path.exists():
                return {"success": False, "error": f"File does not exist: {file_path}"}

            content = file_path.read_text(encoding="utf-8", errors="replace")

            if search not in content:
                return {"success": False, "error": f"Text not found in file: '{search}'"}

            # Find first occurrence for auto-positioning
            first_occurrence_pos = content.find(search)
            lines_before = content[:first_occurrence_pos].splitlines()
            first_match_line = len(lines_before)

            # Replace in entire file
            if replace_all:
                new_content = content.replace(search, replace)
                replacement_count = content.count(search)
            else:
                new_content = content.replace(search, replace, 1)
                replacement_count = 1

            file_path.write_text(new_content, encoding="utf-8")

            # Determine view window (auto-position around first match if not specified)
            if view_start is None:
                view_start = max(0, first_match_line - view_size // 3)

            content = self._get_window_content(file_path, view_start, view_size)

            result = {
                "content": content,
                "replace_info": {
                    "search_text": search,
                    "replacement_text": replace,
                    "replacements_made": replacement_count,
                    "first_match_line": first_match_line + 1,  # 1-indexed for display
                },
                "window_info": {
                    "start_line": view_start,
                    "window_size": view_size,
                }
            }

            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": f"Replace failed: {e}"}

    @tool_function(
        description="Insert text in file and return window view.",
        parameters=[
            ToolParameter("path", "string", "Absolute path to the file", required=True),
            ToolParameter("text", "string", "Text to insert", required=True),
            ToolParameter("line", "integer", "Line to insert after (1-indexed). If not provided, appends to end.", required=False),
            ToolParameter("view_start", "integer", "Starting line for return view (0-indexed)", required=False),
            ToolParameter("view_size", "integer", "Window size for return view", required=False, default=50),
        ],
        returns="Insert result with updated window view",
        category=ToolCategory.WINDOWED_EDITOR,
    )
    def windowed_editor__insert(
        self,
        path: str,
        text: str,
        line: Optional[int] = None,
        view_start: Optional[int] = None,
        view_size: int = 50
    ) -> Dict[str, Any]:
        """Insert text at specified location or end of file."""
        try:
            file_path = self._validate_absolute_path(path)

            if not file_path.exists():
                return {"success": False, "error": f"File does not exist: {file_path}"}

            content = file_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()

            insert_lines = text.rstrip('\n').split('\n')

            if line is None:
                # Append to end
                new_lines = lines + insert_lines
                insert_pos = len(lines)
            else:
                # Insert after line
                insert_idx = max(0, min(line, len(lines)))
                new_lines = lines[:insert_idx] + insert_lines + lines[insert_idx:]
                insert_pos = insert_idx

            file_path.write_text('\n'.join(new_lines), encoding="utf-8")

            # Determine view window (auto-position around insertion if not specified)
            if view_start is None:
                view_start = max(0, insert_pos - 5)

            content = self._get_window_content(file_path, view_start, view_size)

            result = {
                "content": content,
                "insert_info": {
                    "insertion_line": insert_pos + 1,  # 1-indexed for display
                    "lines_added": len(insert_lines),
                },
                "window_info": {
                    "start_line": view_start,
                    "window_size": view_size,
                }
            }

            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": f"Insert failed: {e}"}

    @tool_function(
        description="Get file information including absolute path and total line count. Useful for file status checks.",
        parameters=[
            ToolParameter("path", "string", "Absolute path to the file", required=True),
        ],
        returns="File path information and line count",
        category=ToolCategory.WINDOWED_EDITOR,
    )
    def windowed_editor__get_file_info(self, path: str) -> Dict[str, Any]:
        """Get file information including resolved absolute path and line count."""
        try:
            file_path = self._validate_absolute_path(path)

            result = {
                "absolute_path": str(file_path),
                "exists": file_path.exists(),
            }

            if file_path.exists():
                if file_path.is_file():
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="replace")
                        lines = content.splitlines()

                        result.update({
                            "type": "file",
                            "total_lines": len(lines),
                            "size_bytes": file_path.stat().st_size,
                            "extension": file_path.suffix,
                            "name": file_path.name,
                            "parent_dir": str(file_path.parent),
                            "is_empty": len(lines) == 0 or (len(lines) == 1 and lines[0] == "")
                        })
                    except Exception as e:
                        result.update({
                            "type": "file",
                            "error": f"Could not read file: {e}"
                        })
                else:
                    result.update({
                        "type": "directory",
                        "error": "Path is a directory, not a file"
                    })
            else:
                result.update({
                    "type": "nonexistent",
                    "parent_dir": str(file_path.parent),
                    "parent_exists": file_path.parent.exists(),
                    "suggested_name": file_path.name
                })

            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": f"Failed to get file info: {e}"}

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "item_id": self.item_id,
            "functions": len(self.functions) if hasattr(self, 'functions') else 0,
        }