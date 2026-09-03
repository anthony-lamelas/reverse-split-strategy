# Manual diagnostics — not tests

These scripts hit the live network (yfinance) and print results for a human to
eyeball. They contain no assertions and are excluded from the automated suite via
`norecursedirs` in `pyproject.toml`.

Run them by hand when debugging a data-source problem:

```bash
python tests/manual/test_price_fetch.py
python tests/manual/verify_imports.py
```

Real tests live in `tests/` and must run offline with synthetic data.
