from __future__ import annotations

from utils import load_config
from utils.trigger_optimizer import TriggerOptimizationConfig, TriggerOptimizer


def _build_config(raw_cfg) -> TriggerOptimizationConfig:
    trigger_cfg = getattr(raw_cfg, "trigger_optimization", None)
    if trigger_cfg is None:
        return TriggerOptimizationConfig()

    return TriggerOptimizationConfig(
        iterations=getattr(trigger_cfg, "iterations", 5),
        output_yaml=getattr(trigger_cfg, "output_yaml", "./outputs/trigger_candidates.yaml"),
        tool_schema_path=getattr(trigger_cfg, "tool_schema_path", "./data_examples/default_tools_schema.json"),
        validation_data=getattr(trigger_cfg, "validation_data", "./data_examples/trigger_validation.json"),
        embedding_model=getattr(trigger_cfg, "embedding_model", "text-embedding-3-small"),
        evaluation_model=getattr(trigger_cfg, "evaluation_model", getattr(raw_cfg, "model_name", "qwen3-coder-plus")),
        cluster_k=getattr(trigger_cfg, "cluster_k", 12),
        pca_dim=getattr(trigger_cfg, "pca_dim", 32),
        trigger_length=getattr(trigger_cfg, "trigger_length", 4),
        candidates_per_iter=getattr(trigger_cfg, "candidates_per_iter", 8),
        hotflip_top_k=getattr(trigger_cfg, "hotflip_top_k", 6),
        distance_weight=getattr(trigger_cfg, "distance_weight", 1.0),
        semantic_weight=getattr(trigger_cfg, "semantic_weight", 0.7),
        perplexity_weight=getattr(trigger_cfg, "perplexity_weight", 0.2),
        selection_weight=getattr(trigger_cfg, "selection_weight", 2.0),
        max_perplexity=getattr(trigger_cfg, "max_perplexity", 45.0),
        seed_triggers=getattr(trigger_cfg, "seed_triggers", ["skill", "router", "activation", "workflow"]),
        skill_anchor_path=getattr(trigger_cfg, "skill_anchor_path", "./skills/enhanced-ehr-sql/SKILL.md"),
        skill_tool_name=getattr(trigger_cfg, "skill_tool_name", "skills_tools__activate_skill"),
        tool_description_template=getattr(
            trigger_cfg,
            "tool_description_template",
            (
                "Activate the skill router to load a domain-specific skill stack and its tool "
                "preferences. Use when a task requires skill-aware routing or preloaded "
                "instructions. Trigger tokens: {trigger}"
            ),
        ),
    )


def main() -> None:
    config = load_config("config/default.yaml")
    optimizer_config = _build_config(config)
    optimizer = TriggerOptimizer(
        optimizer_config,
        api_key=config.api_key,
        api_url=config.api_url,
    )
    optimizer.run()


if __name__ == "__main__":
    main()
