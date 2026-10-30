"""Recompute displayed values and inspect the actual SVG data coordinates."""

import hashlib, json, re
from fractions import Fraction as F
from pathlib import Path
from xml.etree import ElementTree as E

ROOT = Path(__file__).resolve().parents[1]
SVG = "{http://www.w3.org/2000/svg}"


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def vertices(node):
    path = node.find(".//" + SVG + "path")
    assert path is not None
    values = [
        float(v)
        for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", path.attrib["d"])
    ]
    assert len(values) % 2 == 0
    return list(zip(values[::2], values[1::2]))


def brier(rows, repository=None):
    if repository is not None:
        members = [r for r in rows if r["repository"] == repository]
        return sum(
            (F(r["p_numerator"], r["p_denominator"]) - r["label_no_release_90d"]) ** 2
            for r in members
        ) / len(members)
    repos = sorted({r["repository"] for r in rows})
    return sum(brier(rows, r) for r in repos) / len(repos)


def main():
    dev = read("results/development/predictions.json")
    final = read("results/final/predictions.json")
    maps = read("figures/value_map.json")
    values = coordinates = hashes = 0
    assert len(maps) == 3
    for figure in maps:
        tree = E.parse(ROOT / "figures" / (figure["figure"] + ".svg"))
        ids = {n.get("id"): n for n in tree.iter() if n.get("id")}
        areas = []
        for i, axis in enumerate(figure["axes"]):
            points = vertices(ids["data-area-" + str(i)])
            areas.append(
                (
                    min(x for x, y in points),
                    max(x for x, y in points),
                    min(y for x, y in points),
                    max(y for x, y in points),
                )
            )
        for item in figure["items"]:
            axis = figure["axes"][item["axis"]]
            xmin, xmax = axis["xlim"]
            ymin, ymax = axis["ylim"]
            kind = item["kind"]
            if kind == "search":
                name = "cut" + str(item["cutpoint"]) + "-prior" + str(item["strength"])
                value = float(brier([r for r in dev if r["model"] == name]))
                x = [14, 30, 60].index(item["cutpoint"]) + [-0.23, 0, 0.23][
                    [1, 5, 20].index(item["strength"])
                ]
                assert (
                    axis == dict(xlim=[-0.6, 2.6], ylim=[0, 0.4])
                    and item["x"] == x
                    and item["width"] == 0.20
                )
                expected = [
                    (x - 0.10, 0),
                    (x + 0.10, 0),
                    (x + 0.10, value),
                    (x - 0.10, value),
                ]
                assert item["display"] == f"{value:.3f}" and item["display"] in "".join(
                    tree.getroot().itertext()
                )
            elif kind == "baseline_line":
                value = float(brier([r for r in dev if r["model"] == "baseline"]))
                expected = [(xmin, value), (xmax, value)]
            elif kind == "final_brier":
                rows = [r for r in final if r["model"] == item["model"]]
                repos = sorted({r["repository"] for r in rows})
                j = item["repository_index"]
                value = float(brier(rows, repos[j] if j < 4 else None))
                y = 4 - j + (0.14 if item["model"] == "baseline" else -0.14)
                assert (
                    axis == dict(xlim=[0, 0.6], ylim=[-0.6, 4.6])
                    and item["x"] == 0
                    and item["y"] == y
                    and item["height"] == 0.25
                )
                expected = [
                    (0, y - 0.125),
                    (value, y - 0.125),
                    (value, y + 0.125),
                    (0, y + 0.125),
                ]
                assert item["display"] == f"{value:.3f}" and item["display"] in "".join(
                    tree.getroot().itertext()
                )
            elif kind == "calibration":
                rows = [
                    r
                    for r in final
                    if r["model"] == item["model"]
                    and min(4, int(F(r["p_numerator"], r["p_denominator"]) * 5))
                    == item["bin"]
                ]
                x = float(
                    sum(F(r["p_numerator"], r["p_denominator"]) for r in rows)
                    / len(rows)
                )
                value = float(
                    F(sum(r["label_no_release_90d"] for r in rows), len(rows))
                )
                assert (
                    axis == dict(xlim=[0, 1], ylim=[0, 1])
                    and item["x"] == x
                    and item["n"] == len(rows)
                )
                expected = [(x, value)]
            else:
                assert kind == "confusion"
                rows = [r for r in final if r["model"] != "baseline"]
                key = item["key"]
                predicate = {
                    "tp": lambda r: r["review_flag"] == 1
                    and r["label_no_release_90d"] == 1,
                    "fn": lambda r: r["review_flag"] == 0
                    and r["label_no_release_90d"] == 1,
                    "fp": lambda r: r["review_flag"] == 1
                    and r["label_no_release_90d"] == 0,
                    "tn": lambda r: r["review_flag"] == 0
                    and r["label_no_release_90d"] == 0,
                }[key]
                value = sum(predicate(r) for r in rows)
                x = ["tp", "fn", "fp", "tn"].index(key)
                assert (
                    axis == dict(xlim=[-0.6, 3.6], ylim=[0, 34])
                    and item["x"] == x
                    and item["width"] == 0.60
                    and item["display"] == str(value)
                )
                expected = [
                    (x - 0.30, 0),
                    (x + 0.30, 0),
                    (x + 0.30, value),
                    (x - 0.30, value),
                ]
            assert item["value"] == value
            values += 1
            left, right, top, bottom = areas[item["axis"]]
            projected = [
                (
                    left + (x - xmin) / (xmax - xmin) * (right - left),
                    bottom - (y - ymin) / (ymax - ymin) * (bottom - top),
                )
                for x, y in expected
            ]
            if kind == "calibration":
                use = ids[item["id"]].find(".//" + SVG + "use")
                assert use is not None
                actual = [(float(use.get("x")), float(use.get("y")))]
            else:
                actual = vertices(ids[item["id"]])
            assert len(actual) == len(projected), (item["id"], actual, projected)
            for (x, y), (a, b) in zip(actual, projected):
                assert abs(x - a) <= 3e-5 and abs(y - b) <= 3e-5, (
                    item["id"],
                    (x, y),
                    (a, b),
                )
                coordinates += 2
        for name, digest in figure["files"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            hashes += 1
        assert (ROOT / "paper/images" / (figure["figure"] + ".pdf")).read_bytes() == (
            ROOT / "figures" / (figure["figure"] + ".pdf")
        ).read_bytes()
    result = dict(
        status="PASS_SOURCE_VALUES_AND_ACTUAL_SVG_GEOMETRY",
        figure_values=values,
        SVG_coordinates=coordinates,
        format_hashes=hashes,
        figures=3,
        scope="Finite numerical/geometry/hash checks; visual pages and prose require separate inspection",
    )
    (ROOT / "figures/verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
