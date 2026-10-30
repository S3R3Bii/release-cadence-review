"""Apply a saved teaching model to three authored past intervals, offline."""

import argparse, json
from pathlib import Path
from fractions import Fraction
from cadence import predict
from features import DAY_US

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        assert args.input.stat().st_size <= 65536, "Authored input too large"
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        values = payload["past_interval_days"]
        assert (
            isinstance(values, list) and len(values) == 3
        ), "Supply exactly three past intervals"
        assert all(
            type(v) is int and 0 <= v <= 36500 for v in values
        ), "Days must be bounded nonnegative integers"
        saved = json.loads(
            (ROOT / "results/development/selected_model.json").read_text(
                encoding="utf-8"
            )
        )
        p = predict(saved["model"], sorted(values)[1] * DAY_US)
        print(
            json.dumps(
                dict(
                    status="OFFLINE_TEACHING_ESTIMATE",
                    model=saved["name"],
                    probability_no_stable_GitHub_release_in_90_days=float(p),
                    probability_ratio=str(p),
                    authored_review_flag=bool(p >= Fraction(1, 2)),
                    next_step="Review the project release process and dependency requirements; no message or automatic replacement was made",
                    qualification="Retrospective eight-project benchmark. Not a maintenance, abandonment, security or production-risk diagnosis.",
                )
            )
        )
    except (AssertionError, ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps(dict(status="UNAVAILABLE", reason=str(error))))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
