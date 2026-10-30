"""Rebuild chronology with SQLite windows and scores with Fraction/Decimal."""

import argparse
import hashlib
import json
import math
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400000000


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("missing time")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("missing timezone")
    dt = dt.astimezone(timezone.utc)
    delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds, dt.year


def reconstruct(group):
    db = sqlite3.connect(":memory:")
    db.execute(
        "create table source(repository text,id integer,published text,draft integer,pre integer,t integer,year integer,position integer)"
    )
    raw = (ROOT / "data/release_metadata.json").read_text(encoding="utf-8")
    source = []
    for index, (value,) in enumerate(
        db.execute(
            "select value from json_each(?) where json_extract(value,'$.phase')=?",
            (raw, group),
        ).fetchall()
    ):
        row = json.loads(value)
        source.append(row)
        try:
            t, year = timestamp(row["published_at"])
        except (ValueError, TypeError):
            t, year = None, None
        db.execute(
            "insert into source values(?,?,?,?,?,?,?,?)",
            (
                row["repository"],
                row["release_id"],
                row["published_at"],
                int(row["draft"]),
                int(row["prerelease"]),
                t,
                year,
                index,
            ),
        )
    windows = db.execute(
        """with qualified as(select * from source where not draft and not pre and t is not null)
    select *,lag(t,1)over w,lag(t,2)over w,lag(t,3)over w,
    lag(id,1)over w,lag(id,2)over w,lag(id,3)over w,lead(t)over w,lead(id)over w
    from qualified window w as(partition by repository order by t,id)"""
    ).fetchall()
    lookup = {r[1]: r for r in windows}
    rows = []
    ledger = []
    for item in source:
        reason = (
            "draft" if item["draft"] else ("prerelease" if item["prerelease"] else None)
        )
        native = lookup.get(item["release_id"])
        if not reason and native is None:
            reason = "missing_or_invalid_published_at"
        if not reason:
            (
                repo,
                identifier,
                published,
                draft,
                pre,
                t,
                year,
                position,
                l1,
                l2,
                l3,
                id1,
                id2,
                id3,
                next_t,
                next_id,
            ) = native
            phase = (
                "training"
                if group == "development" and year in [2020, 2021, 2022]
                else (
                    "validation"
                    if group == "development" and year == 2023
                    else (
                        "final" if group == "final" and year in [2024, 2025] else None
                    )
                )
            )
            if phase is None:
                reason = "outside_origin_years"
            elif l3 is None:
                reason = "insufficient_history"
            else:
                cutoff = timestamp(
                    {
                        "training": "2022-12-31T23:59:59Z",
                        "validation": "2023-12-31T23:59:59Z",
                        "final": "2025-12-31T23:59:59Z",
                    }[phase]
                )[0]
                if t + 90 * DAY > cutoff:
                    reason = "label_window_crosses_boundary"
                else:
                    # SQLite's scalar min/max give an independent median-of-three path.
                    median = db.execute(
                        "select ?-?-min(?-?,?-?,?-?)-max(?-?,?-?,?-?)",
                        (t, l3, t, l1, l1, l2, l2, l3, t, l1, l1, l2, l2, l3),
                    ).fetchone()[0]
                    seen = next_t is not None and next_t <= cutoff
                    rows.append(
                        dict(
                            repository=repo,
                            release_id=identifier,
                            phase=phase,
                            origin_utc=published,
                            prior_release_ids=";".join(map(str, [id3, id2, id1])),
                            median_interval_us=median,
                            label_no_release_90d=int(
                                next_t is None or next_t - t > 90 * DAY
                            ),
                            label_window_end_us=t + 90 * DAY,
                            phase_boundary_us=cutoff,
                            following_release_within_boundary_id=(
                                next_id if seen else None
                            ),
                            followup_us=(next_t if seen else cutoff) - t,
                            followup_event=int(seen),
                        )
                    )
                    reason = "included_" + phase
        ledger.append(
            dict(
                repository=item["repository"],
                release_id=item["release_id"],
                published_at=item["published_at"],
                role=group,
                disposition=reason,
            )
        )
    rows.sort(
        key=lambda r: (
            r["phase"],
            r["repository"],
            timestamp(r["origin_utc"])[0],
            r["release_id"],
        )
    )
    return rows, ledger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["development", "final"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "final":
        subprocess.run([sys.executable, "src/freeze_proof.py"], cwd=ROOT, check=True)
    checks = 0

    def require(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(message)

    def ratio(actual, expected, label):
        require(
            actual["numerator"] == expected.numerator
            and actual["denominator"] == expected.denominator,
            label + " exact ratio",
        )
        require(actual["value"] == float(expected), label + " displayed float")

    def check_metrics(actual, members):
        per = {
            name: [r for r in members if r["repository"] == name]
            for name in sorted({r["repository"] for r in members})
        }
        briers = {
            name: sum(
                (
                    Fraction(r["p_numerator"], r["p_denominator"])
                    - r["label_no_release_90d"]
                )
                ** 2
                for r in rows
            )
            / len(rows)
            for name, rows in per.items()
        }
        with localcontext() as ctx:
            ctx.prec = 75
            logs = {}
            for name, rows in per.items():
                values = []
                for r in rows:
                    q = Fraction(r["p_numerator"], r["p_denominator"])
                    mass = q if r["label_no_release_90d"] else 1 - q
                    values.append(
                        -max(
                            Decimal("1e-12"),
                            Decimal(mass.numerator) / Decimal(mass.denominator),
                        ).ln()
                    )
                logs[name] = sum(values) / len(values)
            require(
                abs(actual["macro_log_loss"] - float(sum(logs.values()) / len(logs)))
                <= 2e-12,
                "independent Decimal macro log loss",
            )
            require(
                abs(
                    actual["micro_log_loss"]
                    - float(
                        sum(logs[name] * len(per[name]) for name in per) / len(members)
                    )
                )
                <= 2e-12,
                "independent Decimal micro log loss",
            )
        db = sqlite3.connect(":memory:")
        db.execute("create table prediction(y integer,flag integer)")
        db.executemany(
            "insert into prediction values(?,?)",
            [(r["label_no_release_90d"], r["review_flag"]) for r in members],
        )
        tp, fp, fn, tn = db.execute(
            "select sum(y=1 and flag=1),sum(y=0 and flag=1),sum(y=1 and flag=0),sum(y=0 and flag=0) from prediction"
        ).fetchone()
        for key, value in dict(
            n=len(members),
            positives=tp + fn,
            tp=tp,
            fp=fp,
            fn=fn,
            tn=tn,
            log_loss_floor=1e-12,
        ).items():
            require(actual[key] == value, "confusion/count " + key)
        ratio(actual["prevalence"], Fraction(tp + fn, len(members)), "prevalence")
        ratio(actual["macro_brier"], sum(briers.values()) / len(briers), "macro Brier")
        ratio(
            actual["micro_brier"],
            sum(briers[name] * len(per[name]) for name in per) / len(members),
            "micro Brier",
        )
        for key, n, d in [("precision", tp, tp + fp), ("recall", tp, tp + fn)]:
            if d:
                ratio(actual[key], Fraction(n, d), key)
            else:
                require(actual[key] is None, "undefined " + key)
        require(len(actual["per_repository"]) == len(per), "all repository metrics")
        for row, name in zip(actual["per_repository"], per):
            require(
                row["repository"] == name
                and row["n"] == len(per[name])
                and row["positives"]
                == sum(r["label_no_release_90d"] for r in per[name]),
                "repository metric identity/count",
            )
            ratio(row["brier"], briers[name], "repository Brier")
            require(
                abs(row["log_loss"] - float(logs[name])) <= 2e-12,
                "repository Decimal log loss",
            )
        for index, binrow in enumerate(actual["calibration"]):
            chosen = [
                r
                for r in members
                if Fraction(index, 5) <= Fraction(r["p_numerator"], r["p_denominator"])
                and (
                    Fraction(r["p_numerator"], r["p_denominator"])
                    < Fraction(index + 1, 5)
                    or index == 4
                )
            ]
            require(
                binrow["bin"] == index
                and binrow["lower"] == index / 5
                and binrow["upper"] == (index + 1) / 5
                and binrow["n"] == len(chosen),
                "calibration bin membership",
            )
            if chosen:
                ratio(
                    binrow["mean_probability"],
                    sum(Fraction(r["p_numerator"], r["p_denominator"]) for r in chosen)
                    / len(chosen),
                    "calibration mean",
                )
                ratio(
                    binrow["observed_fraction"],
                    Fraction(
                        sum(r["label_no_release_90d"] for r in chosen), len(chosen)
                    ),
                    "calibration observed",
                )
            else:
                require(
                    binrow["mean_probability"] is None
                    and binrow["observed_fraction"] is None,
                    "empty bin unknown",
                )

    rows, ledger = reconstruct(args.phase)
    output = ROOT / "results" / args.phase
    actual_rows = read(output / "features.json")
    actual_ledger = read(output / "qualification_ledger.json")
    require(len(rows) == len(actual_rows), "all independent feature rows")
    for expected, actual in zip(rows, actual_rows):
        require(set(expected) == set(actual), "feature schema")
        for name, value in expected.items():
            require(actual[name] == value, "SQLite chronological field " + name)
    require(len(ledger) == len(actual_ledger), "complete qualification ledger")
    for expected, actual in zip(ledger, actual_ledger):
        require(expected == actual, "independent qualification reason")
    for row in rows:
        require(
            timestamp(row["origin_utc"])[0] + 90 * DAY <= row["phase_boundary_us"],
            "complete label window",
        )
        require(
            row["median_interval_us"] >= 0 and row["followup_us"] >= 0,
            "nonnegative intervals",
        )
    development = (
        read(ROOT / "results/development/features.json")
        if args.phase == "final"
        else rows
    )
    training = [r for r in development if r["phase"] == "training"]
    baseline = Fraction(
        sum(r["label_no_release_90d"] for r in training) + 1, len(training) + 2
    )
    search = read(ROOT / "results/development/search.json")
    require(
        len(search) == 9
        and {(r["cutpoint_days"], r["prior_strength"]) for r in search}
        == {(c, s) for c in [14, 30, 60] for s in [1, 5, 20]},
        "complete candidate grid",
    )
    probs = {"baseline": {0: baseline, 1: baseline}}
    for candidate in search:
        cutoff, strength = candidate["cutpoint_days"], candidate["prior_strength"]
        model = candidate["model"]
        ratio(model["baseline_probability"], baseline, "training baseline")
        require(
            model["training_origins"] == len(training)
            and model["training_positives"]
            == sum(r["label_no_release_90d"] for r in training),
            "training-only fit counts",
        )
        q = {}
        for side in [0, 1]:
            stratum = [
                r
                for r in training
                if (r["median_interval_us"] > cutoff * DAY) == bool(side)
            ]
            y = sum(r["label_no_release_90d"] for r in stratum)
            p = (y + strength * baseline) / (len(stratum) + strength)
            stored = model["strata"][side]
            require(
                stored["side"] == side
                and stored["n"] == len(stratum)
                and stored["positives"] == y,
                "training stratum",
            )
            ratio(stored["probability"], p, "independent smoothed probability")
            q[side] = p
        probs[candidate["name"]] = q
    predicted = read(output / "predictions.json")
    by_id = {r["release_id"]: r for r in rows}
    seen = set()
    selected = read(ROOT / "results/development/selected_model.json")
    model_names = (
        ["baseline"] + [r["name"] for r in search]
        if args.phase == "development"
        else ["baseline", selected["name"]]
    )
    target_rows = (
        [r for r in rows if r["phase"] == "validation"]
        if args.phase == "development"
        else rows
    )
    require(
        len(predicted) == len(target_rows) * len(model_names),
        "complete prediction count",
    )
    cutoff_by_name = {r["name"]: r["cutpoint_days"] for r in search}
    for row in predicted:
        key = (row["release_id"], row["model"])
        require(key not in seen and row["model"] in model_names, "prediction identity")
        seen.add(key)
        original = by_id[row["release_id"]]
        require(
            original["phase"]
            == ("validation" if args.phase == "development" else "final"),
            "prediction phase",
        )
        for name, value in original.items():
            require(row[name] == value, "prediction source field " + name)
        side = (
            0
            if row["model"] == "baseline"
            else int(
                original["median_interval_us"] > cutoff_by_name[row["model"]] * DAY
            )
        )
        q = probs[row["model"]][side]
        require(
            (
                row["p_numerator"],
                row["p_denominator"],
                row["probability"],
                row["review_flag"],
            )
            == (q.numerator, q.denominator, float(q), int(q >= Fraction(1, 2))),
            "independent prediction and review flag",
        )
    summary = read(output / "summary.json")
    if args.phase == "development":
        require(
            summary["training_origins"] == len(training)
            and summary["validation_origins"] == len(target_rows),
            "phase counts",
        )
        check_metrics(
            summary["baseline_validation"],
            [r for r in predicted if r["model"] == "baseline"],
        )
        for candidate in search:
            check_metrics(
                candidate["metrics"],
                [r for r in predicted if r["model"] == candidate["name"]],
            )
        winner = min(
            search,
            key=lambda r: (
                Fraction(
                    r["metrics"]["macro_brier"]["numerator"],
                    r["metrics"]["macro_brier"]["denominator"],
                ),
                -r["prior_strength"],
                r["cutpoint_days"],
            ),
        )
        require(
            winner["name"] == summary["selected_name"] == selected["name"]
            and selected["model"] == winner["model"],
            "frozen selection with exact tie rule",
        )
        check_metrics(
            summary["selected_validation"],
            [r for r in predicted if r["model"] == winner["name"]],
        )
    else:
        require(
            not {r["repository"] for r in rows}
            & {r["repository"] for r in development},
            "repository holdout disjointness",
        )
        require(
            all(timestamp(r["origin_utc"])[1] in [2024, 2025] for r in rows),
            "future final dates",
        )
        for name in model_names:
            check_metrics(
                summary["models"][name], [r for r in predicted if r["model"] == name]
            )
    curves = read(output / "survival.json")
    for phase in sorted({r["phase"] for r in rows}):
        members = [r for r in rows if r["phase"] == phase]
        risk = len(members)
        surv = Fraction(1)
        groups = {
            t: [r for r in members if r["followup_us"] == t]
            for t in sorted({r["followup_us"] for r in members})
        }
        require(len(curves[phase]) == len(groups), "complete survival event times")
        for row, (t, at_time) in zip(curves[phase], groups.items()):
            events = sum(r["followup_event"] for r in at_time)
            censored = len(at_time) - events
            require(
                (row["followup_us"], row["at_risk"], row["events"], row["censored"])
                == (t, risk, events, censored),
                "independent survival risk set",
            )
            surv *= 1 - Fraction(events, risk)
            ratio(row["survival"], surv, "survival product")
            risk -= len(at_time)
        require(risk == 0, "exhausted survival risk set")
    result = dict(
        status="PASS_INDEPENDENT_SQLITE_FRACTION_DECIMAL",
        phase=args.phase,
        conditions=checks,
        feature_rows=len(rows),
        qualification_rows=len(ledger),
        prediction_rows=len(predicted),
        source_network_requests=0,
        log_tolerance=2e-12,
        scope="Finite retrospective chronological, source-field and exact arithmetic checks; no original-vintage, independence or operational-maintenance claim",
    )
    args.output.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
