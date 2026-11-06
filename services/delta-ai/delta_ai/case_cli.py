"""Server-local review CLI. The graph's API token has no review capability."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from delta_ai.case_evaluation import EvaluationDataset, evaluate, export_candidate
from delta_ai.cases import CaseReview, CaseStore


def private_write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=os.environ.get("DELTA_CASE_STORE",
        str(Path(os.environ.get("DELTA_RULE_STORE", "var/rule_candidates.json")).parent / "verified-cases.sqlite")))
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--offset", type=int, default=0)
    review = commands.add_parser("review")
    review.add_argument("case_id")
    review.add_argument("revision")
    review.add_argument("--file", required=True, type=Path, help="검토자/사유/기대 확인 항목 JSON")
    export = commands.add_parser("export")
    export.add_argument("case_ids", nargs="+")
    export.add_argument("--output", required=True, type=Path)
    scoring = commands.add_parser("evaluate")
    scoring.add_argument("dataset", type=Path)
    scoring.add_argument("--llm", choices=("fake", "claude", "none"), default="none")
    scoring.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    store = CaseStore(args.store)
    if args.command == "list":
        rows = store.list(offset=args.offset)
        for row in rows:
            row["case"] = json.loads(row.pop("payload"))
            row["review"] = json.loads(row["review"]) if row["review"] else None
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.command == "review":
        store.review(args.case_id, args.revision, CaseReview.model_validate_json(args.file.read_bytes()))
        print(json.dumps({"case_id": args.case_id, "revision": args.revision, "reviewed": True}))
    elif args.command == "export":
        dataset = EvaluationDataset(candidates=tuple(export_candidate(store, c) for c in args.case_ids))
        private_write(args.output, dataset.model_dump(mode="json"))
        print(json.dumps({"exported": len(dataset.candidates)}))
    else:
        from delta_ai.diagnostics import FakeDiagnosticLLM
        from delta_ai.diagnostic_contract import DiagnosisDraft
        from delta_ai.llm import ClaudeLLM
        llm = FakeDiagnosticLLM() if args.llm == "fake" else ClaudeLLM(output=DiagnosisDraft) if args.llm == "claude" else None
        result = asyncio.run(evaluate(EvaluationDataset.model_validate_json(args.dataset.read_bytes()), llm=llm, store=store))
        result["llm_mode"] = args.llm
        private_write(args.output, result)
        print(json.dumps({k: v for k, v in result.items() if k != "cases"}, ensure_ascii=False))
        return 0 if result["total"] and result["with_cases_passed"] == result["total"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
