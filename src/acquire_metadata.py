"""Bounded public release metadata capture; no source assets or user exports."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import requests

ROOT = Path(__file__).resolve().parents[1]
CAP = 8 * 1024 * 1024
TOTAL_CAP = 160 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode())


class PublicAuth(requests.auth.AuthBase):
    def __call__(self, request):
        assert "Authorization" not in request.headers
        return request


def check_next_url(url, repository, repository_id, page):
    parsed = urlsplit(url)
    assert (
        parsed.scheme == "https"
        and parsed.netloc == "api.github.com"
        and not parsed.fragment
    )
    assert parsed.path.lower() in [
        ("/repos/" + repository + "/releases").lower(),
        "/repositories/" + str(repository_id) + "/releases",
    ]
    query = parse_qs(parsed.query)
    assert (
        set(query) == {"per_page", "page"}
        and query["page"] == [str(page)]
        and query["per_page"] == ["100"]
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--resume-captured", action="store_true")
    args = parser.parse_args()
    private = args.private_root.resolve()
    assert not private.is_relative_to(ROOT)
    receipts = []
    if args.resume_captured:
        assert private.is_dir()
        marker = private / "cached-resume.json"
        assert not marker.exists(), "One targeted implementation resume only"
        receipts = json.loads(
            (private / "acquisition-receipts.json").read_text(encoding="utf-8")
        )
        assert all(
            row.get("http_status") == 200
            and not row.get("body_truncated")
            and not row.get("transport_error")
            for row in receipts
        ), "Do not use this path to retry denied source access"
        dump(
            marker,
            dict(
                started_utc=now(),
                cached_successful_responses=len(receipts),
                new_source_attempts_only=True,
            ),
        )
    else:
        private.mkdir(parents=True, exist_ok=False)
    session = requests.Session()
    session.auth = PublicAuth()
    session.headers.update(
        {
            "User-Agent": "ReleaseCadenceReview/0.1 (bounded research metadata)",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    cached = {row["url"]: row for row in receipts}
    assert len(cached) == len(receipts)
    consumed = sum(row["stored_bytes"] for row in receipts)
    last = None

    def capture(url):
        nonlocal consumed, last
        parsed = urlsplit(url)
        assert (
            parsed.scheme == "https"
            and parsed.netloc == "api.github.com"
            and not parsed.fragment
            and not parsed.username
        )
        assert set(parse_qs(parsed.query)) <= {"per_page", "page"}
        if url in cached:
            receipt = cached[url]
            body = (private / receipt["body_file"]).read_bytes()
            assert (
                len(body) == receipt["stored_bytes"]
                and hashlib.sha256(body).hexdigest() == receipt["body_sha256"]
            )
            return json.loads(body), receipt
        if last is not None:
            time.sleep(max(0, 2 - (time.monotonic() - last)))
        session.cookies.clear()
        started = now()
        number = len(receipts)
        path = private / ("response-" + str(number).zfill(3) + ".json")
        body = b""
        receipt = dict(url=url, started_utc=started, body_file=path.name)
        try:
            with session.get(
                url, timeout=(15, 45), stream=True, allow_redirects=False
            ) as response:
                receipt.update(
                    http_status=response.status_code,
                    headers={
                        key: response.headers[key]
                        for key in [
                            "Content-Type",
                            "Date",
                            "ETag",
                            "Link",
                            "Retry-After",
                            "X-RateLimit-Remaining",
                            "X-RateLimit-Reset",
                            "X-RateLimit-Limit",
                        ]
                        if key in response.headers
                    },
                )
                parts = []
                for chunk in response.iter_content(65536):
                    remaining = CAP - sum(map(len, parts))
                    parts.append(chunk[:remaining])
                    if len(chunk) > remaining:
                        receipt["body_truncated"] = True
                        break
                body = b"".join(parts)
        except (requests.RequestException, OSError) as error:
            receipt.update(
                transport_error=type(error).__name__, private_error=str(error)
            )
        finally:
            last = time.monotonic()
            path.write_bytes(body)
            consumed += len(body)
            receipt.update(
                completed_utc=now(),
                stored_bytes=len(body),
                body_sha256=hashlib.sha256(body).hexdigest(),
            )
            receipts.append(receipt)
            dump(private / "acquisition-receipts.json", receipts)
        assert consumed <= TOTAL_CAP, "Private capture budget reached"
        if (
            receipt.get("http_status") != 200
            or receipt.get("body_truncated")
            or receipt.get("transport_error")
        ):
            dump(
                private / "source-gate.json",
                dict(
                    status="INCOMPLETE_SOURCE_RESPONSE",
                    last_receipt=receipt,
                    cohort_released=False,
                    retry_policy="No automatic retry. Preserve this attempt; honor supplied Retry-After/reset before at most one targeted retry after external-state review. No identity or endpoint rotation.",
                ),
            )
            raise RuntimeError(
                "Source response unavailable; preserved private receipt, no cohort released"
            )
        return json.loads(body), receipt

    rate, rate_receipt = capture("https://api.github.com/rate_limit")
    remaining = rate["resources"]["core"]["remaining"]
    if remaining < 57:
        dump(
            private / "source-gate.json",
            dict(
                status="INCOMPLETE_RATE_ALLOWANCE",
                observed_remaining=remaining,
                required_remaining=57,
                reset_unix=rate["resources"]["core"]["reset"],
                cohort_requested=False,
                cohort_released=False,
            ),
        )
        raise RuntimeError(
            "Insufficient public API allowance for the fixed bound; no cohort requested"
        )
    plan = json.loads((ROOT / "data/source_plan.json").read_text(encoding="utf-8"))
    records, repositories, pages = [], [], []
    all_ids = set()
    for item in plan["repositories"]:
        name = item["repository"]
        meta, receipt = capture("https://api.github.com/repos/" + name)
        assert (
            meta["full_name"].lower() == name.lower()
            and meta["private"] is False
            and meta["fork"] is False
        )
        repositories.append(
            dict(
                repository=name,
                repository_id=meta["id"],
                phase=item["phase"],
                fork=meta["fork"],
                archived=meta["archived"],
                metadata_sha256=receipt["body_sha256"],
            )
        )
        url = "https://api.github.com/repos/" + name + "/releases?per_page=100&page=1"
        ended = False
        for number in range(1, plan["maximum_release_pages_per_repository"] + 1):
            data, receipt = capture(url)
            assert isinstance(data, list) and len(data) <= 100
            for row in data:
                identifier = row["id"]
                assert (
                    type(identifier) is int and identifier not in all_ids
                ), "Duplicate release ID or pagination overlap"
                all_ids.add(identifier)
                assert type(row["draft"]) is bool and type(row["prerelease"]) is bool
                records.append(
                    dict(
                        repository=name,
                        repository_id=meta["id"],
                        phase=item["phase"],
                        release_id=identifier,
                        created_at=row.get("created_at"),
                        published_at=row.get("published_at"),
                        draft=row["draft"],
                        prerelease=row["prerelease"],
                        source_sha256=receipt["body_sha256"],
                        source_page=number,
                    )
                )
            links = requests.utils.parse_header_links(
                receipt["headers"].get("Link", "")
            )
            following = [link["url"] for link in links if link.get("rel") == "next"]
            assert len(following) <= 1
            pages.append(
                dict(
                    repository=name,
                    page=number,
                    rows=len(data),
                    has_next=bool(following),
                    sha256=receipt["body_sha256"],
                )
            )
            if not following:
                ended = True
                break
            url = following[0]
            check_next_url(url, name, meta["id"], number + 1)
        assert (
            ended
        ), "Complete-snapshot pagination gate failed; do not truncate or extend"
        print(
            json.dumps(
                dict(
                    repository=name,
                    source_pages=sum(p["repository"] == name for p in pages),
                    source_rows=sum(r["repository"] == name for r in records),
                    outcome_analysis="not_extracted",
                )
            ),
            flush=True,
        )
    for filename in [
        "release_metadata.json",
        "repository_metadata.json",
        "source_receipts.json",
    ]:
        assert not (ROOT / "data" / filename).exists()
    dump(ROOT / "data/release_metadata.json", records)
    dump(ROOT / "data/repository_metadata.json", repositories)
    dump(
        ROOT / "data/source_receipts.json",
        dict(
            status="CAPTURED_PENDING_INDEPENDENT_SOURCE_CHECK",
            pages=pages,
            originals=[
                {
                    key: value
                    for key, value in row.items()
                    if key not in ["body_file", "private_error"]
                }
                for row in receipts
            ],
            minimal_fields_only=True,
            source_assets_downloaded=False,
            final_outcomes_analytically_extracted=False,
        ),
    )
    dump(
        private / "source-gate.json",
        dict(
            status="CAPTURED_PENDING_INDEPENDENT_SOURCE_CHECK",
            files=len(receipts),
            bytes=consumed,
            releases=len(records),
            repositories=len(repositories),
        ),
    )
    print(
        json.dumps(
            dict(
                status="CAPTURED_PENDING_INDEPENDENT_SOURCE_CHECK",
                files=len(receipts),
                bytes=consumed,
                releases=len(records),
            )
        )
    )


if __name__ == "__main__":
    main()
