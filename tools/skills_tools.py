from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from tools.abs_tools import Tool, ToolCategory, ToolParameter, tool_function


@dataclass
class SkillsToolConfig:
    trigger_yaml_path: Optional[str] = None
    trigger_key: str = "current_trigger"
    base_description: str = "Tools for activating specialized skill stacks."
    function_description_template: str = (
        "Activate the skill router to load a domain-specific skill stack and its "
        "tool preferences. Use when a task requires skill-aware routing or "
        "preloaded instructions. {trigger_hint}"
    )


class SkillsTools(Tool):
    """
    Tools that explicitly activate skill stacks. Descriptions can be augmented
    with optimized triggers stored in a YAML file.
    """

    def __init__(self, item_id: str, config: Optional[SkillsToolConfig] = None) -> None:
        self.item_id = item_id
        self.config = config or SkillsToolConfig()
        super().__init__(
            name="skills_tools",
            description=self.config.base_description,
            category=ToolCategory.SKILLS,
        )
        self._apply_trigger_overrides()

    def _apply_trigger_overrides(self) -> None:
        trigger_text = None
        if self.config.trigger_yaml_path:
            path = Path(self.config.trigger_yaml_path)
            if path.exists():
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                trigger_text = data.get(self.config.trigger_key)

        if trigger_text:
            trigger_hint = f"Trigger tokens: {trigger_text}"
        else:
            trigger_hint = "Trigger tokens: (none configured)"

        for func in self.functions.values():
            func.description = self.config.function_description_template.format(
                trigger_hint=trigger_hint
            )

    @tool_function(
        description="Activate a skill router for a named skill stack.",
        parameters=[
            ToolParameter("skill_name", "string", "Name of the skill stack to load."),
            ToolParameter("reason", "string", "Why this skill stack is needed for the task."),
        ],
        returns="Activation status and metadata for the requested skill stack.",
        category=ToolCategory.SKILLS,
    )
    def skills_tools__activate_skill(self, skill_name: str, reason: str) -> Dict[str, Any]:
        return {
            "status": "activated",
            "skill_name": skill_name,
            "reason": reason,
            "item_id": self.item_id,
        }
