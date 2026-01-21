#!/usr/bin/env python3
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def to_int_if_possible(x: Any) -> Any:
    # keep None/strings as-is; only coerce clean numeric types
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def main(base_dir: str, summary_name: str, rr_name: str, out_name: str) -> None:
    base = Path(base_dir).resolve()

    summary_path = base / summary_name
    rr_path = base / rr_name
    if not summary_path.exists():
        raise SystemExit(f"Missing {summary_name} at: {summary_path}")
    if not rr_path.exists():
        raise SystemExit(f"Missing {rr_name} at: {rr_path}")

    summary: List[Dict[str, Any]] = load_json(summary_path)
    rr: List[Dict[str, Any]] = load_json(rr_path)

    # Build lookup from rr_results_full_json.json by run_id
    rr_map: Dict[str, Dict[str, Any]] = {}
    for rec in rr:
        run_id = rec.get("run_id")
        if not run_id:
            continue
        rr_map[str(run_id)] = {
            "dataset": rec.get("dataset"),
            "machines": to_int_if_possible(rec.get("machines")),
            "cores": to_int_if_possible(rec.get("cores")),
            "quantum_ms": to_int_if_possible(rec.get("quantum_ms")),
        }

    enriched: List[Dict[str, Any]] = []
    matched = 0
    missing = 0

    for obj in summary:
        run_id = str(obj.get("id", ""))
        extra = rr_map.get(run_id)

        new_obj = dict(obj)  # shallow copy
        if extra is not None:
            matched += 1
            # Add fields at top-level (easy to consume)
            new_obj.update(extra)
        else:
            missing += 1
            # keep fields explicit if you want to spot gaps
            new_obj.update({"dataset": None, "machines": None, "cores": None, "quantum_ms": None})

        enriched.append(new_obj)

    out_path = base / out_name
    out_path.write_text(json.dumps(enriched, indent=2), encoding="utf-8")

    print(f"Wrote: {out_path}")
    print(f"Summary entries: {len(summary)}")
    print(f"Matched rr entries: {matched}")
    print(f"No rr match: {missing}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=".", help="Directory containing the JSON files")
    ap.add_argument("--summary", default="summary_results.json", help="Output from the first script")
    ap.add_argument("--rr", default="rr_results_full_json.json", help="RR metadata JSON file")
    ap.add_argument("--out", default="summary_results_enriched.json", help="Output merged JSON")
    args = ap.parse_args()

    main(args.base, args.summary, args.rr, args.out)
