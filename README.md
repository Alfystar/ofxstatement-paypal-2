# ofxstatement-paypal

PayPal CSV → OFX converter plugin for [`ofxstatement`](https://github.com/kedder/ofxstatement).

This repository is a maintained fork of the original project, created so the
plugin can continue evolving and staying compatible with recent PayPal exports.

## 1. What this project is for

Use this plugin if you:

- export your account activity from PayPal as CSV,
- want to import it into software that understands OFX,
- already use tools such as GnuCash, HomeBank, Odoo, Tryton, or any other
  application that accepts OFX bank statements.

In short: this plugin reads a PayPal CSV export, parses the transactions, and
produces an OFX statement that downstream finance tools can import.

<details>
<summary><strong>More details: audience, scope, and supported behavior</strong></summary>

### Who this is aimed at

This project is primarily for end users who want to move PayPal transactions
into personal finance or accounting software, and for maintainers who want a
reliable, scriptable CSV → OFX conversion flow.

### What it does

- plugs into `ofxstatement`, the generic bank-statement conversion tool,
- parses PayPal “Bank statements” CSV exports,
- infers the date format when possible,
- infers the statement currency when the CSV contains exactly one currency,
- sorts rows chronologically before processing,
- handles PayPal currency-conversion groups so foreign-currency purchases do
  not break OFX balances.

### What it does not do

- it does not download statements from PayPal for you,
- it does not replace `ofxstatement`; it extends it,
- it does not produce one multi-currency OFX file, because OFX statements are
  single-currency by design.

### Project background

The original project was based on earlier work by gerasiov:
<https://github.com/gerasiov/ofxstatement-paypal/>.

</details>

## 2. Install and configure it minimally

For most users, install the package and run it with no manual configuration:

```bash
pip3 install ofxstatement-paypal-2
ofxstatement convert -t paypal-convert input.csv output.ofx
```

If you already use an `ofxstatement` config file, create a section that points
to this plugin:

```ini
[paypal-convert]
plugin = paypal-convert
```

Then use that section name with `-t`.

<details>
<summary><strong>More details: installation, configuration, auto-detection, and multi-currency exports</strong></summary>

### Install from PyPI

```bash
pip3 install ofxstatement-paypal-2
```

### Install from source

```bash
git clone https://github.com/Alfystar/ofxstatement-paypal-2.git
cd ofxstatement-paypal-2
python3 -m pip install --upgrade build
python3 -m build --sdist --wheel
python3 -m pip install dist/ofxstatement_paypal_2-<version>.tar.gz
```

Replace `<version>` with the version you just built.

### When config is optional

If no `config.ini` is loaded, you can call the plugin directly by its
registered name:

```bash
ofxstatement convert -t paypal-convert input.csv output.ofx
```

If a personal `ofxstatement` config file exists, `-t` is interpreted as a
section name in that file, so the most stable setup is a section such as:

```ini
[paypal-convert]
plugin = paypal-convert
```

### What the plugin auto-detects

When you omit settings, the plugin tries to infer them from the CSV:

| Setting | Inferred from | Fallback |
| --- | --- | --- |
| `dataformat` | Date column: `.` → `%d.%m.%Y`, `-` → `%Y-%m-%d`, `/` → DMY/MDY decided by the file contents | Raises if the separator is not recognized |
| `default_currency` | The CSV's only currency, when there is just one | Raises if there are zero currencies or more than one |
| `default_account` | — | `"PayPal"` |
| `charset` | — | `UTF-8` |

The inferred values are logged at `INFO` level so you can check what the parser
decided.

### Multi-currency exports

PayPal mixes all balances into one CSV, but OFX is single-currency. That means:

- if the CSV contains only one currency, the plugin exports it directly;
- if the CSV contains multiple currencies and `default_currency` is not set,
  parsing stops and asks you to choose one.

To export each currency separately, define one config section per currency:

```ini
[paypal-convert-eur]
plugin = paypal-convert
default_currency = EUR

[paypal-convert-gbp]
plugin = paypal-convert
default_currency = GBP

[paypal-convert-usd]
plugin = paypal-convert
default_currency = USD
```

Then run the conversion once per section:

```bash
ofxstatement convert -t paypal-convert-eur input.csv output.eur.ofx
ofxstatement convert -t paypal-convert-gbp input.csv output.gbp.ofx
ofxstatement convert -t paypal-convert-usd input.csv output.usd.ofx
```

### Overriding inferred settings

Run:

```bash
ofxstatement edit-config
```

Then add a section like:

```ini
[paypal-convert-custom]
plugin = paypal-convert
encoding = utf-8
dataformat = %%d/%%m/%%Y
default_currency = EUR
default_account = Paypal Personal
```

Useful fields:

- `dataformat`: the `strptime` format matching your CSV date column,
- `default_currency`: target statement currency (`EUR`, `USD`, `GBP`, ...),
- `default_account`: account label written into the OFX output.

Existing configurations that already say `plugin = paypal-convert` keep working
unchanged after upgrade.

</details>

## 3. Use it with the minimal workflow

1. In PayPal, download a CSV from **History → Download → customized → Bank statements**.
2. Open a terminal in the folder that contains the CSV.
3. Convert it:

```bash
ofxstatement convert -t paypal-convert input.csv output.ofx
```

If you use a named config section instead, replace `paypal-convert` with your
section name.

<details>
<summary><strong>More details: where to get the CSV, aliases, anonymizing, and OFX consumers</strong></summary>

### Where to get the CSV in PayPal

From the PayPal web interface, download a CSV of **Bank statements** for the
report period you want.

<img src="BankStatements.png" alt="Bank statements guide" style="zoom:50%;" />

### Optional shell alias

If you want a shorter command, add an alias. Example for `bash`:

```bash
printf '\n# Paypal CSV convert to OFX format\nalias ofxPaypal="ofxstatement convert -t paypal-convert"\n' >> ~/.bash_aliases
```

After reloading your shell, you can run:

```bash
ofxPaypal Paypal.csv Paypal.ofx
```

If your shell does not load `~/.bash_aliases`, make sure your `~/.bashrc`
contains something like:

```bash
if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi
```

### Foreign-currency purchases

PayPal often exports a foreign-currency purchase as a four-row group. The
plugin collapses the redundant rows and annotates the statement-currency leg
with `<ORIGCURRENCY>`, so OFX consumers can still display the original amount.

### Anonymizing a PayPal CSV before sharing it

If you need to attach a real CSV to a bug report, you can scrub it with:

```bash
python3 scripts/anonymize_paypal.py input.csv output.csv [--seed N]
```

The script removes personally identifying data while preserving the structure,
locale, arithmetic, and transaction cross-references that the parser needs.

### What to do with the generated OFX file

Once you have the OFX file, you can import it into software that accepts OFX.
One good open-source option is [HomeBank](http://homebank.free.fr/en/index.php),
which works well with the generated files.

</details>

## 4. For maintainers

<details>
<summary><strong>Release packages to PyPI</strong></summary>

If you are publishing a new release:

1. Update the version in `pyproject.toml`.
2. Run tests and any checks you want before release.
3. Remove old build artifacts.
4. Build the package.
5. Optionally validate the artifacts with `twine check`.
6. Upload them to PyPI.

### With Pipenv

```bash
pipenv install --dev
pipenv run python -m unittest discover -s tests
rm -rf dist/
pipenv run python -m build
pipenv run python -m twine check dist/*
pipenv run python -m twine upload dist/*
```

### With a plain virtualenv

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m unittest discover -s tests
rm -rf dist/
.venv/bin/python -m build
.venv/bin/python -m twine check dist/*
.venv/bin/python -m twine upload dist/*
```

### TestPyPI first

```bash
pipenv run python -m twine upload --repository testpypi dist/*
```

`twine` authentication is usually handled via an API token stored in the
environment or in `~/.pypirc`.

</details>

