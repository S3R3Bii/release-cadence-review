"""Small authored probability benchmark with exact Brier arithmetic."""

import math
from collections import Counter, defaultdict
from fractions import Fraction
from features import DAY_US


def rational(value):
    return dict(
        numerator=value.numerator, denominator=value.denominator, value=float(value)
    )


def fit(rows, cutpoint=None, strength=None):
    assert rows and all(row["phase"] == "training" for row in rows)
    baseline = Fraction(
        sum(row["label_no_release_90d"] for row in rows) + 1, len(rows) + 2
    )
    bins = []
    if cutpoint is not None:
        assert cutpoint in [14, 30, 60] and strength in [1, 5, 20]
        for side in [0, 1]:
            members = [
                r
                for r in rows
                if int(r["median_interval_us"] > cutpoint * DAY_US) == side
            ]
            positive = sum(r["label_no_release_90d"] for r in members)
            probability = (positive + strength * baseline) / (len(members) + strength)
            bins.append(
                dict(
                    side=side,
                    n=len(members),
                    positives=positive,
                    probability=rational(probability),
                )
            )
    return dict(
        cutpoint_days=cutpoint,
        prior_strength=strength,
        baseline_probability=rational(baseline),
        strata=bins,
        training_origins=len(rows),
        training_positives=sum(r["label_no_release_90d"] for r in rows),
    )


def predict(model, median_interval_us):
    if model["cutpoint_days"] is None:
        value = model["baseline_probability"]
    else:
        side = int(median_interval_us > model["cutpoint_days"] * DAY_US)
        value = model["strata"][side]["probability"]
    return Fraction(value["numerator"], value["denominator"])


def predictions(model, rows, name):
    result = []
    for row in rows:
        p = predict(model, row["median_interval_us"])
        result.append(
            dict(
                row,
                model=name,
                p_numerator=p.numerator,
                p_denominator=p.denominator,
                probability=float(p),
                review_flag=int(p >= Fraction(1, 2)),
            )
        )
    return result


def metrics(rows):
    assert rows
    groups = defaultdict(list)
    for row in rows:
        groups[row["repository"]].append(row)
    reports = []
    all_losses = []
    for repository, members in sorted(groups.items()):
        brier = sum(
            (Fraction(r["p_numerator"], r["p_denominator"]) - r["label_no_release_90d"])
            ** 2
            for r in members
        ) / len(members)
        losses = []
        for r in members:
            p = Fraction(r["p_numerator"], r["p_denominator"])
            assert 0 <= p <= 1
            mass = p if r["label_no_release_90d"] else 1 - p
            losses.append(-math.log(max(1e-12, float(mass))))
        all_losses += losses
        reports.append(
            dict(
                repository=repository,
                n=len(members),
                positives=sum(r["label_no_release_90d"] for r in members),
                brier=rational(brier),
                log_loss=sum(losses) / len(losses),
            )
        )
    tp = sum(r["review_flag"] and r["label_no_release_90d"] for r in rows)
    fp = sum(r["review_flag"] and not r["label_no_release_90d"] for r in rows)
    fn = sum(not r["review_flag"] and r["label_no_release_90d"] for r in rows)
    tn = len(rows) - tp - fp - fn
    macro = sum(
        Fraction(r["brier"]["numerator"], r["brier"]["denominator"]) for r in reports
    ) / len(reports)
    micro = sum(
        (Fraction(r["p_numerator"], r["p_denominator"]) - r["label_no_release_90d"])
        ** 2
        for r in rows
    ) / len(rows)
    calibration = []
    for index in range(5):
        members = [
            r
            for r in rows
            if min(4, int(Fraction(r["p_numerator"], r["p_denominator"]) * 5)) == index
        ]
        calibration.append(
            dict(
                bin=index,
                lower=index / 5,
                upper=(index + 1) / 5,
                n=len(members),
                mean_probability=(
                    rational(
                        sum(
                            Fraction(r["p_numerator"], r["p_denominator"])
                            for r in members
                        )
                        / len(members)
                    )
                    if members
                    else None
                ),
                observed_fraction=(
                    rational(
                        Fraction(
                            sum(r["label_no_release_90d"] for r in members),
                            len(members),
                        )
                    )
                    if members
                    else None
                ),
            )
        )
    return dict(
        n=len(rows),
        positives=tp + fn,
        prevalence=rational(Fraction(tp + fn, len(rows))),
        macro_brier=rational(macro),
        micro_brier=rational(micro),
        macro_log_loss=sum(r["log_loss"] for r in reports) / len(reports),
        micro_log_loss=sum(all_losses) / len(rows),
        log_loss_floor=1e-12,
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        precision=rational(Fraction(tp, tp + fp)) if tp + fp else None,
        recall=rational(Fraction(tp, tp + fn)) if tp + fn else None,
        per_repository=reports,
        calibration=calibration,
    )


def survival(rows):
    times = defaultdict(Counter)
    for row in rows:
        times[row["followup_us"]]["event" if row["followup_event"] else "censored"] += 1
    risk = len(rows)
    remaining = Fraction(1)
    result = []
    for timestamp, counts in sorted(times.items()):
        events, censored = counts["event"], counts["censored"]
        remaining *= Fraction(risk - events, risk)
        result.append(
            dict(
                followup_us=timestamp,
                at_risk=risk,
                events=events,
                censored=censored,
                survival=rational(remaining),
            )
        )
        risk -= events + censored
    assert risk == 0
    return result
