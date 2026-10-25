# Contributing

Discuss a reproducible problem in an issue before a broad change. Use synthetic
data, never customer exports. Python 3.11+ is required. Install with
`python -m pip install -e . build==1.6.1 ruff==0.17.2`, then run
`python -m unittest discover -s tests -v`, `python -m ruff check .`,
`python -m ruff format --check .` and `python -m build`.
Add regression tests for changes to conversion semantics and update both READMEs.
The maintainer reviews contributions manually; no bot commits are configured.
