from __future__ import annotations

import json
import math
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import yaml
from openai import OpenAI
from rich.console import Console


console = Console()


@dataclass
class TriggerOptimizationConfig:
    iterations: int = 5
    output_yaml: str = "./outputs/trigger_candidates.yaml"
    tool_schema_path: str = "./data_examples/default_tools_schema.json"
    validation_data: str = "./data_examples/trigger_validation.json"
    embedding_model: str = "text-embedding-3-small"
    evaluation_model: str = "qwen3-coder-plus"
    cluster_k: int = 12
    pca_dim: int = 32
    trigger_length: int = 4
    candidates_per_iter: int = 8
    hotflip_top_k: int = 6
    distance_weight: float = 1.0
    semantic_weight: float = 0.7
    perplexity_weight: float = 0.2
    selection_weight: float = 2.0
    max_perplexity: float = 45.0
    seed_triggers: Sequence[str] = field(default_factory=list)
    skill_anchor_path: str = "./skills/enhanced-ehr-sql/SKILL.md"
    skill_tool_name: str = "skills_tools__activate_skill"
    tool_description_template: str = (
        "Activate the EHR SQL skill router for EHRSQL-style text-to-SQL requests "
        "(generate SQL, choose tables/columns, or return answers from EHR data). "
        "Use when prompts mention SQL queries over eICU/MIMIC schemas. Trigger tokens: {trigger}"
    )
    vocab_size: int = 250
    random_seed: int = 17


class EmbeddingClient:
    def __init__(self, api_key: str, api_url: str, model: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=api_url)
        self.model = model

    def embed(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1))
        response = self.client.embeddings.create(model=self.model, input=texts)
        vectors = [np.array(item.embedding, dtype=np.float32) for item in response.data]
        return np.vstack(vectors)


class PerplexityScorer:
    def __init__(self, api_key: str, api_url: str, model: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=api_url)
        self.model = model

    def score(self, text: str) -> Optional[float]:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Return log-probabilities for the user text."},
                    {"role": "user", "content": text},
                ],
                logprobs=True,
                top_logprobs=0,
                temperature=0.0,
            )
        except Exception as exc:
            console.print(f"[yellow]Perplexity scoring skipped:[/yellow] {exc}")
            return None

        logprobs = getattr(response.choices[0], "logprobs", None)
        if not logprobs or not getattr(logprobs, "content", None):
            return None
        token_logprobs = [tok.logprob for tok in logprobs.content if tok.logprob is not None]
        if not token_logprobs:
            return None
        avg_logprob = sum(token_logprobs) / len(token_logprobs)
        return math.exp(-avg_logprob)


def _load_tool_descriptions(path: str) -> List[Tuple[str, str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    descriptions: List[Tuple[str, str]] = []
    for item in data:
        function = item.get("function", {})
        name = function.get("name", "")
        description = function.get("description", "")
        if description:
            descriptions.append((name, description))
    return descriptions


def _load_validation_prompts(path: str) -> Tuple[List[str], List[str]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    positive = payload.get("positive", [])
    negative = payload.get("negative", [])

    def unpack(items: Iterable[Any]) -> List[str]:
        prompts: List[str] = []
        for item in items:
            if isinstance(item, str):
                prompts.append(item)
            elif isinstance(item, dict):
                prompt = item.get("prompt")
                if prompt:
                    prompts.append(prompt)
        return prompts

    return unpack(positive), unpack(negative)


def _pca_fit(embeddings: np.ndarray, dim: int) -> Tuple[np.ndarray, np.ndarray]:
    mean = embeddings.mean(axis=0)
    centered = embeddings - mean
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    components = vt[:dim]
    return mean, components


def _pca_transform(embeddings: np.ndarray, mean: np.ndarray, components: np.ndarray) -> np.ndarray:
    return (embeddings - mean) @ components.T


def _kmeans(embeddings: np.ndarray, k: int, steps: int = 25) -> np.ndarray:
    if embeddings.shape[0] < k:
        k = max(1, embeddings.shape[0])
    rng = np.random.default_rng(7)
    centers = embeddings[rng.choice(embeddings.shape[0], k, replace=False)]
    for _ in range(steps):
        distances = np.linalg.norm(embeddings[:, None, :] - centers[None, :, :], axis=2)
        labels = distances.argmin(axis=1)
        new_centers = []
        for i in range(k):
            cluster_points = embeddings[labels == i]
            if len(cluster_points) == 0:
                new_centers.append(centers[i])
            else:
                new_centers.append(cluster_points.mean(axis=0))
        centers = np.vstack(new_centers)
    return centers


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-8
    return float(np.dot(a, b) / denom)


def _extract_vocab(texts: Iterable[str], vocab_size: int) -> List[str]:
    freq: Dict[str, int] = {}
    for text in texts:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower()):
            freq[token] = freq.get(token, 0) + 1
    sorted_tokens = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return [token for token, _ in sorted_tokens[:vocab_size]]


def _build_skill_tool_schema(
    tool_name: str, description: str
) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the skill stack to load.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this skill stack should be activated.",
                    },
                },
                "required": ["skill_name", "reason"],
            },
        },
    }


class TriggerOptimizer:
    def __init__(self, config: TriggerOptimizationConfig, api_key: str, api_url: str) -> None:
        self.config = config
        self.embedding_client = EmbeddingClient(api_key, api_url, config.embedding_model)
        self.eval_client = OpenAI(api_key=api_key, base_url=api_url)
        self.perplexity_scorer = PerplexityScorer(api_key, api_url, config.evaluation_model)
        self.random = random.Random(config.random_seed)

        tool_descriptions = _load_tool_descriptions(config.tool_schema_path)
        self.tool_names = [name for name, _ in tool_descriptions]
        self.tool_texts = [desc for _, desc in tool_descriptions]

        self.anchor_text = Path(config.skill_anchor_path).read_text(encoding="utf-8")
        self.vocab = _extract_vocab(self.tool_texts + [self.anchor_text], config.vocab_size)
        if not self.vocab:
            self.vocab = ["skill", "router", "activate", "workflow"]

        embeddings = self.embedding_client.embed(self.tool_texts)
        mean, components = _pca_fit(embeddings, min(self.config.pca_dim, embeddings.shape[1]))
        reduced = _pca_transform(embeddings, mean, components)
        centers = _kmeans(reduced, self.config.cluster_k)
        self.embedding_mean = mean
        self.embedding_components = components
        self.cluster_centers = centers
        anchor_embedding = self.embedding_client.embed([self.anchor_text])[0:1]
        self.anchor_vector = _pca_transform(anchor_embedding, mean, components)[0]
        self.positive_prompts, self.negative_prompts = _load_validation_prompts(
            config.validation_data
        )
        self.tools_schema_base = json.loads(
            Path(self.config.tool_schema_path).read_text(encoding="utf-8")
        )

    def run(self) -> Dict[str, Any]:
        random.seed(self.config.random_seed)
        history: List[Dict[str, Any]] = []
        best_record: Optional[Dict[str, Any]] = None
        seed_tokens = list(self.config.seed_triggers)
        if len(seed_tokens) < self.config.trigger_length:
            seed_tokens.extend(
                self.random.sample(self.vocab, self.config.trigger_length - len(seed_tokens))
            )
        current_trigger = " ".join(seed_tokens[: self.config.trigger_length])

        for iteration in range(1, self.config.iterations + 1):
            tokens = current_trigger.split()
            if len(tokens) < self.config.trigger_length:
                tokens += self.random.sample(
                    self.vocab, self.config.trigger_length - len(tokens)
                )
            tokens = tokens[: self.config.trigger_length]

            candidates = {current_trigger}
            for idx in range(self.config.trigger_length):
                sample_vocab = self.random.sample(
                    self.vocab, min(len(self.vocab), self.config.hotflip_top_k * 4)
                )
                scored: List[Tuple[float, str]] = []
                for replacement in sample_vocab:
                    new_tokens = tokens[:]
                    new_tokens[idx] = replacement
                    candidate = " ".join(new_tokens)
                    description = self.config.tool_description_template.format(trigger=candidate)
                    embedding = self.embedding_client.embed([description])
                    reduced = _pca_transform(
                        embedding, self.embedding_mean, self.embedding_components
                    )[0]
                    similarities = [
                        _cosine_similarity(reduced, center) for center in self.cluster_centers
                    ]
                    distance_score = 1.0 - max(similarities)
                    semantic_score = _cosine_similarity(reduced, self.anchor_vector)
                    score = (
                        self.config.distance_weight * distance_score
                        + self.config.semantic_weight * semantic_score
                    )
                    scored.append((score, candidate))
                scored.sort(reverse=True, key=lambda item: item[0])
                for _, candidate in scored[: self.config.hotflip_top_k]:
                    candidates.add(candidate)
                if len(candidates) >= self.config.candidates_per_iter * 2:
                    break
            candidates = list(candidates)
            if current_trigger not in candidates:
                candidates.append(current_trigger)
            candidates = candidates[: max(self.config.candidates_per_iter, 1)]

            scored_candidates: List[Dict[str, Any]] = []
            for trigger in candidates:
                description = self.config.tool_description_template.format(trigger=trigger)
                embedding = self.embedding_client.embed([description])
                reduced = _pca_transform(
                    embedding, self.embedding_mean, self.embedding_components
                )[0]
                similarities = [
                    _cosine_similarity(reduced, center) for center in self.cluster_centers
                ]
                distance_score = 1.0 - max(similarities)
                semantic_score = _cosine_similarity(reduced, self.anchor_vector)
                magnitude = float(np.linalg.norm(reduced))
                perplexity = self.perplexity_scorer.score(trigger)
                if (
                    perplexity is not None
                    and perplexity > self.config.max_perplexity
                ):
                    continue
                base_score = (
                    self.config.distance_weight * distance_score
                    + self.config.semantic_weight * semantic_score
                )
                if perplexity is not None:
                    normalized = min(perplexity / self.config.max_perplexity, 1.0)
                    base_score -= self.config.perplexity_weight * normalized

                skill_schema = _build_skill_tool_schema(
                    self.config.skill_tool_name,
                    description,
                )
                tools_schema = self.tools_schema_base + [skill_schema]

                pos_hits = 0
                for prompt in self.positive_prompts:
                    response = self.eval_client.chat.completions.create(
                        model=self.config.evaluation_model,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are a tool router. Call the most relevant tool if needed, "
                                    "otherwise respond normally."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        tools=tools_schema,
                        tool_choice="auto",
                        temperature=0.0,
                    )
                    tool_calls = response.choices[0].message.tool_calls or []
                    called = any(
                        call.function.name == self.config.skill_tool_name for call in tool_calls
                    )
                    if called:
                        pos_hits += 1

                neg_hits = 0
                for prompt in self.negative_prompts:
                    response = self.eval_client.chat.completions.create(
                        model=self.config.evaluation_model,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are a tool router. Call the most relevant tool if needed, "
                                    "otherwise respond normally."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        tools=tools_schema,
                        tool_choice="auto",
                        temperature=0.0,
                    )
                    tool_calls = response.choices[0].message.tool_calls or []
                    called = any(
                        call.function.name == self.config.skill_tool_name for call in tool_calls
                    )
                    if called:
                        neg_hits += 1

                asr = pos_hits / max(len(self.positive_prompts), 1)
                ftr = neg_hits / max(len(self.negative_prompts), 1)
                selection_score = asr - ftr
                total_score = base_score + self.config.selection_weight * selection_score
                scored_candidates.append(
                    {
                        "trigger": trigger,
                        "distance_score": distance_score,
                        "semantic_score": semantic_score,
                        "embedding_norm": magnitude,
                        "perplexity": perplexity,
                        "asr": asr,
                        "ftr": ftr,
                        "objective_score": total_score,
                    }
                )

            if not scored_candidates:
                console.print("[red]No candidates survived perplexity filtering.[/red]")
                break

            scored_candidates.sort(key=lambda item: item["objective_score"], reverse=True)
            best_candidate = scored_candidates[0]
            current_trigger = best_candidate["trigger"]
            record = {
                "iteration": iteration,
                "trigger": current_trigger,
                "asr": best_candidate["asr"],
                "ftr": best_candidate["ftr"],
                "objective_score": best_candidate["objective_score"],
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            history.append(record)

            if best_record is None or best_candidate["objective_score"] > best_record["objective_score"]:
                best_record = record

            payload = {
                "current_trigger": current_trigger,
                "best_trigger": best_record,
                "iterations": history,
            }
            path = Path(self.config.output_yaml)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

            console.print(
                f"[cyan]Iteration {iteration}[/cyan] | trigger='{current_trigger}' "
                f"| ASR={best_candidate['asr']:.2f} FTR={best_candidate['ftr']:.2f}"
            )

        return {
            "current_trigger": current_trigger,
            "best_trigger": best_record,
            "history": history,
        }
