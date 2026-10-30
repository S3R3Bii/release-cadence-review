"""Build chronological release-gap rows from current public metadata facts."""

from collections import defaultdict
from datetime import datetime, timezone

DAY_US = 86400 * 1000000
HORIZON_US = 90 * DAY_US
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
BOUNDARIES = {
    "training": "2022-12-31T23:59:59+00:00",
    "validation": "2023-12-31T23:59:59+00:00",
    "final": "2025-12-31T23:59:59+00:00",
}


def moment(value):
    if not isinstance(value, str):
        raise ValueError("Published timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Published timestamp needs a timezone")
    return parsed.astimezone(timezone.utc)


def micros(value):
    delta = moment(value) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def build(metadata, group):
    assert group in ["development", "final"]
    records = [row for row in metadata if row["phase"] == group]
    by_repo = defaultdict(list)
    ledger = []
    for item in records:
        entry = dict(
            repository=item["repository"],
            release_id=item["release_id"],
            published_at=item["published_at"],
            role=group,
            disposition=None,
        )
        reason = (
            "draft" if item["draft"] else ("prerelease" if item["prerelease"] else None)
        )
        if not reason:
            try:
                timestamp = micros(item["published_at"])
            except (ValueError, TypeError):
                reason = "missing_or_invalid_published_at"
        if reason:
            entry["disposition"] = reason
        else:
            by_repo[item["repository"]].append(
                (timestamp, item["release_id"], item, entry)
            )
        ledger.append(entry)
    rows = []
    for repository, events in sorted(by_repo.items()):
        events.sort(key=lambda item: (item[0], item[1]))
        for index, (origin, identifier, item, entry) in enumerate(events):
            year = moment(item["published_at"]).year
            phase = (
                "training"
                if group == "development" and 2020 <= year <= 2022
                else (
                    "validation"
                    if group == "development" and year == 2023
                    else (
                        "final" if group == "final" and 2024 <= year <= 2025 else None
                    )
                )
            )
            if phase is None:
                entry["disposition"] = "outside_origin_years"
                continue
            if index < 3:
                entry["disposition"] = "insufficient_history"
                continue
            boundary = micros(BOUNDARIES[phase])
            if origin + HORIZON_US > boundary:
                entry["disposition"] = "label_window_crosses_boundary"
                continue
            intervals = [
                events[j][0] - events[j - 1][0] for j in range(index - 2, index + 1)
            ]
            median_us = sorted(intervals)[1]
            following = events[index + 1] if index + 1 < len(events) else None
            event_seen = following is not None and following[0] <= boundary
            delay = following[0] - origin if event_seen else boundary - origin
            no_release_90d = int(
                following is None or following[0] > origin + HORIZON_US
            )
            rows.append(
                dict(
                    repository=repository,
                    release_id=identifier,
                    phase=phase,
                    origin_utc=item["published_at"],
                    prior_release_ids=";".join(
                        str(events[j][1]) for j in range(index - 3, index)
                    ),
                    median_interval_us=median_us,
                    label_no_release_90d=no_release_90d,
                    label_window_end_us=origin + HORIZON_US,
                    phase_boundary_us=boundary,
                    following_release_within_boundary_id=(
                        following[1] if event_seen else None
                    ),
                    followup_us=delay,
                    followup_event=int(event_seen),
                )
            )
            entry["disposition"] = "included_" + phase
    rows.sort(
        key=lambda row: (
            row["phase"],
            row["repository"],
            micros(row["origin_utc"]),
            row["release_id"],
        )
    )
    return rows, ledger
