"""Bind manuscript measurements to checked sources and extracted PDF text."""

import ast, hashlib, json, re
from collections import Counter
from fractions import Fraction as F
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def brier(rows, macro):
    def one(values):
        return sum(
            (F(r["p_numerator"], r["p_denominator"]) - r["label_no_release_90d"]) ** 2
            for r in values
        ) / len(values)

    if not macro:
        return float(one(rows))
    repos = sorted({r["repository"] for r in rows})
    return float(
        sum(one([r for r in rows if r["repository"] == name]) for name in repos)
        / len(repos)
    )


def main():
    text = (ROOT / "paper/main.tex").read_text(encoding="utf-8")
    bindings = read("data/paper_number_map.json")
    d = read("results/development/summary.json")
    f = read("results/final/summary.json")
    m = read("results/development/selected_model.json")
    b = f["models"]["baseline"]
    s = f["models"][f["selected_name"]]
    source = read("data/release_metadata.json")
    native = read("data/metadata_original_verification.json")
    features = read("results/development/features.json") + read(
        "results/final/features.json"
    )
    training = [r for r in features if r["phase"] == "training"]
    validation = [r for r in features if r["phase"] == "validation"]
    final = [r for r in features if r["phase"] == "final"]
    pred = read("results/final/predictions.json")
    devpred = read("results/development/predictions.json")
    fixture_count = sum(
        isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
        for path in (ROOT / "tests").glob("test_*.py")
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )
    expected = {
        "MSourceRows": len(source),
        "MSourceFiles": native["source_files"],
        "MSourceBytes": native["source_bytes"],
        "MNativeChecks": native["conditions"],
        "MDevChecks": read("data/development_verification.json")["conditions"],
        "MFinalChecks": read("data/final_verification.json")["conditions"],
        "MTraining": len(training),
        "MValidation": len(validation),
        "MFinal": len(final),
        "MTrainPos": sum(r["label_no_release_90d"] for r in training),
        "MValPos": sum(r["label_no_release_90d"] for r in validation),
        "MFinalPos": sum(r["label_no_release_90d"] for r in final),
        "MFixtures": fixture_count,
        "MDeps": len(read("data/dependency_freeze.json")["files"]),
        "MCandidates": len(read("results/development/search.json")),
    }
    for side, prefix in [(0, "MShort"), (1, "MLong")]:
        members = [
            r
            for r in training
            if (r["median_interval_us"] > 60 * 86400000000) == bool(side)
        ]
        expected[prefix + "N"] = len(members)
        expected[prefix + "Pos"] = sum(r["label_no_release_90d"] for r in members)
    chosen = [r for r in pred if r["model"] == f["selected_name"]]
    expected.update(
        MTP=sum(
            r["review_flag"] == 1 and r["label_no_release_90d"] == 1 for r in chosen
        ),
        MFP=sum(
            r["review_flag"] == 1 and r["label_no_release_90d"] == 0 for r in chosen
        ),
        MFN=sum(
            r["review_flag"] == 0 and r["label_no_release_90d"] == 1 for r in chosen
        ),
        MTN=sum(
            r["review_flag"] == 0 and r["label_no_release_90d"] == 0 for r in chosen
        ),
    )
    for name, model, table, macro in [
        ("MBaseDev", "baseline", devpred, True),
        ("MSelectedDev", d["selected_name"], devpred, True),
        ("MBaseFinal", "baseline", pred, True),
        ("MSelectedFinal", f["selected_name"], pred, True),
        ("MBaseMicro", "baseline", pred, False),
        ("MSelectedMicro", f["selected_name"], pred, False),
    ]:
        expected[name] = brier([r for r in table if r["model"] == model], macro)
    expected.update(
        MBaseLog=b["macro_log_loss"],
        MSelectedLog=s["macro_log_loss"],
        MBaseP=m["model"]["baseline_probability"]["value"],
        MShortP=m["model"]["strata"][0]["probability"]["value"],
        MLongP=m["model"]["strata"][1]["probability"]["value"],
        MHTTPXBaseLog=b["per_repository"][0]["log_loss"],
        MHTTPXSelectedLog=s["per_repository"][0]["log_loss"],
    )
    source_counts = Counter(r["repository"] for r in source)
    counts = {
        p: Counter(r["repository"] for r in features if r["phase"] == p)
        for p in ["training", "validation", "final"]
    }
    for i, item in enumerate(read("data/source_plan.json")["repositories"]):
        for suffix, value in [
            ("Source", source_counts[item["repository"]]),
            ("Training", counts["training"][item["repository"]]),
            ("Validation", counts["validation"][item["repository"]]),
            ("Final", counts["final"][item["repository"]]),
        ]:
            expected["MRepo" + chr(65 + i) + suffix] = value
    assert set(bindings) == set(expected)
    pdf_path = ROOT / "paper/manuscript.pdf"
    pdf = PdfReader(pdf_path)
    assert len(pdf.pages) == 6
    pages = [page.extract_text() or "" for page in pdf.pages]
    flat = "\n".join(pages)
    checks = 0
    for name, value in expected.items():
        actual = bindings[name]
        assert actual["value"] == value, name
        checks += 1
        display = (
            f"{value:.6f}"
            if isinstance(value, float)
            else (
                f"{value:,}"
                if name
                in ["MSourceBytes", "MNativeChecks", "MDevChecks", "MFinalChecks"]
                else str(value)
            )
        )
        assert (
            actual["display"] == display
            and "\\newcommand{\\" + name + "}{" + display + "}" in text
        ), name
        checks += 1
        assert display in flat, (name, display)
        checks += 1
    low = [r for r in chosen if F(r["p_numerator"], r["p_denominator"]) < F(1, 5)]
    high = [
        r
        for r in chosen
        if F(1, 5) <= F(r["p_numerator"], r["p_denominator"]) < F(2, 5)
    ]
    assert (
        len(low) == 28
        and sum(r["label_no_release_90d"] for r in low) == 5
        and len(high) == 11
        and sum(r["label_no_release_90d"] for r in high) == 5
    )
    assert (
        "contains 28 origins with five gaps" in text
        and "eleven origins with five gaps" in text
    )
    checks += 2
    for phrase in [
        "2020--2022",
        "2023 origins",
        "2024--2025",
        "14, 30 and 60",
        "1, 5 and 20",
        "p_0=(k+1)/(n+2)",
        "p_s=\\frac{k_s+a p_0}{n_s+a}",
        "p\\geq0.5",
        "2\\times10^{-12}",
        "75-digit Decimal",
        "3.10.11 and 3.12.14",
        "not peer reviewed",
        "Precision is undefined",
        "threshold remains unchanged",
        "Azure optimization",
    ]:
        assert phrase in text, phrase
        checks += 1
    assert all(len(page) > 1500 for page in pages)
    assert all(
        "REPOSITORYROWS" not in page and "COMMANDS" not in page for page in pages
    )
    images = ["validation_search", "project_scores", "calibration_and_misses"]
    for name in images:
        assert ("images/" + name + ".pdf") in text
        assert (ROOT / "paper/images" / (name + ".pdf")).read_bytes() == (
            ROOT / "figures" / (name + ".pdf")
        ).read_bytes()
        checks += 2
    assert "Overfull" not in (ROOT / "paper/main.log").read_text(
        encoding="utf-8"
    ), "Unrepaired TeX layout warning"
    result = dict(
        status="PASS_SOURCE_BOUND_MANUSCRIPT",
        bound_measured_displays=len(expected),
        conditions=checks,
        pages=len(pages),
        PDF_text_extracted=True,
        visual_page_inspection="Separate owner inspection of all six 95dpi rendered pages",
        paper_sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        source_sha256=hashlib.sha256(
            (ROOT / "paper/main.tex").read_bytes()
        ).hexdigest(),
        limits="Finite source/text/layout checks; no peer review, independent population validation or original-vintage claim",
    )
    (ROOT / "paper_verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
