"""
Planning Tool for SafeFlow - Plan with Files

Provides atomic planning operations for agents to:
- Create and manage execution plans
- Track progress against plans
- Update plans based on feedback
- Maintain planning context across sessions

All operations are stateless and work with absolute paths.
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from tools.abs_tools import Tool, ToolCategory, ToolParameter, tool_function

logger = logging.getLogger(__name__)


class PlanningTool(Tool):
    """
    Atomic Planning Tool for SafeFlow.

    Manages task planning and execution tracking through file-based persistence.
    Supports iterative plan refinement and progress monitoring.
    """

    def __init__(
        self,
        item_id: str,
        name: str = "planning_tool",
        description: str = "Task planning and progress management",
    ):
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.PLAN_TOOLS
        )

        self.item_id = item_id

    def _validate_absolute_path(self, path: str) -> Path:
        """Validate absolute path and return resolved Path object."""
        p = Path(path)
        if not p.is_absolute():
            raise ValueError(f"Path must be absolute, got: {path}")
        return p.resolve()


    def _get_timestamp(self) -> str:
        """Get current timestamp string."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _load_plan(self, plan_file: Path) -> Optional[Dict[str, Any]]:
        """Load existing plan from file."""
        if not plan_file.exists():
            return None

        try:
            with open(plan_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load plan from {plan_file}: {e}")
            return None

    def _save_plan(self, plan_file: Path, plan_data: Dict[str, Any]) -> None:
        """Save plan to file."""
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        with open(plan_file, 'w', encoding='utf-8') as f:
            json.dump(plan_data, f, ensure_ascii=False, indent=2)

    @tool_function(
        description="Create a new execution plan for the current task.",
        parameters=[
            ToolParameter("plan_file", "string", "Absolute path to plan file", required=True),
            ToolParameter("task_description", "string", "High-level description of the task", required=True),
            ToolParameter("overall_goal", "string", "The main objective to accomplish", required=True),
            ToolParameter("steps", "array", "List of planned execution steps", required=True),
            ToolParameter("context_info", "string", "Additional context about the task", required=False, default=""),
        ],
        returns="Plan creation result",
        category=ToolCategory.PLAN_TOOLS,
    )
    def planning_tool__create_plan(
        self,
        plan_file: str,
        task_description: str,
        overall_goal: str,
        steps: List[str],
        context_info: str = ""
    ) -> Dict[str, Any]:
        """Create a new execution plan and save to file."""
        try:
            plan_path = self._validate_absolute_path(plan_file)

            # Check if plan already exists
            existing_plan = self._load_plan(plan_path)
            if existing_plan:
                return {
                    "success": False,
                    "error": f"Plan already exists at {plan_path}. Use update_plan to modify or view_plan to see current plan."
                }

            # Create new plan structure
            plan_data = {
                "metadata": {
                    "created_at": self._get_timestamp(),
                    "updated_at": self._get_timestamp(),
                    "item_id": self.item_id,
                    "version": 1,
                },
                "task": {
                    "description": task_description,
                    "overall_goal": overall_goal,
                    "context_info": context_info,
                },
                "execution": {
                    "steps": [
                        {
                            "id": i + 1,
                            "description": step,
                            "status": "pending",  # pending, in_progress, completed, failed
                            "notes": "",
                            "completed_at": None,
                        }
                        for i, step in enumerate(steps)
                    ],
                    "current_step": 1,
                    "overall_status": "active",  # active, paused, completed, failed
                },
                "history": [
                    {
                        "timestamp": self._get_timestamp(),
                        "action": "plan_created",
                        "description": f"Initial plan created with {len(steps)} steps",
                    }
                ]
            }

            # Save plan
            self._save_plan(plan_path, plan_data)

            result = {
                "plan_file": str(plan_path),
                "steps_count": len(steps),
                "current_step": 1,
                "plan_content": "\n".join([f"{i + 1}. {step}" for i, step in enumerate(steps)]),
                "plan_summary": {
                    "task": task_description,
                    "goal": overall_goal,
                    "total_steps": len(steps),
                    "first_step": steps[0] if steps else None,
                }
            }

            logger.info(f"Created plan with {len(steps)} steps: {plan_path}")
            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": f"Failed to create plan: {e}"}

    @tool_function(
        description="View the current execution plan and progress.",
        parameters=[
            ToolParameter("plan_file", "string", "Absolute path to plan file", required=True),
            ToolParameter("show_completed", "boolean", "Include completed steps in output", required=False, default=True),
        ],
        returns="Current plan and progress status",
        category=ToolCategory.PLAN_TOOLS,
    )
    def planning_tool__view_plan(
        self,
        plan_file: str,
        show_completed: bool = True
    ) -> Dict[str, Any]:
        """View current plan and progress status."""
        try:
            plan_path = self._validate_absolute_path(plan_file)

            plan_data = self._load_plan(plan_path)
            if not plan_data:
                return {"success": False, "error": f"No plan found at {plan_path}"}

            task = plan_data.get("task", {})
            execution = plan_data.get("execution", {})
            steps = execution.get("steps", [])

            # Filter steps based on show_completed
            if show_completed:
                display_steps = steps
            else:
                display_steps = [step for step in steps if step.get("status") != "completed"]

            # Calculate progress
            completed_count = len([s for s in steps if s.get("status") == "completed"])
            total_count = len(steps)
            progress_percentage = (completed_count / total_count * 100) if total_count > 0 else 0

            # Get current step info
            current_step_id = execution.get("current_step", 1)
            current_step = next((s for s in steps if s.get("id") == current_step_id), None)

            result = {
                "plan_file": str(plan_path),
                "task_info": {
                    "description": task.get("description", ""),
                    "overall_goal": task.get("overall_goal", ""),
                    "context_info": task.get("context_info", ""),
                },
                "progress": {
                    "completed_steps": completed_count,
                    "total_steps": total_count,
                    "percentage": round(progress_percentage, 1),
                    "current_step_id": current_step_id,
                    "overall_status": execution.get("overall_status", "active"),
                },
                "current_step": current_step,
                "steps": display_steps,
                "metadata": plan_data.get("metadata", {}),
            }

            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": f"Failed to view plan: {e}"}

    @tool_function(
        description="Update plan progress or modify plan content.",
        parameters=[
            ToolParameter("plan_file", "string", "Absolute path to plan file", required=True),
            ToolParameter("action", "string", "Action to perform: complete_step, fail_step, add_step, update_step, set_current_step", required=True),
            ToolParameter("step_id", "integer", "ID of step to operate on", required=False),
            ToolParameter("step_description", "string", "New or updated step description", required=False),
            ToolParameter("notes", "string", "Notes about the step or update", required=False, default=""),
        ],
        returns="Plan update result",
        category=ToolCategory.PLAN_TOOLS,
    )
    def planning_tool__update_plan(
        self,
        plan_file: str,
        action: str,
        step_id: Optional[int] = None,
        step_description: Optional[str] = None,
        notes: str = ""
    ) -> Dict[str, Any]:
        """Update plan progress or modify plan content."""
        try:
            plan_path = self._validate_absolute_path(plan_file)

            plan_data = self._load_plan(plan_path)
            if not plan_data:
                return {"success": False, "error": f"No plan found at {plan_path}"}

            execution = plan_data.get("execution", {})
            steps = execution.get("steps", [])
            history = plan_data.get("history", [])

            timestamp = self._get_timestamp()

            if action == "complete_step":
                if step_id is None:
                    return {"success": False, "error": "step_id required for complete_step action"}

                # Find and update step
                step = next((s for s in steps if s.get("id") == step_id), None)
                if not step:
                    return {"success": False, "error": f"Step {step_id} not found"}

                step["status"] = "completed"
                step["completed_at"] = timestamp
                if notes:
                    step["notes"] = notes

                # Move to next step if this was the current step
                if execution.get("current_step") == step_id:
                    next_step = next((s for s in steps if s.get("status") == "pending"), None)
                    execution["current_step"] = next_step.get("id") if next_step else step_id

                history.append({
                    "timestamp": timestamp,
                    "action": "step_completed",
                    "description": f"Completed step {step_id}: {step.get('description', '')}",
                    "notes": notes,
                })

            elif action == "fail_step":
                if step_id is None:
                    return {"success": False, "error": "step_id required for fail_step action"}

                step = next((s for s in steps if s.get("id") == step_id), None)
                if not step:
                    return {"success": False, "error": f"Step {step_id} not found"}

                step["status"] = "failed"
                step["completed_at"] = timestamp
                if notes:
                    step["notes"] = notes

                history.append({
                    "timestamp": timestamp,
                    "action": "step_failed",
                    "description": f"Failed step {step_id}: {step.get('description', '')}",
                    "notes": notes,
                })

            elif action == "add_step":
                if not step_description:
                    return {"success": False, "error": "step_description required for add_step action"}

                new_id = max([s.get("id", 0) for s in steps], default=0) + 1
                new_step = {
                    "id": new_id,
                    "description": step_description,
                    "status": "pending",
                    "notes": notes,
                    "completed_at": None,
                }
                steps.append(new_step)

                history.append({
                    "timestamp": timestamp,
                    "action": "step_added",
                    "description": f"Added new step {new_id}: {step_description}",
                    "notes": notes,
                })

            elif action == "set_current_step":
                if step_id is None:
                    return {"success": False, "error": "step_id required for set_current_step action"}

                step = next((s for s in steps if s.get("id") == step_id), None)
                if not step:
                    return {"success": False, "error": f"Step {step_id} not found"}

                execution["current_step"] = step_id

                history.append({
                    "timestamp": timestamp,
                    "action": "current_step_changed",
                    "description": f"Changed current step to {step_id}: {step.get('description', '')}",
                    "notes": notes,
                })

            else:
                return {"success": False, "error": f"Unknown action: {action}"}

            # Update metadata
            plan_data["metadata"]["updated_at"] = timestamp
            plan_data["metadata"]["version"] = plan_data.get("metadata", {}).get("version", 0) + 1

            # Save updated plan
            self._save_plan(plan_path, plan_data)

            result = {
                "action_performed": action,
                "step_id": step_id,
                "plan_updated": True,
                "current_step": execution.get("current_step"),
                "total_steps": len(steps),
            }

            logger.info(f"Updated plan: {action} for step {step_id}")
            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": f"Failed to update plan: {e}"}

    @tool_function(
        description="Create a new plan version based on human feedback or task changes.",
        parameters=[
            ToolParameter("plan_file", "string", "Absolute path to plan file", required=True),
            ToolParameter("feedback", "string", "Human feedback or new requirements", required=True),
            ToolParameter("new_steps", "array", "Updated list of execution steps", required=False),
            ToolParameter("keep_completed", "boolean", "Keep already completed steps", required=False, default=True),
        ],
        returns="Plan revision result",
        category=ToolCategory.PLAN_TOOLS,
    )
    def planning_tool__revise_plan(
        self,
        plan_file: str,
        feedback: str,
        new_steps: Optional[List[str]] = None,
        keep_completed: bool = True
    ) -> Dict[str, Any]:
        """Revise plan based on feedback or changed requirements."""
        try:
            plan_path = self._validate_absolute_path(plan_file)

            plan_data = self._load_plan(plan_path)
            if not plan_data:
                return {"success": False, "error": f"No plan found at {plan_path}"}

            timestamp = self._get_timestamp()

            # Create backup of current steps
            current_steps = plan_data.get("execution", {}).get("steps", [])

            # Keep completed steps if requested
            if keep_completed:
                completed_steps = [s for s in current_steps if s.get("status") == "completed"]
                max_completed_id = max([s.get("id", 0) for s in completed_steps], default=0)
            else:
                completed_steps = []
                max_completed_id = 0

            # Create new steps list
            if new_steps:
                new_steps_list = []

                # Add completed steps first
                new_steps_list.extend(completed_steps)

                # Add new pending steps
                for i, step_desc in enumerate(new_steps):
                    new_steps_list.append({
                        "id": max_completed_id + i + 1,
                        "description": step_desc,
                        "status": "pending",
                        "notes": "",
                        "completed_at": None,
                    })

                plan_data["execution"]["steps"] = new_steps_list

                # Set current step to first pending step
                next_pending = next((s for s in new_steps_list if s.get("status") == "pending"), None)
                plan_data["execution"]["current_step"] = next_pending.get("id") if next_pending else max_completed_id + 1

            # Update metadata
            plan_data["metadata"]["updated_at"] = timestamp
            plan_data["metadata"]["version"] = plan_data.get("metadata", {}).get("version", 0) + 1

            # Add to history
            history = plan_data.get("history", [])
            history.append({
                "timestamp": timestamp,
                "action": "plan_revised",
                "description": f"Plan revised based on feedback. New steps: {len(new_steps) if new_steps else 'no change'}",
                "feedback": feedback,
            })

            # Save updated plan
            self._save_plan(plan_path, plan_data)

            result = {
                "plan_revised": True,
                "feedback_incorporated": feedback,
                "completed_steps_kept": len(completed_steps) if keep_completed else 0,
                "new_steps_added": len(new_steps) if new_steps else 0,
                "total_steps": len(plan_data["execution"]["steps"]),
                "current_step": plan_data["execution"]["current_step"],
            }

            logger.info(f"Revised plan based on feedback: {plan_path}")
            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": f"Failed to revise plan: {e}"}

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "item_id": self.item_id,
            "functions": len(self.functions) if hasattr(self, 'functions') else 0,
        }
