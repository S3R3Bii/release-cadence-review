"""Independently reconstruct minimal facts with DuckDB's JSON reader and SQL."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import duckdb

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--originals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original = args.originals.resolve()
    assert not original.is_relative_to(ROOT)
    receipts = json.loads(
        (original / "acquisition-receipts.json").read_text(encoding="utf-8")
    )
    public = json.loads(
        (ROOT / "data/source_receipts.json").read_text(encoding="utf-8")
    )
    repositories = json.loads(
        (ROOT / "data/repository_metadata.json").read_text(encoding="utf-8")
    )
    releases = json.loads(
        (ROOT / "data/release_metadata.json").read_text(encoding="utf-8")
    )
    plan = json.loads((ROOT / "data/source_plan.json").read_text(encoding="utf-8"))
    db = duckdb.connect()
    checks = 0

    def require(value, message):
        nonlocal checks
        checks += 1
        if not value:
            raise AssertionError(message)

    ordered = sorted(
        [r["repository"] for r in plan["repositories"]],
        key=lambda name: hashlib.sha256(name.lower().encode()).hexdigest(),
    )
    require(
        [r["repository"] for r in plan["repositories"]] == ordered,
        "fixed assignment order",
    )
    for index, row in enumerate(plan["repositories"]):
        require(
            row["phase"] == ("development" if index % 2 == 0 else "final"),
            "group assignment",
        )
        require(
            row["assignment_sha256"]
            == hashlib.sha256(row["repository"].lower().encode()).hexdigest(),
            "assignment hash",
        )
    require(
        len(receipts) == 18 and len(repositories) == 8,
        "actual complete capture envelope",
    )
    require(
        len({r["url"] for r in receipts}) == len(receipts),
        "no repeated completed source request",
    )
    native = {}
    previous = None
    original_hashes = {}
    for receipt, safe in zip(receipts, public["originals"]):
        path = original / receipt["body_file"]
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        original_hashes[path.name] = digest
        require(
            digest == receipt["body_sha256"] and len(body) == receipt["stored_bytes"],
            "exact original body",
        )
        require(
            receipt["http_status"] == 200
            and not receipt.get("body_truncated")
            and not receipt.get("transport_error"),
            "usable captured response",
        )
        require(
            {
                key: value
                for key, value in receipt.items()
                if key not in ["body_file", "private_error"]
            }
            == safe,
            "sanitized receipt fields",
        )
        require(
            urlsplit(receipt["url"]).scheme == "https"
            and urlsplit(receipt["url"]).netloc == "api.github.com",
            "API origin",
        )
        start, end = map(
            datetime.fromisoformat, [receipt["started_utc"], receipt["completed_utc"]]
        )
        require(
            start.tzinfo is not None and end.tzinfo is not None and end >= start,
            "actual capture interval",
        )
        if previous:
            require(
                (start - previous).total_seconds() >= 2, "actual inter-request spacing"
            )
        previous = end
        native[digest] = (body.decode("utf-8"), receipt)
    independent_releases = []
    for item, repo in zip(plan["repositories"], repositories):
        body, receipt = native[repo["metadata_sha256"]]
        result = db.execute(
            "select json_extract_string(?,'$.full_name'), cast(json_extract_string(?,'$.id') as bigint), json_extract_string(?,'$.private'), json_extract_string(?,'$.fork'), json_extract_string(?,'$.archived')",
            [body] * 5,
        ).fetchone()
        require(
            result[0].lower() == item["repository"].lower()
            and result[1] == repo["repository_id"],
            "native repository ID",
        )
        require(
            result[2] == "false" and result[3] == "false",
            "public non-fork source cohort",
        )
        require(
            repo["fork"] is False
            and repo["archived"] == (result[4] == "true")
            and repo["phase"] == item["phase"],
            "repository source fields",
        )
        pages = [
            page for page in public["pages"] if page["repository"] == item["repository"]
        ]
        require(
            1 <= len(pages) <= 6
            and [p["page"] for p in pages] == list(range(1, len(pages) + 1)),
            "complete bounded page sequence",
        )
        for index, page in enumerate(pages):
            body, receipt = native[page["sha256"]]
            parsed = urlsplit(receipt["url"])
            paths = [
                "/repos/" + item["repository"].lower() + "/releases",
                "/repositories/" + str(repo["repository_id"]) + "/releases",
            ]
            require(
                parsed.path.lower() in paths
                and parse_qs(parsed.query)
                == dict(per_page=["100"], page=[str(index + 1)]),
                "verified page identity",
            )
            link = receipt["headers"].get("Link", "")
            nexts = [
                part.split(">", 1)[0].strip().lstrip("<")
                for part in link.split(",")
                if 'rel="next"' in part
            ]
            require(
                bool(nexts) == page["has_next"] and len(nexts) <= 1,
                "native pagination state",
            )
            require(bool(nexts) == (index < len(pages) - 1), "actual pagination end")
            if nexts:
                require(
                    nexts[0] == native[pages[index + 1]["sha256"]][1]["url"],
                    "follow exact publisher next URL",
                )
            result = db.execute(
                "select cast(json_extract_string(value,'$.id') as bigint), json_extract_string(value,'$.created_at'), json_extract_string(value,'$.published_at'), json_extract_string(value,'$.draft'), json_extract_string(value,'$.prerelease') from json_each(?) order by cast(key as integer)",
                [body],
            ).fetchall()
            require(len(result) == page["rows"] <= 100, "native page row count")
            for identifier, created, published, draft, prerelease in result:
                require(
                    draft in ["true", "false"] and prerelease in ["true", "false"],
                    "native boolean states",
                )
                independent_releases.append(
                    dict(
                        repository=item["repository"],
                        repository_id=repo["repository_id"],
                        phase=item["phase"],
                        release_id=identifier,
                        created_at=created,
                        published_at=published,
                        draft=draft == "true",
                        prerelease=prerelease == "true",
                        source_sha256=page["sha256"],
                        source_page=index + 1,
                    )
                )
    require(
        len(independent_releases) == len(releases) == 484, "all source release facts"
    )
    for expected, actual in zip(independent_releases, releases):
        require(set(expected) == set(actual), "minimal public schema")
        for key, value in expected.items():
            require(actual[key] == value, "independent native field: " + key)
    require(
        len({r["release_id"] for r in releases}) == len(releases),
        "unique source release IDs",
    )
    require(
        sum(
            path.stat().st_size
            for path in (original / name for name in original_hashes)
        )
        == 2248070,
        "actual byte count",
    )
    for name, digest in original_hashes.items():
        require(
            hashlib.sha256((original / name).read_bytes()).hexdigest() == digest,
            "original unchanged",
        )
    result = dict(
        status="PASS_INDEPENDENT_NATIVE_METADATA",
        conditions=checks,
        source_files=len(original_hashes),
        source_bytes=2248070,
        source_release_rows=len(releases),
        repositories=len(repositories),
        native_reader="DuckDB "
        + duckdb.__version__
        + " JSON parser and SQL; no collector helper imports",
        final_outcomes_analytically_extracted=False,
        input_sha256={
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in [
                "data/source_plan.json",
                "data/release_metadata.json",
                "data/repository_metadata.json",
                "data/source_receipts.json",
            ]
        },
        scope="Current minimal release facts and complete API pagination; not historical vintages, all Git tags, maintenance status or future scores",
    )
    args.output.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
