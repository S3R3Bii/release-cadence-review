"""Evaluate other repositories and later dates using the committed selection."""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from features import build
from cadence import predictions, metrics, survival

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2, allow_nan=False) + "\n").encode())


def execute(output):
    subprocess.run([sys.executable, "src/freeze_proof.py"], cwd=ROOT, check=True)
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    begin = time.perf_counter()
    try:
        rows, ledger = build(
            json.loads(
                (ROOT / "data/release_metadata.json").read_text(encoding="utf-8")
            ),
            "final",
        )
        expected = {
            r["repository"]
            for r in json.loads(
                (ROOT / "data/source_plan.json").read_text(encoding="utf-8")
            )["repositories"]
            if r["phase"] == "final"
        }
        assert (
            len(rows) >= 20 and {r["repository"] for r in rows} == expected
        ), "Final evidence gate failed; no cohort/horizon revision"
        selected = json.loads(
            (ROOT / "results/development/selected_model.json").read_text(
                encoding="utf-8"
            )
        )
        all_predictions = []
        scores = {}
        for name, model in [
            ("baseline", selected["baseline"]),
            (selected["name"], selected["model"]),
        ]:
            predicted = predictions(model, rows, name)
            scores[name] = metrics(predicted)
            all_predictions += predicted
        dump(output / "features.json", rows)
        dump(output / "qualification_ledger.json", ledger)
        dump(output / "predictions.json", all_predictions)
        dump(output / "survival.json", dict(final=survival(rows)))
        summary = dict(
            status="FINAL_PENDING_INDEPENDENT_CHECK",
            final_origins=len(rows),
            final_repositories=sorted(expected),
            selected_name=selected["name"],
            models=scores,
            selection_changed=False,
            fit="Original development training only; no final fit or validation refit",
            qualifications=[
                "Current public release snapshot, not original historical vintages.",
                "No release within90 days is not a diagnosis of abandonment or maintenance failure.",
                "Other repository IDs and later dates; common organizations/ecosystem and dependent intervals remain.",
            ],
        )
        dump(output / "summary.json", summary)
        print(
            json.dumps(
                dict(
                    status=summary["status"],
                    origins=len(rows),
                    selected=selected["name"],
                    baseline_macro_brier=scores["baseline"]["macro_brier"],
                    selected_macro_brier=scores[selected["name"]]["macro_brier"],
                )
            )
        )
    except Exception as error:
        dump(
            output / "execution_runtime.json",
            dict(
                status="FAIL",
                error_type=type(error).__name__,
                started_utc=started,
                completed_utc=datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=time.perf_counter() - begin,
            ),
        )
        raise
    dump(
        output / "execution_runtime.json",
        dict(
            status="PASS",
            started_utc=started,
            completed_utc=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=time.perf_counter() - begin,
            runtime_is_execution_provenance_not_replay_expected_bytes=True,
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "results/final")
    args = parser.parse_args()
    execute(args.output)
