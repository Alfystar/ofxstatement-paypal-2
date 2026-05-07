#!/usr/bin/env python3
"""Anonymize a PayPal activity CSV for use as a test fixture or bug report.

Runs as a standalone script with zero dependencies on ofxstatement — only
the Python standard library. Preserves every property the parser cares
about (column shape, date format, decimal separator, currency codes,
balance arithmetic) while scrubbing personally identifying fields.

Usage:
    python3 scripts/anonymize_paypal.py input.csv output.csv [--seed N]

What gets scrubbed (by column position, so locale headers are fine):
    9  Transaction ID    → stable synthetic ID (seeded)
    10 From Email        → user<n>@example.invalid
    11 Name              → "Counterparty <n>"
    12 Bank Name         → "" (or "Example Bank" if originally populated)
    13 Bank Account      → "" (or masked placeholder if originally populated)
    16 Invoice Number    → "INV-<n>" (or "" if originally empty)
    17 Reference Txn ID  → mapped consistently with col 9

What stays exactly as-is (required for parser behaviour / test realism):
    0  Date, 1 Time, 2 Time Zone
    3  Description (PayPal booking-type labels like "Bankgutschrift auf
       PayPal-Konto" — a small fixed vocabulary, not PII)
    4  Currency
    5  Gross, 6 Fee, 7 Net, 8 Balance
    14 Delivery Fees, 15 Sales Tax
    Header row (locale-dependent labels preserved)

The transaction-ID mapping is deterministic per run (seeded), so
Reference Txn IDs still resolve to the correct (synthetic) Transaction IDs
within the output file.
"""

from __future__ import annotations

import argparse
import csv
import random
import string
import sys
from pathlib import Path
from typing import Dict, List


EXPECTED_COLUMN_COUNT = 18

TXN_ID_IDX = 9
EMAIL_IDX = 10
NAME_IDX = 11
BANK_NAME_IDX = 12
BANK_ACCOUNT_IDX = 13
INVOICE_IDX = 16
REF_TXN_ID_IDX = 17


def _synthetic_txn_id(rng: random.Random) -> str:
    """Generate a PayPal-shaped 17-char alphanumeric transaction ID."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(rng.choices(alphabet, k=17))


def _build_txn_id_mapping(rows: List[List[str]], rng: random.Random) -> Dict[str, str]:
    """Map every real Transaction ID (col 9) to a synthetic one.

    Reference Txn IDs (col 17) are resolved through the same mapping so
    cross-references inside the file still point at the right (now-fake)
    rows. IDs that appear only in col 17 (referencing transactions
    outside the statement window) are also mapped.
    """
    mapping: Dict[str, str] = {"": ""}
    for row in rows:
        for idx in (TXN_ID_IDX, REF_TXN_ID_IDX):
            original = row[idx]
            if original not in mapping:
                mapping[original] = _synthetic_txn_id(rng)
    return mapping


def anonymize_row(row: List[str], index: int, txn_map: Dict[str, str]) -> List[str]:
    row = list(row)

    row[TXN_ID_IDX] = txn_map[row[TXN_ID_IDX]]
    row[EMAIL_IDX] = f"user{index}@example.invalid" if row[EMAIL_IDX] else ""
    row[NAME_IDX] = f"Counterparty {index}" if row[NAME_IDX] else ""
    row[BANK_NAME_IDX] = "Example Bank" if row[BANK_NAME_IDX] else ""
    row[BANK_ACCOUNT_IDX] = "XX00 0000 0000 0000" if row[BANK_ACCOUNT_IDX] else ""
    row[INVOICE_IDX] = f"INV-{index}" if row[INVOICE_IDX] else ""
    row[REF_TXN_ID_IDX] = txn_map[row[REF_TXN_ID_IDX]]

    return row


def anonymize_csv(input_path: Path, output_path: Path, seed: int) -> int:
    # utf-8-sig transparently strips the BOM PayPal's Windows exports include.
    with input_path.open(encoding="utf-8-sig", newline="") as fin:
        reader = csv.reader(fin)
        try:
            header = next(reader)
        except StopIteration:
            raise SystemExit(f"error: {input_path} is empty")

        if len(header) != EXPECTED_COLUMN_COUNT:
            raise SystemExit(
                f"error: {input_path} has {len(header)} columns, "
                f"expected {EXPECTED_COLUMN_COUNT} (not a PayPal CSV?)"
            )

        rows = [row for row in reader if row]

    rng = random.Random(seed)
    txn_map = _build_txn_id_mapping(rows, rng)

    with output_path.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout, quoting=csv.QUOTE_ALL)
        writer.writerow(header)
        for i, row in enumerate(rows, start=1):
            writer.writerow(anonymize_row(row, i, txn_map))

    return len(rows)


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("input", type=Path, help="Real PayPal CSV to anonymize")
    parser.add_argument("output", type=Path, help="Destination for anonymized CSV")
    parser.add_argument(
        "--seed", type=int, default=0,
        help="Seed for deterministic transaction-ID generation (default: 0)",
    )
    args = parser.parse_args(argv)

    if not args.input.is_file():
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 1

    count = anonymize_csv(args.input, args.output, args.seed)
    print(f"Anonymized {count} row(s) → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
