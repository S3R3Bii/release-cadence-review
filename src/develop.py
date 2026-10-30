"""Retain all nine candidate results using development repositories only."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from features import build
from cadence import fit, predictions, metrics, survival

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2, allow_nan=False) + "\n").encode())


def choose(search):
    def key(row):
        score = row["metrics"]["macro_brier"]
        return (
            Fraction(score["numerator"], score["denominator"]),
            -row["prior_strength"],
            row["cutpoint_days"],
        )

    return min(search, key=key)


def execute(output):
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()
    attempts = []
    try:
        check = json.loads(
            (ROOT / "data/metadata_original_verification.json").read_text(
                encoding="utf-8"
            )
        )
        assert check["status"] == "PASS_INDEPENDENT_NATIVE_METADATA"
        for name, digest in check["input_sha256"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
        metadata = json.loads(
            (ROOT / "data/release_metadata.json").read_text(encoding="utf-8")
        )
        rows, ledger = build(metadata, "development")
        training = [r for r in rows if r["phase"] == "training"]
        validation = [r for r in rows if r["phase"] == "validation"]
        expected = {
            r["repository"]
            for r in json.loads(
                (ROOT / "data/source_plan.json").read_text(encoding="utf-8")
            )["repositories"]
            if r["phase"] == "development"
        }
        for name, members in [("training", training), ("validation", validation)]:
            assert (
                len(members) >= 20 and {r["repository"] for r in members} == expected
            ), (name + " evidence gate failed; do not change horizon or cohort")
        dump(output / "features.json", rows)
        dump(output / "qualification_ledger.json", ledger)
        baseline = fit(training)
        base_predictions = predictions(baseline, validation, "baseline")
        search = []
        all_predictions = list(base_predictions)
        for cutpoint in [14, 30, 60]:
            for strength in [1, 5, 20]:
                candidate_start = time.perf_counter()
                model = fit(training, cutpoint, strength)
                name = "cut" + str(cutpoint) + "-prior" + str(strength)
                predicted = predictions(model, validation, name)
                score = metrics(predicted)
                search.append(
                    dict(
                        name=name,
                        cutpoint_days=cutpoint,
                        prior_strength=strength,
                        model=model,
                        metrics=score,
                    )
                )
                all_predictions += predicted
                attempts.append(
                    dict(
                        name=name,
                        status="PASS",
                        elapsed_seconds=time.perf_counter() - candidate_start,
                    )
                )
        selected = choose(search)
        summary = dict(
            status="DEVELOPMENT_PENDING_INDEPENDENT_CHECK",
            training_origins=len(training),
            validation_origins=len(validation),
            development_repositories=sorted(expected),
            candidate_count=len(search),
            baseline_model=baseline,
            baseline_validation=metrics(base_predictions),
            selected_name=selected["name"],
            selected_validation=selected["metrics"],
            selection="Exact validation macro Brier; ties larger prior strength then smaller cutpoint; training fit is not refit on validation",
            qualifications=[
                "Current edited/deleted-release snapshot, not original historical vintages.",
                "No contributor/bot attribution, security/abandonment label, causal effect or actual maintenance interruption.",
                "Authored eight-project Python-tooling cohort; overlapping release intervals and common ecosystems remain dependent.",
            ],
        )
        dump(output / "search.json", search)
        dump(output / "predictions.json", all_predictions)
        dump(
            output / "selected_model.json",
            dict(
                name=selected["name"],
                model=selected["model"],
                baseline=baseline,
                fit_rows="development training only; no validation refit",
            ),
        )
        dump(output / "summary.json", summary)
        dump(
            output / "survival.json",
            {
                phase: survival([r for r in rows if r["phase"] == phase])
                for phase in ["training", "validation"]
            },
        )
        print(
            json.dumps(
                dict(
                    status=summary["status"],
                    training_origins=len(training),
                    validation_origins=len(validation),
                    selected=summary["selected_name"],
                    baseline_macro_brier=summary["baseline_validation"]["macro_brier"],
                    selected_macro_brier=summary["selected_validation"]["macro_brier"],
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
                elapsed_seconds=time.perf_counter() - start,
                attempts=attempts,
            ),
        )
        raise
    dump(
        output / "execution_runtime.json",
        dict(
            status="PASS",
            started_utc=started,
            completed_utc=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=time.perf_counter() - start,
            attempts=attempts,
            runtime_is_execution_provenance_not_replay_expected_bytes=True,
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "results/development")
    args = parser.parse_args()
    execute(args.output)
