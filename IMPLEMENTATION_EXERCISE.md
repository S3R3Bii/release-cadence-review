# Put a release-gap estimate in a review queue

A dependency owner may need time to check a project's release process before an upgrade deadline. A gap in stable GitHub releases is a prompt to gather context; projects can continue development, publish elsewhere or follow a deliberately slow release schedule.

Run the offline example after the development model is saved:

```sh
python src/review_cli.py examples/authored-intervals.json
python -m unittest discover -s tests -v
```

The three integer day intervals are authored inputs. They are not a new source observation or measured maintenance event. The CLI uses their median with the fixed development-selected teaching model. It returns a conditional release-gap estimate and the authored probability-at-least-one-half review flag. It sends no requests or notifications and replaces no dependency.

Implement a local review item containing the estimate, the three past intervals, the model revision and a space for a person's source-backed explanation. Keep the explanation separate from the estimate. A model trained on eight familiar Python-tooling repositories cannot establish another project's reliability, security or production risk. Before operational use, collect a broader prospective cohort and verify the relevant release channels and actual review outcomes.

For the implementation exercise, add an authored case with no three-interval history and return unavailable. Then verify that changing a later release changes its retrospective label without changing its already available past feature. Inspect retained failures and baseline results before making a performance claim.
