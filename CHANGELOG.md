# Changelog

## [3.2.0] - 2026-04-12

### Fixed
- Empty CSV files no longer crash with `max() over empty sequence` — the
  parser now raises `ParseError(0, ...)` with a clear message.
- `_normalize_amount` strips NBSP (U+00A0) before decimal conversion, so
  German-locale exports with `-1\xa0234,56` parse correctly.
- European thousands separator (`-1.090,00`) parses correctly; previously
  `.replace(',', '.')` collapsed to `-1.090.00`. Rewritten as a locale-
  aware heuristic based on the last `,` vs last `.` position.
- Start balance now derived from the *chronologically earliest* row.
  Previously exports that arrived newest-first produced a wrong start.

### Added
- **Auto-detection of config settings.** `config.ini` is now optional:
  - `dataformat` inferred from the Date column separator (`.` → DMY dots,
    `-` → ISO, `/` → DMY/MDY disambiguated by any day > 12 in the file).
    USD-majority files break the `/`-ambiguous tie toward MDY.
  - `default_currency` inferred via majority vote over the Currency column.
  - `default_account` defaults to `"PayPal"` if unset.
  Any ini-provided value still overrides auto-detection, and every
  inferred value is logged at `INFO` level.
- **Header validation** with a clear `ParseError(1, ...)` when the column
  count doesn't match PayPal's export shape.
- **Chronological sort** of rows before parsing, so exports that arrive
  newest-first are handled correctly.
- **German locale headers** (`Datum`, `Brutto`, `Netto`, …) accepted
  alongside English headers.
- **Balance consistency sanity check** post-parse: warns if
  `start_balance + Σ amounts` diverges from the last row's `Balance`
  column, which catches silent amount-parsing drift, skipped rows, or
  PayPal column-layout changes.
- 37 unit tests covering header validation, locale-aware amount parsing,
  auto-detection, and chronological sorting.

### Changed
- **Renamed plugin entry point `paypal-convert` → `paypal`.** Users with
  an existing `[section]` in `config.ini` that sets `plugin = paypal-convert`
  must update it to `plugin = paypal`. With no config.ini, the CLI can
  now be invoked as `ofxstatement convert -t paypal input.csv output.ofx`
  since ofxstatement falls back to plugin-name lookup.
- Added a docstring on `PayPalPlugin` so `ofxstatement list-plugins`
  shows a description next to the plugin name.
- Migrated packaging from `setup.py` to PEP 621 `pyproject.toml`; removed
  stale `requirements.txt`.
- Replaced generic `ValueError` with ofxstatement's framework
  `ParseError` for format-mismatch errors.
- Full type annotations across `paypal.py` (parameters, returns, class
  attributes).
- Added module-level `logger` with `INFO` messages tracing auto-detected
  settings and parse summary.

### Developer tooling
- Added `Makefile` with `test`, `mypy`, `black`, `ruff`, `coverage` targets.
- Added GitHub Actions CI (`.github/workflows/test.yml`) with a Python
  3.11 / 3.12 / 3.13 matrix running pytest, mypy, black, and ruff, plus
  an `all-checks` aggregation job for branch protection.
- Added `[tool.mypy]` and `[tool.ruff]` configuration to `pyproject.toml`;
  `pip install -e ".[dev]"` now pulls `pytest`, `mypy`, `black`, `ruff`.

## [3.0.1]

### Fixed
- Docstring formatting in `split_records`.

## [3.0.0]

### Changed
- Fork of `gerasiov/ofxstatement-paypal` under the new name
  `ofxstatement-paypal-2`.
