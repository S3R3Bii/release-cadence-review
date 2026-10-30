"""Verify the original committed pre-final dependency manifest and file bytes."""

import hashlib, json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)

    path = "data/dependency_freeze.json"
    raw = (ROOT / path).read_bytes()
    assert raw == git("show", "HEAD:" + path)
    freeze = git("log", "--format=%H", "--", path).decode().splitlines()[-1]
    assert raw == git("show", freeze + ":" + path)
    manifest = json.loads(raw)
    development = manifest["development_commit"]
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", development, freeze],
        cwd=ROOT,
        check=True,
    )
    assert not any(
        name.startswith(("results/final/", "data/final_verification"))
        for name in git("ls-tree", "-r", "--name-only", freeze).decode().splitlines()
    )
    for item in manifest["files"]:
        data = (ROOT / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"] and data == git(
            "show", freeze + ":" + item["path"]
        ), item["path"]
    print(
        json.dumps(
            dict(
                status="PASS_COMMITTED_BEFORE_FINAL_ANALYTIC_EXTRACTION",
                files=len(manifest["files"]),
                development_commit=development,
                freeze_commit=freeze,
                final_features_labels_scores_absent_at_freeze=True,
            )
        )
    )


if __name__ == "__main__":
    main()
