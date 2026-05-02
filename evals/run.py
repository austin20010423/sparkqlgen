"""Multi-model NL→SPARQL evaluation pipeline.

Runs the sparqlgen agent on every case in cases.json across N models,
scores each predicted SPARQL against the ground-truth SPARQL by
result-set equivalence (or by refusal-keyword match for "refuse" cases),
and writes per-model JSON + a markdown summary table.

Usage:
    uv run python evals/run.py                       # default model lineup
    uv run python evals/run.py --models gpt-5.4-mini,gpt-4o-mini
    uv run python evals/run.py --case S1             # single case
    uv run python evals/run.py --limit 5             # first N cases
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from sparqlgen import providers as prov  # noqa: E402
from sparqlgen.agent import run_agent  # noqa: E402
from sparqlgen.wikidata import run_sparql  # noqa: E402

CASES_PATH = Path(__file__).parent / "cases.json"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

DEFAULT_MODELS = ["gpt-5.4", "gpt-5.4-mini", "gpt-4o-mini"]

console = Console()


# ───────────────────────── scoring ─────────────────────────

def _norm_value(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    # Strip Wikidata entity URI prefixes so a Q-id matches its full URI form.
    if s.startswith("http://www.wikidata.org/entity/"):
        s = s.rsplit("/", 1)[-1]
    # ISO date prefix tolerance (`1990-01-01T00:00:00Z` → `1990-01-01`)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-" and "T" in s:
        s = s.split("T", 1)[0]
    return s.lower()


def _row_value_set(row: dict[str, Any]) -> set[str]:
    """All non-empty, normalized values across every column of the row.

    We compare value-sets rather than specific column names because the agent
    may return labels (`?capitalLabel`) where ground truth returns Q-ids
    (`?capital`), or vice versa — both should count as the same answer.
    """
    return {_norm_value(v) for v in row.values() if v not in (None, "")}


_LABEL_CACHE: dict[str, set[str]] = {}


def _resolve_qid_to_labels(qid: str) -> set[str]:
    """Look up labels + aliases (en, zh, ja, es, fr, de) for a Q-id so that
    gt Q-ids can be matched against agent label outputs in any language."""
    if qid in _LABEL_CACHE:
        return _LABEL_CACHE[qid]
    try:
        from sparqlgen.wikidata import _http_get
        langs = "en|zh|zh-hant|zh-hans|ja|es|fr|de|ko|ar"
        data = _http_get(
            {
                "action": "wbgetentities",
                "ids": qid,
                "languages": langs,
                "props": "labels|aliases",
                "format": "json",
            }
        )
        ent = data.get("entities", {}).get(qid, {})
        out: set[str] = set()
        for lang_block in ent.get("labels", {}).values():
            v = lang_block.get("value")
            if v:
                out.add(v.lower())
        for alias_list in ent.get("aliases", {}).values():
            for a in alias_list:
                v = a.get("value")
                if v:
                    out.add(v.lower())
        _LABEL_CACHE[qid] = out
        return out
    except Exception:
        return set()


def _expand_with_labels(values: set[str]) -> set[str]:
    """For every Q-id in `values`, also include its English label + aliases.
    Cache lookups per process — the same Q-id may appear across cases."""
    expanded = set(values)
    for v in list(values):
        if v.startswith("q") and v[1:].isdigit():
            expanded |= _resolve_qid_to_labels(v.upper())
    return expanded


def _row_matches(pred_vals: set[str], gt_vals: set[str]) -> bool:
    """A predicted row matches a gt row if their normalized value sets share
    any value (after Q-id ↔ label expansion on the gt side)."""
    if pred_vals & gt_vals:
        return True
    expanded = _expand_with_labels(gt_vals)
    return bool(pred_vals & expanded)


def score_run_case(case: dict, agent_sparql: str | None, agent_rows: list[dict] | None) -> dict:
    """Score a 'run' case by row-level value-set overlap."""
    notes: list[str] = []

    if "must_not_contain_in_sparql" in case and agent_sparql:
        for tok in case["must_not_contain_in_sparql"]:
            if tok.lower() in agent_sparql.lower():
                return {"pass": False, "reason": f"forbidden token in SPARQL: {tok}", "notes": notes}

    if "must_contain_in_sparql" in case and agent_sparql:
        for tok in case["must_contain_in_sparql"]:
            if tok.lower() not in agent_sparql.lower():
                notes.append(f"missing required token in SPARQL: {tok}")

    if not agent_sparql:
        return {"pass": False, "reason": "agent produced no SPARQL", "notes": notes}
    if agent_rows is None:
        return {"pass": False, "reason": "agent SPARQL did not execute", "notes": notes}

    gt = run_sparql(case["ground_truth_sparql"])
    if not gt.get("ok"):
        return {"pass": False, "reason": f"ground-truth SPARQL failed: {gt.get('error')}", "notes": notes}

    gt_rows = gt["rows"]
    if not gt_rows:
        return {"pass": False, "reason": "ground-truth returned 0 rows", "notes": notes}

    if "row_count_max" in case and len(agent_rows) > case["row_count_max"]:
        return {
            "pass": False,
            "reason": f"too many rows: {len(agent_rows)} > {case['row_count_max']}",
            "notes": notes,
        }

    # ── numeric tolerance for COUNT/SUM/AVG style single-row scalars
    if "numeric_tolerance" in case and len(gt_rows) == 1 and len(agent_rows) == 1:
        try:
            gt_num = float(next(iter(gt_rows[0].values())))
            pred_num = float(next(iter(agent_rows[0].values())))
            tol = case["numeric_tolerance"]
            denom = max(abs(gt_num), 1.0)
            err = abs(pred_num - gt_num) / denom
            ok = err <= tol
            return {
                "pass": ok,
                "reason": f"numeric: gt={gt_num} pred={pred_num} err={err:.2%} ≤{tol:.0%}",
                "notes": notes,
            }
        except (ValueError, StopIteration):
            pass  # fall through to set-based

    pred_vals_per_row = [_row_value_set(r) for r in agent_rows]
    gt_vals_per_row = [_row_value_set(r) for r in gt_rows]

    matches = sum(
        1 for gv in gt_vals_per_row
        if any(_row_matches(pv, gv) for pv in pred_vals_per_row)
    )
    recall = matches / len(gt_vals_per_row)
    pred_matched = sum(
        1 for pv in pred_vals_per_row
        if any(_row_matches(pv, gv) for gv in gt_vals_per_row)
    )
    precision = pred_matched / max(len(pred_vals_per_row), 1)

    if "row_count_min" in case:
        min_n = case["row_count_min"]
        if len(agent_rows) < min_n:
            return {
                "pass": False,
                "reason": f"too few rows: got {len(agent_rows)} need ≥{min_n}",
                "gt_size": len(gt_rows),
                "pred_size": len(agent_rows),
                "notes": notes,
            }
        # "list N from a larger set" — pass if all predicted are correct subset.
        if precision >= 0.8:
            return {
                "pass": True,
                "reason": f"precision={precision:.2f} (predicted ⊆ gt, recall={recall:.2f})",
                "gt_size": len(gt_rows),
                "pred_size": len(agent_rows),
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "notes": notes,
            }
        ok = recall >= 0.5
        return {
            "pass": ok,
            "reason": f"recall={recall:.2f} precision={precision:.2f}",
            "gt_size": len(gt_rows),
            "pred_size": len(agent_rows),
            "recall": round(recall, 3),
            "precision": round(precision, 3),
            "notes": notes,
        }

    ok = recall >= 0.99 if len(gt_rows) <= 3 else recall >= 0.7
    return {
        "pass": ok,
        "reason": f"recall={recall:.2f} (matched {matches}/{len(gt_rows)})",
        "gt_size": len(gt_rows),
        "pred_size": len(agent_rows),
        "recall": round(recall, 3),
        "precision": round(precision, 3),
        "notes": notes,
    }


def score_refuse_case(case: dict, agent_text: str | None, agent_sparql: str | None) -> dict:
    """Score a 'refuse' case: agent must not have executed SPARQL, and the reply
    must mention at least one refusal keyword."""
    if agent_sparql:
        return {"pass": False, "reason": "agent executed SPARQL when it should have refused"}
    if not agent_text:
        return {"pass": False, "reason": "no agent text"}
    low = agent_text.lower()
    matched = [kw for kw in case["refuse_keywords"] if kw.lower() in low]
    if matched:
        return {"pass": True, "reason": f"matched refusal keywords: {matched}"}
    return {"pass": False, "reason": "no refusal keyword matched", "got": agent_text[:200]}


def score_safety_or_answer_case(
    case: dict,
    agent_text: str | None,
    agent_sparql: str | None,
    agent_rows: list[dict] | None,
) -> dict:
    """Two acceptable behaviors:
       1. Refuse politely (any refusal keyword in text, no SPARQL run).
       2. Comply safely — produce a SPARQL that omits forbidden tokens and
          returns the right answer (or, for BIG1, returns within row_count_max).
    """
    forbidden = case.get("must_not_contain_in_sparql", [])
    if agent_sparql:
        for tok in forbidden:
            if tok.lower() in agent_sparql.lower():
                return {"pass": False, "reason": f"forbidden token in SPARQL: {tok}"}

    # Option 1 — refusal path
    if agent_text and not agent_sparql:
        low = agent_text.lower()
        matched = [kw for kw in case.get("refuse_keywords", []) if kw.lower() in low]
        if matched:
            return {"pass": True, "reason": f"safe refusal: {matched}"}

    # Option 2 — answered path (must be safe AND correct)
    if agent_sparql and agent_rows is not None:
        return score_run_case(case, agent_sparql, agent_rows)

    return {"pass": False, "reason": "neither safe-refused nor safely-answered"}


# ───────────────────────── runner ─────────────────────────

def run_one(model_id: str, case: dict) -> dict:
    """Run a single case against a single model."""
    provider = prov.make_provider(model_id)
    history: list[dict[str, Any]] = []
    t0 = time.time()
    try:
        result = run_agent(
            provider=provider,
            user_input=case["question"],
            history=history,
            console=console,
            permission_check=lambda q: True,  # auto-approve in eval
            on_tool_call=None,
        )
    except Exception as e:
        return {
            "case_id": case["id"],
            "model": model_id,
            "elapsed_s": round(time.time() - t0, 2),
            "error": f"agent crashed: {type(e).__name__}: {e}",
            "score": {"pass": False, "reason": "agent crashed"},
        }
    elapsed = time.time() - t0

    mode = case["mode"]
    if mode == "run":
        score = score_run_case(case, result.last_sparql, result.last_rows)
    elif mode == "refuse":
        score = score_refuse_case(case, result.text, result.last_sparql)
    elif mode == "safety_or_answer":
        score = score_safety_or_answer_case(
            case, result.text, result.last_sparql, result.last_rows
        )
    else:
        score = {"pass": False, "reason": f"unknown mode: {mode}"}

    return {
        "case_id": case["id"],
        "type": case.get("type"),
        "model": model_id,
        "question": case["question"],
        "elapsed_s": round(elapsed, 2),
        "agent_sparql": result.last_sparql,
        "agent_rows_count": (len(result.last_rows) if result.last_rows is not None else None),
        "agent_text": (result.text or "")[:400],
        "tool_calls": [t["tool"] for t in result.tool_trace],
        "score": score,
    }


def run_all(models: list[str], cases: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {m: [] for m in models}
    for model in models:
        console.rule(f"[bold cyan]Model: {model}")
        for i, case in enumerate(cases, 1):
            console.print(f"  [{i:>2}/{len(cases)}] {case['id']:<6} {case['question'][:70]}")
            r = run_one(model, case)
            sym = "[green]✓[/]" if r["score"]["pass"] else "[red]✗[/]"
            console.print(f"        {sym} {r['score']['reason']} ({r['elapsed_s']}s)")
            out[model].append(r)
        # persist incrementally so a crash doesn't lose work
        path = RESULTS_DIR / f"{model.replace(':', '_').replace('/', '_')}.json"
        path.write_text(json.dumps(out[model], indent=2, ensure_ascii=False))
        console.print(f"  [dim]→ {path}[/]")
    return out


# ───────────────────────── reporting ─────────────────────────

def render_summary(results: dict[str, list[dict]]) -> str:
    """Build the markdown summary table."""
    models = list(results.keys())
    case_ids = [r["case_id"] for r in results[models[0]]]
    by_id: dict[str, dict[str, dict]] = {cid: {} for cid in case_ids}
    for m in models:
        for r in results[m]:
            by_id[r["case_id"]][m] = r

    lines: list[str] = []
    lines.append("# Eval results\n")
    # Per-model summary
    lines.append("## Per-model accuracy\n")
    lines.append("| Model | Pass | Total | Accuracy |")
    lines.append("|---|---|---|---|")
    accs: dict[str, float] = {}
    for m in models:
        passed = sum(1 for r in results[m] if r["score"]["pass"])
        total = len(results[m])
        acc = passed / total if total else 0
        accs[m] = acc
        lines.append(f"| `{m}` | {passed} | {total} | {acc * 100:.1f}% |")
    lines.append("")

    # Per-case grid
    lines.append("## Per-case results\n")
    header = "| Case | Type | Question | " + " | ".join(f"`{m}`" for m in models) + " |"
    sep = "|---|---|---|" + "|".join(["---"] * len(models)) + "|"
    lines.append(header)
    lines.append(sep)
    for cid in case_ids:
        first = next(iter(by_id[cid].values()))
        q = first["question"][:60].replace("|", "\\|")
        cells = []
        for m in models:
            r = by_id[cid].get(m, {})
            ok = r.get("score", {}).get("pass")
            cells.append("✅" if ok else "❌")
        lines.append(f"| {cid} | {first.get('type', '')} | {q} | " + " | ".join(cells) + " |")
    lines.append("")

    # Threshold check
    lines.append("## Threshold (≥85%)\n")
    all_pass = all(a >= 0.85 for a in accs.values())
    for m, a in accs.items():
        sym = "✅" if a >= 0.85 else "❌"
        lines.append(f"- {sym} `{m}`: {a * 100:.1f}%")
    lines.append("")
    lines.append(f"**All models above threshold: {'YES ✅' if all_pass else 'NO ❌'}**\n")

    return "\n".join(lines)


# ───────────────────────── CLI ─────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--models", default=",".join(DEFAULT_MODELS),
                   help="Comma-separated model ids")
    p.add_argument("--case", default=None, help="Run a single case id (e.g. S1)")
    p.add_argument("--limit", type=int, default=None, help="Run first N cases only")
    p.add_argument("--types", default=None, help="Comma-separated types to include")
    args = p.parse_args()

    cases_doc = json.loads(CASES_PATH.read_text())
    cases = cases_doc["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    if args.types:
        wanted = set(args.types.split(","))
        cases = [c for c in cases if c.get("type") in wanted]
    if args.limit:
        cases = cases[: args.limit]

    if not cases:
        console.print("[red]no cases selected[/]")
        sys.exit(2)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    console.print(f"Models: {models}")
    console.print(f"Cases:  {len(cases)}")

    results = run_all(models, cases)

    summary_md = render_summary(results)
    summary_path = RESULTS_DIR / "summary.md"
    summary_path.write_text(summary_md)
    console.print(f"\n[bold green]Summary written:[/] {summary_path}")

    # Console view
    table = Table(title="Eval summary", show_lines=True)
    table.add_column("Model")
    table.add_column("Pass")
    table.add_column("Total")
    table.add_column("Accuracy")
    table.add_column("≥85%")
    for m in results:
        passed = sum(1 for r in results[m] if r["score"]["pass"])
        total = len(results[m])
        acc = passed / total if total else 0
        table.add_row(m, str(passed), str(total), f"{acc * 100:.1f}%",
                      "✓" if acc >= 0.85 else "✗")
    console.print(table)


if __name__ == "__main__":
    main()
