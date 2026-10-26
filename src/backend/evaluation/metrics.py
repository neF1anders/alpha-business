"""Offline evaluation of ranked event IDs or evidence IDs; standard library only."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(row)
    return rows


def required_id(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile; no interpolated performance claims."""
    return sorted(values)[max(0, math.ceil(fraction * len(values)) - 1)] if values else None


def evaluate(
    judgments: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    k: int = 5,
    unjudged_policy: str = "zero",
    min_relevant_grade: int = 1,
) -> dict[str, Any]:
    """Grades: 0 irrelevant, 1 related, 2 useful, 3 essential; grade > 0 is a hit.

    All queries in judgments are evaluated, including missing runs (empty ranking).
    A query with no positive labels is excluded from ranking macro averages and
    evaluated for abstention separately. Unknown retrieved IDs count as zero by
    default; their fraction is always reported. Use 'error' for a fully judged pool.
    """
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    if unjudged_policy not in {"zero", "error"}:
        raise ValueError("unjudged_policy must be 'zero' or 'error'")
    if type(min_relevant_grade) is not int or not 1 <= min_relevant_grade <= 3:
        raise ValueError("min_relevant_grade must be 1, 2 or 3")
    qrels: dict[str, dict[str, int]] = {}
    for row in judgments:
        query_id = required_id(row, "query_id")
        doc_id = required_id(row, "doc_id")
        grade = row.get("grade")
        if type(grade) is not int or not 0 <= grade <= 3:
            raise ValueError(f"{query_id}/{doc_id}: grade must be an integer from 0 to 3")
        labels = qrels.setdefault(query_id, {})
        if doc_id in labels:
            raise ValueError(f"{query_id}/{doc_id}: duplicate judgment")
        labels[doc_id] = grade
    if not qrels:
        raise ValueError("judgments must contain at least one query")

    indexed_runs: dict[str, dict[str, Any]] = {}
    for row in runs:
        query_id = required_id(row, "query_id")
        if query_id not in qrels:
            raise ValueError(f"{query_id}: run has no judgments")
        if query_id in indexed_runs:
            raise ValueError(f"{query_id}: duplicate run")
        ranking = row.get("ranking")
        if not isinstance(ranking, list):
            raise ValueError(f"{query_id}: ranking must be a list of IDs in ranked order")
        if any(not isinstance(item, str) or not item.strip() for item in ranking):
            raise ValueError(f"{query_id}: ranked IDs must be non-empty strings")
        if len(ranking) != len(set(ranking)):
            raise ValueError(f"{query_id}: duplicate ranked ID; deduplicate before evaluation")
        elapsed = row.get("latency_ms")
        if elapsed is not None and (
            type(elapsed) not in {int, float} or not math.isfinite(elapsed) or elapsed < 0
        ):
            raise ValueError(f"{query_id}: latency_ms must be finite and nonnegative")
        indexed_runs[query_id] = row

    per_query = []
    for query_id, labels in qrels.items():
        supplied = query_id in indexed_runs
        run = indexed_runs.get(query_id, {"ranking": []})
        ranked = run["ranking"][:k]
        unknown = [doc_id for doc_id in ranked if doc_id not in labels]
        if unknown and unjudged_policy == "error":
            raise ValueError(f"{query_id}: unjudged IDs in top-{k}: {unknown}")
        grades = [labels.get(doc_id, 0) for doc_id in ranked]
        positives = sum(grade >= min_relevant_grade for grade in labels.values())
        hits = sum(grade >= min_relevant_grade for grade in grades)
        dcg = sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))
        ideal = sorted(labels.values(), reverse=True)[:k]
        idcg = sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(ideal))
        first_hit = next((rank for rank, grade in enumerate(grades, 1) if grade >= min_relevant_grade), None)
        per_query.append(
            {
                "query_id": query_id,
                "run_present": supplied,
                "retrieved": len(ranked),
                "relevant_in_judgments": positives,
                "precision_at_k": hits / k if positives else None,
                "recall_at_k": hits / positives if positives else None,
                "ndcg_at_k": dcg / idcg if idcg and positives else None,
                "reciprocal_rank_at_k": (1 / first_hit if first_hit else 0) if positives else None,
                "hit_at_k": int(hits > 0) if positives else None,
                # Missing execution is a failure, not a correct abstention.
                "correct_abstention": int(supplied and not ranked) if not positives else None,
                "unjudged_in_top_k": len(unknown),
                "latency_ms": run.get("latency_ms"),
            }
        )

    aggregate = {}
    for name in ["precision_at_k", "recall_at_k", "ndcg_at_k", "reciprocal_rank_at_k", "hit_at_k"]:
        aggregate[name] = mean([row[name] for row in per_query if row[name] is not None])
    aggregate["correct_abstention_rate"] = mean(
        [row["correct_abstention"] for row in per_query if row["correct_abstention"] is not None]
    )
    ranked_count = sum(row["retrieved"] for row in per_query)
    latencies = [row["latency_ms"] for row in per_query if row["latency_ms"] is not None]
    return {
        "k": k,
        "binary_relevance": f"grade >= {min_relevant_grade}",
        "unjudged_policy": unjudged_policy,
        "query_count": len(per_query),
        "ranking_query_count": sum(row["relevant_in_judgments"] > 0 for row in per_query),
        "no_relevant_query_count": sum(row["relevant_in_judgments"] == 0 for row in per_query),
        "missing_run_count": sum(not row["run_present"] for row in per_query),
        "unjudged_fraction_at_k": (
            sum(row["unjudged_in_top_k"] for row in per_query) / ranked_count if ranked_count else None
        ),
        "latency_sample_count": len(latencies),
        "latency_p50_ms": percentile(latencies, 0.5),
        "latency_p95_ms": percentile(latencies, 0.95),
        "macro": aggregate,
        "per_query": per_query,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qrels", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--unjudged-policy", choices=["zero", "error"], default="zero")
    parser.add_argument("--min-relevant-grade", type=int, choices=[1,2,3], default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = evaluate(read_jsonl(args.qrels), read_jsonl(args.run), args.k, args.unjudged_policy, args.min_relevant_grade)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        # Never overwrite labels or a retriever's raw results accidentally.
        if args.output.resolve() in {args.qrels.resolve(), args.run.resolve()}:
            parser.error("output must differ from input files")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
