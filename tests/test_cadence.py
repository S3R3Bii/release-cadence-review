import math
import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from features import build, micros, DAY_US
from cadence import fit, predict, metrics, survival
from develop import choose


def release(identifier, date, group="development", **extra):
    return dict(
        repository="authored/project",
        release_id=identifier,
        phase=group,
        published_at=date,
        draft=False,
        prerelease=False,
        **extra
    )


class Chronology(unittest.TestCase):
    def sequence(self, last):
        return [
            release(1, "2019-12-01T00:00:00Z"),
            release(2, "2019-12-11T00:00:00Z"),
            release(3, "2019-12-21T00:00:00Z"),
            release(4, "2020-01-01T00:00:00Z"),
            release(5, last),
        ]

    def test_exact_ninety_day_next_release_is_within_window(self):
        rows, _ = build(self.sequence("2020-03-31T00:00:00Z"), "development")
        self.assertEqual(rows[0]["label_no_release_90d"], 0)

    def test_one_microsecond_after_window_is_a_gap(self):
        rows, _ = build(self.sequence("2020-03-31T00:00:00.000001Z"), "development")
        self.assertEqual(rows[0]["label_no_release_90d"], 1)

    def test_future_change_cannot_change_past_feature(self):
        a, _ = build(self.sequence("2020-02-01T00:00:00Z"), "development")
        b, _ = build(self.sequence("2021-02-01T00:00:00Z"), "development")
        self.assertEqual(a[0]["median_interval_us"], b[0]["median_interval_us"])
        self.assertNotEqual(a[0]["label_no_release_90d"], b[0]["label_no_release_90d"])

    def test_same_timestamp_follows_release_id_order(self):
        source = self.sequence("2020-01-01T00:00:00Z")
        rows, _ = build(list(reversed(source)), "development")
        self.assertEqual(rows[0]["release_id"], 4)
        self.assertEqual(rows[0]["followup_us"], 0)

    def test_drafts_prereleases_and_missing_time_retained(self):
        source = self.sequence("2020-02-01T00:00:00Z")
        source[0]["draft"] = True
        source[1]["prerelease"] = True
        source[2]["published_at"] = None
        rows, ledger = build(source, "development")
        self.assertEqual(rows, [])
        self.assertTrue(
            {
                "draft",
                "prerelease",
                "missing_or_invalid_published_at",
                "insufficient_history",
            }
            <= {r["disposition"] for r in ledger}
        )

    def test_timezone_required(self):
        with self.assertRaises(ValueError):
            micros("2020-01-01T00:00:00")

    def test_late_origin_is_excluded_not_imputed(self):
        rows, ledger = build(self.sequence("2022-12-30T00:00:00Z"), "development")
        self.assertIn(
            "label_window_crosses_boundary", {r["disposition"] for r in ledger}
        )
        self.assertEqual(len(rows), 1)

    def test_other_repository_phase_not_parsed(self):
        rows, ledger = build(
            [release(1, "malformed final timestamp", group="final")], "development"
        )
        self.assertEqual((rows, ledger), ([], []))


class ProbabilityChecks(unittest.TestCase):
    def training(self):
        return [
            dict(phase="training", median_interval_us=DAY_US, label_no_release_90d=0),
            dict(
                phase="training", median_interval_us=2 * DAY_US, label_no_release_90d=1
            ),
        ]

    def test_empty_stratum_returns_baseline(self):
        model = fit(self.training(), 14, 5)
        self.assertEqual(predict(model, 100 * DAY_US), Fraction(1, 2))

    def test_fit_rejects_validation_labels(self):
        rows = self.training()
        rows[0]["phase"] = "validation"
        with self.assertRaises(AssertionError):
            fit(rows, 14, 5)

    def test_cutpoint_boundary_is_in_lower_stratum(self):
        model = fit(self.training(), 14, 1)
        self.assertEqual(
            predict(model, 14 * DAY_US),
            Fraction(
                model["strata"][0]["probability"]["numerator"],
                model["strata"][0]["probability"]["denominator"],
            ),
        )

    def test_zero_positive_and_alert_denominators_remain_unavailable(self):
        result = metrics(
            [
                dict(
                    repository="authored",
                    p_numerator=1,
                    p_denominator=10,
                    label_no_release_90d=0,
                    review_flag=0,
                )
            ]
        )
        self.assertIsNone(result["recall"])
        self.assertIsNone(result["precision"])
        self.assertEqual(result["micro_brier"]["numerator"], 1)
        self.assertEqual(result["micro_brier"]["denominator"], 100)

    def test_log_floor_at_zero_mass(self):
        result = metrics(
            [
                dict(
                    repository="authored",
                    p_numerator=1,
                    p_denominator=1,
                    label_no_release_90d=0,
                    review_flag=1,
                )
            ]
        )
        self.assertAlmostEqual(result["micro_log_loss"], -math.log(1e-12), places=12)

    def test_exact_tie_prefers_stronger_prior_then_lower_cut(self):
        candidates = [
            dict(
                cutpoint_days=c,
                prior_strength=s,
                metrics={"macro_brier": {"numerator": 1, "denominator": 10}},
            )
            for c, s in [(60, 20), (14, 5), (14, 20)]
        ]
        self.assertEqual(
            (choose(candidates)["cutpoint_days"], choose(candidates)["prior_strength"]),
            (14, 20),
        )

    def test_censoring_and_tied_events_use_risk_set(self):
        result = survival(
            [
                dict(followup_us=1, followup_event=1),
                dict(followup_us=1, followup_event=0),
                dict(followup_us=2, followup_event=1),
            ]
        )
        self.assertEqual(
            result[0]["survival"], dict(numerator=2, denominator=3, value=2 / 3)
        )
        self.assertEqual(result[-1]["survival"]["numerator"], 0)


if __name__ == "__main__":
    unittest.main()
