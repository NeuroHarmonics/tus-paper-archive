# Contributing

Most contributions are issues, not pull requests. The three issue forms cover the common cases.

- A value in a paper's record is wrong: use the Correction form. Give the field, the value shown, the correct value and the sentence of the paper that states it.
- A paper is missing: use the Propose a paper form. Give the DOI and say how the paper meets the four conditions in INCLUSION.md.
- Anything else, such as a field, a filter, a vocabulary term or an error in the documentation: use the Suggestion form.

Corrections are applied at the next release and appear on the site then.

## Pull requests

A pull request that edits a record in `data/papers/` must set `provenance.human_edited` to `true`, keep every number paired with a sentence from the paper in `provenance.source_quotes`, and pass `uv run scripts/validate.py`. A pull request that changes a script should keep the existing conventions and pass the same validation. Changes to the schema or a vocabulary are made through SCHEMA.md and `schema/paper.schema.json` together, with a changelog line.

Please do not send pull requests that add PDFs or full text. Those are not held in this repository.
