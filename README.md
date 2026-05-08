# ofxstatement-paypal

### Paypal plugin for ofxstatement 

This project provides a custom plugin for [ofxstatement](https://github.com/kedder/ofxstatement) for Paypal. It is based
on the work done by gerasiov (https://github.com/gerasiov/ofxstatement-paypal/).


`ofxstatement_*` is a tool to convert proprietary bank statement to OFX format, suitable for importing to GnuCash / Odoo /Tryton. Plugin for ofxstatement parses a particular proprietary bank statement format and produces common data structure, that is then formatted into an OFX file.

Users of ofxstatement have developed several plugins for their banks. They are listed on main [`ofxstatement`](https://github.com/kedder/ofxstatement) site. If your bank is missing, you can develop your own plugin.

> This repository is a fork of the original project, afrter the original author did not respond to the pull request, we decided to create a new repository to maintain the project.

## Installation

### From PyPI repositories
```
pip3 install ofxstatement-paypal-2
```

### From source
```
git clone https://github.com/Alfystar/ofxstatement-paypal-2.git
cd ofxstatement-paypal-2
pip install build
python3 -m build --sdist --wheel
pip install dist/ofxstatement_paypal_2-<version>.tar.gz # replace <version> with the version number
```

### Configuration

**Configuration is optional.** The plugin inspects the CSV contents and infers
every setting it needs (date format, currency, account label). You only need a
config entry if you want to override one of the inferred values or give your
conversion profile a convenient alias for the CLI.

With no config.ini at all, you can invoke the plugin by its registered name:

```bash
$ ofxstatement convert -t paypal-convert input.csv output.ofx
```

This direct plugin lookup only happens when no config file is loaded. If you
already have a personal `config.ini`, `-t` is interpreted as a section name in
that file, so the usual setup is to keep a section such as
`[paypal-convert]` with `plugin = paypal-convert`.

#### Auto-detection behaviour

When a setting is not provided, the plugin infers it as follows:

| Setting | Inferred from | Fallback |
| --- | --- | --- |
| `dataformat` | Date column: `.` → `%d.%m.%Y`, `-` → `%Y-%m-%d`, `/` → DMY/MDY decided by any day > 12 in the file; if ambiguous, USD-majority files pick MDY, everything else picks DMY | Raises if the separator is unrecognised |
| `default_currency` | The CSV's only currency, when there is just one | Raises if no Currency values are present, or if the CSV holds more than one currency (in that case you must pick one explicitly — see below) |
| `default_account` | — | `"PayPal"` |
| `charset` | — | `UTF-8` |

The inferred values are logged at `INFO` level so you can verify them in the
output. Rows are also sorted chronologically (by Date, then Time) before
processing, so exports that arrive newest-first are handled correctly.

#### Multi-currency & foreign-currency purchases

A PayPal account can hold multiple currency balances, and the CSV mixes
all of them into one file. OFX is single-currency per statement, so:

- If the CSV holds **only one** currency the parser auto-detects it and
  emits one OFX as usual.
- If the CSV holds **more than one** currency (e.g. EUR + GBP + USD)
  and `default_currency` is **not** set, parsing aborts with a per-
  currency line-count breakdown and asks you to pick one. To export
  every currency, define one config section per currency and run the
  converter once per section:

  ```ini
  [paypal-eur]
  plugin = paypal-convert
  default_currency = EUR

  [paypal-gbp]
  plugin = paypal-convert
  default_currency = GBP

  [paypal-usd]
  plugin = paypal-convert
  default_currency = USD
  ```

  ```bash
  $ ofxstatement convert -t paypal-eur input.csv output.eur.ofx
  $ ofxstatement convert -t paypal-gbp input.csv output.gbp.ofx
  $ ofxstatement convert -t paypal-usd input.csv output.usd.ofx
  ```

A purchase in a foreign currency is exported by PayPal as a four-row
conversion group (foreign charge + foreign zero-conversion + two
statement-currency legs). The parser:

- Computes the running balance only from rows in the statement currency
  (chosen via auto-detect or `default_currency`), so foreign-currency
  rows can't corrupt the total.
- Collapses each conversion group: drops the redundant foreign
  zero-conversion row and annotates the statement-currency leg with
  `<ORIGCURRENCY>` carrying the foreign symbol and exchange rate. OFX
  consumers (GnuCash, HomeBank, …) then show the booked amount together
  with the original, e.g. `−12.98 EUR (originally −12.95 USD)`.

#### Overriding via config.ini

To override any inferred value — or to define a named profile for the CLI —
run:

```bash
$ ofxstatement edit-config
```

and add a section like:

```ini
[Conf-Name]
plugin = paypal-convert
encoding = utf-8
dataformat = %%d/%%m/%%Y
default_currency = EUR
default_account = Paypal Personal
```

- `Conf-Name`: any identifier; used with `ofxstatement convert -t <Conf-Name> input.csv output.ofx`.
- `dataformat`: strptime format matching your PayPal CSV's Date column. Common values:
  - `%%d/%%m/%%Y` — Europe (DMY with slashes)
  - `%%d.%%m.%%Y` — Germany / Italy / etc. (DMY with dots)
  - `%%m/%%d/%%Y` — USA (MDY with slashes)
  - `%%Y-%%m-%%d` — ISO
  - (The `%%` double-percent escape is required by the INI parser; it becomes a single `%` when read.)
- `default_currency`: three-letter ISO code (`EUR`, `USD`, `GBP`, …).
- `default_account`: shown in the OFX output; helps tools like [HomeBank](http://homebank.free.fr/en/index.php) route imports to the right account.

> Omit any field you're happy to let the plugin infer. You can define multiple
> sections with different names to keep several profiles side by side.
>
> Existing configurations that already say `plugin = paypal-convert` keep
> working unchanged after upgrading.

## Usage

From Paypal Web interface, download a CSV of  `Bank statements` with the personalized report period you wish. (PayPal Login :arrow_right: History  :arrow_right: Download :arrow_right: customized)

<img src="BankStatements.png" alt="Bank statements guide" style="zoom:50%;" />

Finally, open terminal in the directory where you download the report and type:

```bash
$ ofxstatement convert -t <Conf-Name> input.csv output.ofx
```

### Add Alias
To simplify the use of the plugin, we strongly recommend adding an alias to your system (if in a Linux environment or on an emulated terminal) by adding the alias of this command to your *.bash_aliases*:
> **Note**: this alias uses configuration name `paypal-convert`; if you use another name, change it in the alias.

```bash
$ printf '\n# Paypal CSV convert to OFX format\nalias ofxPaypal="ofxstatement convert -t paypal-convert"\n' >> ~/.bash_aliases
```
After that, reload your terminal (close and then reopen) and the usage change to:
```bash
  $ ofxPaypal Paypal.csv Paypal.ofx
```
**Note**: If after reload alias are not loading, go in your *.bashrc* and check if follow line are present, if not, add it on the end:
```bash
  # Alias definitions.
  # You may want to put all your additions into a separate file like
  # ~/.bash_aliases, instead of adding them here directly.
  # See /usr/share/doc/bash-doc/examples in the bash-doc package.

  if [ -f ~/.bash_aliases ]; then
      . ~/.bash_aliases
  fi
```



## Development

The plugin uses a PEP 517/621 `pyproject.toml` layout. `pyproject.toml` is
authoritative for packaging and the `[project.optional-dependencies].dev`
extra is what CI installs. For local hacking you can pick whichever
workflow you prefer — they all reach the same dev environment:

### With Pipenv (recommended for local development)

A `Pipfile` is checked in so contributors can spin up a local development
environment with one command. It mirrors `pyproject.toml`'s runtime
dependencies plus the `dev` extra, including the `build` tool used to create
source distributions and wheels.

```bash
# Install pipenv if you don't have it (system, user, or pipx — your call)
$ pip install --user pipenv

# Install runtime + dev deps into a fresh virtualenv
$ pipenv install --dev

# Drop into the virtualenv shell
$ pipenv shell

# …or run a single command without entering the shell:
$ pipenv run pytest
$ pipenv run mypy src tests
$ pipenv run black --check src tests
$ pipenv run ruff check src tests
$ pipenv run python -m build --sdist --wheel
```

The plugin itself is installed in editable mode (`{editable = true,
path = "."}` in `Pipfile`), so source edits are picked up immediately
without reinstalling.

If you want a local lockfile for your machine, regenerate it after a `Pipfile`
change with:

```bash
$ pipenv lock
```

`Pipfile.lock` is intentionally gitignored in this repository, so contributors
can refresh it locally without creating noisy cross-platform diffs.

### With plain pip + venv

```bash
$ python -m venv .venv
$ .venv/bin/pip install -e ".[dev]"
$ .venv/bin/pytest
$ .venv/bin/python -m build --sdist --wheel
```

This is the path CI uses. Convenient if you don't want to add `pipenv`
to your toolchain.

### Running the tests directly

The unit tests are pure stdlib `unittest` and load the plugin module
straight from `src/` via `importlib`, so they also run without
installing the package at all:

```bash
$ python3 -m unittest discover -s tests
```

### Anonymizing a PayPal CSV

`scripts/anonymize_paypal.py` strips personally identifying fields
(email, name, bank details, invoice/transaction IDs) from a real PayPal
export while preserving everything the parser cares about: column shape,
locale-specific header labels, Description (a small fixed vocabulary of
booking-type labels like "Bankgutschrift auf PayPal-Konto"), date
format, decimal separator, currency codes, and the Gross/Fee/Net/Balance
arithmetic. It
runs on pure stdlib — no ofxstatement install required — so it's safe to
hand to end users who want to scrub a CSV before attaching it to a bug
report.

```bash
$ python3 scripts/anonymize_paypal.py input.csv output.csv [--seed N]
```

Transaction-ID mapping is deterministic per seed, and `Reference Txn ID`
entries are rewritten through the same map so cross-references between
rows stay consistent in the anonymized output.

## How use OFX file after conversion

The `ofx` format stands for '*Open Financial Exchange*', it can be used to transfer your accounting records from one database to another.
Once you have the `ofx` file, you can use any program to manage your finances.
Among the many available, a non-exhaustive list of open source products is:

- [HomeBank](http://homebank.free.fr/en/index.php), continuously updated program, present everywhere except in smartphones, with many beautiful ideas and listening to the community. **100% compatibility** 
