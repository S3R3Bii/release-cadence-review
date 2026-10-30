"""Recompute saved-source results in a fresh directory without live requests."""

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from develop import execute as develop
from evaluate_final import execute as final

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=["development", "all"], default="all")
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.is_relative_to(ROOT)
    output.mkdir(parents=True, exist_ok=False)
    before = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / "data").glob("*.json")
    }
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        check=True,
    )
    counts = dict(exact_fields=0, tolerance_fields=0)

    def compare(a, b, path):
        assert type(a) is type(b), path
        if isinstance(a, dict):
            assert set(a) == set(b), path
            for key in a:
                compare(a[key], b[key], path + "/" + key)
        elif isinstance(a, list):
            assert len(a) == len(b), path
            for index, (x, y) in enumerate(zip(a, b)):
                compare(x, y, path + "/" + str(index))
        elif isinstance(a, float):
            tolerance = 2e-12 if "log_loss" in path else 2e-15
            assert (
                math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tolerance
            ), path
            counts["tolerance_fields"] += 1
        else:
            assert a == b, path
            counts["exact_fields"] += 1

    phases = (
        ["development"] if args.phase == "development" else ["development", "final"]
    )
    for phase in phases:
        (develop if phase == "development" else final)(output / phase)
        names = sorted(
            path.name
            for path in (ROOT / "results" / phase).glob("*.json")
            if path.name != "execution_runtime.json"
        )
        actual_names = sorted(
            path.name
            for path in (output / phase).glob("*.json")
            if path.name != "execution_runtime.json"
        )
        assert names == actual_names
        for name in names:
            compare(
                json.loads(
                    (ROOT / "results" / phase / name).read_text(encoding="utf-8")
                ),
                json.loads((output / phase / name).read_text(encoding="utf-8")),
                phase + "/" + name,
            )
        subprocess.run(
            [
                sys.executable,
                "src/verify_results.py",
                "--phase",
                phase,
                "--output",
                str(output / (phase + "-independent.json")),
            ],
            cwd=ROOT,
            check=True,
        )
    example = json.loads(
        subprocess.check_output(
            [sys.executable, "src/review_cli.py", "examples/authored-intervals.json"],
            cwd=ROOT,
            text=True,
        )
    )
    assert (
        example["status"] == "OFFLINE_TEACHING_ESTIMATE"
        and 0 <= example["probability_no_stable_GitHub_release_in_90_days"] <= 1
    )
    assert before == {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / "data").glob("*.json")
    }
    result = dict(
        status="PASS_OFFLINE_REPLAY",
        phases=phases,
        **counts,
        source_JSON_files_unchanged=len(before),
        source_network_requests=0,
        authored_CLI_examples=1,
        runtime_provenance="Fresh execution_runtime.json receipts retained separately; original measured runtimes are not expected replay bytes",
        scope="Current captured metadata replay; no historical-vintage recapture, production validation or new source observation"
    )
    (output / "replay_receipt.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
