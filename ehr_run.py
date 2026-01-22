#!/usr/bin/env python3
from __future__ import annotations

import argparse
from ast import Not
import csv
import json
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from openai import OpenAI

from agent.default import DefaultAgent
from utils import load_config

console = Console()


# ====== LLM-as-Judge config（从你的 config/default.yaml 读）======
llm_config = load_config("config/default.yaml")
BASE_URL = llm_config.api_url
API_KEY = llm_config.api_key
MODEL = llm_config.model_name
SYSTEM = "You are a judge. Decide whether agent_result matches query_result. The agent's calculation might be more detailed, and it might incorporate natural language. However, I still hope you can verify whether the agent's answer is correct. Just give me a yes or no."
# ================================================================


def call_llm_per_row(question: str, query_result_str: str, agent_result_str: str) -> str:
    # 每条任务 new 一个 client（按你之前要求）
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY, max_retries=5)
    user = (
        f"question: {question}\n\n"
        f"query_result (string): {query_result_str}\n\n"
        f"agent_result (string): {agent_result_str}\n"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0.0,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content


def judge_yes_no(llm_text: str) -> bool:
    t = (llm_text or "").strip().lower()
    return "yes" in t


def to_abs(path: str, base: Optional[str] = None) -> str:
    p = Path(path)
    if p.is_absolute():
        return str(p)
    base_path = Path(base) if base else Path.cwd()
    return str((base_path / p).resolve())


def load_json_records(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        console.print(f"[yellow]Warning[/yellow]: data file not found: {path}")
        return []

    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    except json.JSONDecodeError:
        pass

    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def run_sql_query(db_path: Path, query: str) -> List[Tuple[Any, ...]]:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query)
        return cursor.fetchall()


def normalize_query_result(rows: List[Tuple[Any, ...]]) -> Any:
    if not rows:
        return []
    if len(rows[0]) == 1:
        return [row[0] for row in rows]
    return [list(row) for row in rows]


def normalize_agent_answer(answer_text: str) -> Any:
    cleaned = answer_text.strip()
    if not cleaned:
        return ""
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return cleaned


def canonicalize(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def build_agent_prompt(question: str, db_id: str, db_path: Path) -> str:
    return (
        "You are a SafeFlow agent answering clinical data questions.\n\n"
        f"Database ID: {db_id}\n"
        f"Database path: {db_path}\n"
        f"Question: {question}\n\n"
        "Task:\n"
        "1. Use Python or sqlite to query the SQLite database above.\n"
        "2. Derive the answer from the database only.\n"
        "3. When finished, call base_tools__finish_task with verify_task=false.\n"
        "4. Put ONLY the final answer (or list of answers) in the finish_task message.\n"
    )


def extract_finish_message(run_result: Dict[str, Any]) -> str:
    messages = run_result.get("messages", [])
    if run_result.get('final', None):
        return run_result.get('final', None)
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        if message.get("name") != "base_tools__finish_task":
            continue
        content = message.get("content", "")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(payload, dict):
            result = payload.get("result", {})
            if isinstance(result, dict) and "message" in result:
                return str(result["message"])
        return content
    return "Not Found"


def ensure_csv_with_header(path: Path, header: List[str]) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        f.flush()


# -------------------- 子进程执行的函数（处理一个 id） --------------------
def run_one_record(record: Dict[str, Any], config_path: str, output_dir: str, data_dir: str) -> Dict[str, Any]:
    """
    注意：ProcessPool 下，尽量只传简单可 pickle 的参数。
    所以子进程里重新 load_config(config_path)，避免传复杂对象。
    """
    config = load_config(config_path)

    output_dir_p = Path(output_dir)
    data_dir_p = Path(data_dir)

    item_id = record.get("id", "unknown")
    question = record.get("question", "")
    db_id = record.get("db_id", "")
    query = record.get("query", "")

    task_dir = output_dir_p / item_id
    task_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir_p / f"{db_id}.db"
    if not db_path.exists():
        return {
            "id": item_id,
            "question": question,
            "query_result": "",
            "agent_result": "",
            "llm_judge_raw": "",
            "correct": False,
            "error": f"DB not found: {db_path}",
        }

    try:
        query_rows = run_sql_query(db_path, query)
        ground_truth = normalize_query_result(query_rows)
    except Exception as exc:
        return {
            "id": item_id,
            "question": question,
            "query_result": "",
            "agent_result": "",
            "llm_judge_raw": "",
            "correct": False,
            "error": f"Query failed: {exc}",
        }

    prompt = build_agent_prompt(question=question, db_id=db_id, db_path=db_path)

    agent = DefaultAgent(config=config, item_id=item_id, work_root=str(task_dir))

    try:
        run_result = agent.run(prompt)
        agent_message = extract_finish_message(run_result)
    except Exception as exc:
        agent_message = ""

    agent_answer = normalize_agent_answer(agent_message)

    query_result_str = canonicalize(ground_truth)
    agent_result_str = canonicalize(agent_answer)

    llm_judge_raw = ""
    correct = False
    try:
        llm_judge_raw = call_llm_per_row(question, query_result_str, agent_result_str)
        correct = judge_yes_no(llm_judge_raw)
    except Exception as exc:
        llm_judge_raw = ""
        correct = False

    return {
        "id": item_id,
        "question": question,
        "query_result": query_result_str,
        "agent_result": agent_result_str,
        "llm_judge_raw": llm_judge_raw,
        "correct": correct,
        "error": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EHRSQL tasks with SafeFlow agents (LLM-as-Judge, parallel)")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--output_dir", default="./ehr_outputs")
    parser.add_argument("--data_dir", default="./data/ehrsql")
    parser.add_argument("--train_json", default="eicu_train.json")
    parser.add_argument("--valid_json", default="eicu_valid.json")
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--workers", type=int, default=(os.cpu_count() or 1))
    args = parser.parse_args()

    output_dir = Path(to_abs(args.output_dir))
    data_dir = Path(to_abs(args.data_dir))
    output_dir.mkdir(parents=True, exist_ok=True)

    train_records = load_json_records(data_dir / args.train_json)
    valid_records = load_json_records(data_dir / args.valid_json)
    records = train_records + valid_records

    filtered_records = [r for r in records if not r.get("is_impossible", False)]
    if args.limit is not None:
        filtered_records = filtered_records[: args.limit]

    if not filtered_records:
        console.print("[yellow]No records found to process.[/yellow]")
        return

    results_csv = output_dir / "ehr_results.csv"
    header = ["id", "question", "query_result", "agent_result", "llm_judge_raw", "correct", "error"]
    ensure_csv_with_header(results_csv, header)

    correct_count = 0
    total = len(filtered_records)

    # 主进程实时追加写；子进程只返回 dict
    with results_csv.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)

        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = []
            for record in filtered_records:
                futures.append(
                    ex.submit(
                        run_one_record,
                        record,
                        args.config,
                        str(output_dir),
                        str(data_dir),
                    )
                )

            done_n = 0
            for fut in as_completed(futures):
                result = fut.result()
                done_n += 1

                item_id = result.get("id", "unknown")
                console.print(f"[{done_n}/{total}] finished {item_id} (correct={result.get('correct')})", style="cyan")

                if result.get("correct"):
                    correct_count += 1

                writer.writerow([
                    result.get("id", ""),
                    result.get("question", ""),
                    result.get("query_result", ""),
                    result.get("agent_result", ""),
                    result.get("llm_judge_raw", ""),
                    result.get("correct", False),
                    result.get("error", None),
                ])
                f.flush()  # 实时落盘

    accuracy = correct_count / total
    summary_path = output_dir / "summary.json"
    summary = {
        "total": total,
        "correct": correct_count,
        "accuracy": accuracy,
        "results_csv": str(results_csv),
        "workers": args.workers,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    console.print("\nRun completed.", style="green")
    console.print(f"Accuracy: {accuracy:.2%} ({correct_count}/{total})", style="green")
    console.print(f"Results saved to: {results_csv}", style="blue")


if __name__ == "__main__":
    main()