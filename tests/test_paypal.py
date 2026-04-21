import importlib.util
import io
import logging
import pathlib
import sys
import unittest
from datetime import datetime
from decimal import Decimal

from ofxstatement.exceptions import ParseError


def _load_paypal_module():
    path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src"
        / "ofxstatement"
        / "plugins"
        / "paypal.py"
    )
    spec = importlib.util.spec_from_file_location("paypal_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["paypal_under_test"] = module
    spec.loader.exec_module(module)
    return module


_paypal = _load_paypal_module()
PayPalParser = _paypal.PayPalParser
DEFAULT_ACCOUNT_ID = _paypal.DEFAULT_ACCOUNT_ID

# Synthetic fixtures below intentionally use static Balance=90,00 across rows,
# which trips the real balance-consistency sanity check. Silence the plugin's
# logger so the test output stays clean; actual parse behaviour is still
# exercised.
logging.getLogger(_paypal.__name__).setLevel(logging.CRITICAL)


ENGLISH_HEADER = [
    "Date",
    "Time",
    "Time Zone",
    "Description",
    "Currency",
    "Gross",
    "Fee",
    "Net",
    "Balance",
    "Transaction ID",
    "From Email Address",
    "Name",
    "Bank Name",
    "Bank Account",
    "Deliver And Handling Fees",
    "Sales Tax",
    "Invoice Number",
    "Reference Txn ID",
]

GERMAN_HEADER = [
    "Datum",
    "Uhrzeit",
    "Zeitzone",
    "Beschreibung",
    "Waehrung",
    "Brutto",
    "Entgelt",
    "Netto",
    "Guthaben",
    "Transaktionscode",
    "Absender E-Mail-Adresse",
    "Name",
    "Name der Bank",
    "Bankkonto",
    "Versand- und Bearbeitungsgebuehr",
    "Umsatzsteuer",
    "Rechnungsnummer",
    "Zugehoeriger Transaktionscode",
]


def _row(**overrides):
    defaults = {
        "Date": "02/01/2025",
        "Time": "10:00:00",
        "Time Zone": "UTC",
        "Description": "Synthetic payment",
        "Currency": "EUR",
        "Gross": "-10,00",
        "Fee": "0,00",
        "Net": "-10,00",
        "Balance": "90,00",
        "Transaction ID": "TXN0000000000001",
        "From Email Address": "payer@example.invalid",
        "Name": "Example Merchant",
        "Bank Name": "",
        "Bank Account": "",
        "Deliver And Handling Fees": "0,00",
        "Sales Tax": "0,00",
        "Invoice Number": "INV-1",
        "Reference Txn ID": "",
    }
    defaults.update(overrides)
    cells = [f'"{defaults[c]}"' for c in ENGLISH_HEADER]
    return ",".join(cells)


def _csv(*rows, header=None):
    header = header if header is not None else ENGLISH_HEADER
    header_line = ",".join(f'"{c}"' for c in header)
    return "\n".join([header_line, *rows]) + "\n"


def _parser(csv_text, date_format="%d/%m/%Y", account="ACC1", currency="EUR"):
    return PayPalParser(io.StringIO(csv_text), date_format, account, currency)


class HeaderValidationTests(unittest.TestCase):

    def test_empty_file_raises(self):
        parser = _parser("")
        with self.assertRaises(ParseError) as ctx:
            parser.parse()
        self.assertIn("empty", str(ctx.exception).lower())

    def test_too_few_columns_raises(self):
        short_header = ",".join(f'"{c}"' for c in ENGLISH_HEADER[:5])
        parser = _parser(short_header + "\n")
        with self.assertRaises(ParseError) as ctx:
            parser.parse()
        msg = str(ctx.exception).lower()
        self.assertIn("expected", msg)
        self.assertIn("columns", msg)

    def test_too_many_columns_raises(self):
        long_header = ",".join(f'"{c}"' for c in ENGLISH_HEADER + ["Extra"])
        parser = _parser(long_header + "\n")
        with self.assertRaises(ParseError):
            parser.parse()

    def test_localized_header_accepted(self):
        parser = _parser(_csv(header=GERMAN_HEADER))
        stmt = parser.parse()
        self.assertEqual(len(stmt.lines), 0)


class ParseRecordTests(unittest.TestCase):

    def test_single_payment_parses(self):
        parser = _parser(_csv(_row()))
        stmt = parser.parse()
        self.assertEqual(len(stmt.lines), 1)
        line = stmt.lines[0]
        self.assertEqual(line.id, "TXN0000000000001")
        self.assertEqual(line.date, datetime(2025, 1, 2))
        self.assertEqual(line.amount, Decimal("-10.00"))
        self.assertEqual(line.fee, Decimal("0.00"))
        self.assertEqual(line.currency.symbol, "EUR")
        self.assertEqual(line.refnum, "INV-1")

    def test_negative_amount_is_payment(self):
        parser = _parser(_csv(_row(Net="-10,00")))
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].trntype, "PAYMENT")

    def test_positive_amount_is_directdep(self):
        parser = _parser(_csv(_row(Net="25,50", Gross="25,50", Balance="125,50")))
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].trntype, "DIRECTDEP")

    def test_bank_transaction_is_xfer(self):
        parser = _parser(
            _csv(
                _row(
                    **{
                        "Bank Name": "Example Bank",
                        "Bank Account": "DE00 0000 0000 0000",
                    }
                )
            )
        )
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].trntype, "XFER")

    def test_comma_decimal_separator(self):
        parser = _parser(_csv(_row(Net="-1234,56", Fee="-1,23")))
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].amount, Decimal("-1234.56"))
        self.assertEqual(stmt.lines[0].fee, Decimal("-1.23"))

    def test_nbsp_stripped_from_amount(self):
        parser = _parser(_csv(_row(Net="-1\xa0234,56")))
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].amount, Decimal("-1234.56"))

    def test_european_dot_thousands_separator(self):
        parser = _parser(_csv(_row(Net="-1.090,00", Balance="-1.090,00")))
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].amount, Decimal("-1090.00"))

    def test_us_comma_thousands_separator(self):
        parser = _parser(_csv(_row(Net="-1,234.56", Balance="-1,234.56")))
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].amount, Decimal("-1234.56"))

    def test_amount_without_thousands_separator(self):
        parser = _parser(_csv(_row(Net="-1234,56")))
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].amount, Decimal("-1234.56"))

    def test_start_balance_derived_from_first_row(self):
        row = _row(Net="-10,00", Balance="90,00")
        parser = _parser(_csv(row))
        stmt = parser.parse()
        self.assertEqual(stmt.start_balance, Decimal("100.00"))
        self.assertEqual(stmt.start_date, datetime(2025, 1, 2))

    def test_end_balance_accumulates(self):
        rows = [
            _row(
                Net="-10,00",
                Balance="90,00",
                **{"Transaction ID": "TXN001", "Date": "02/01/2025"},
            ),
            _row(
                Net="5,00",
                Gross="5,00",
                Balance="95,00",
                **{"Transaction ID": "TXN002", "Date": "03/01/2025"},
            ),
        ]
        parser = _parser(_csv(*rows))
        stmt = parser.parse()
        self.assertEqual(stmt.start_balance, Decimal("100.00"))
        self.assertEqual(stmt.end_balance, Decimal("95.00"))
        self.assertEqual(stmt.end_date, datetime(2025, 1, 3))

    def test_memo_contains_populated_fields_only(self):
        parser = _parser(
            _csv(
                _row(
                    Name="Example Merchant",
                    Description="Synthetic payment",
                    **{"Invoice Number": "INV-1", "From Email Address": ""},
                )
            )
        )
        stmt = parser.parse()
        memo = stmt.lines[0].memo
        # Name is in payee, not memo, to avoid Beschreibung/Buchungstext duplication.
        self.assertNotIn("Name:", memo)
        self.assertIn("Description:Synthetic payment", memo)
        self.assertIn("Invoice Number:INV-1", memo)
        self.assertNotIn("From Email Address:", memo)

    def test_payee_uses_name_when_populated(self):
        parser = _parser(
            _csv(
                _row(
                    Name="Example Merchant",
                    Description="Zahlung im Einzugsverfahren mit Zahlungsrechnung",
                )
            )
        )
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].payee, "Example Merchant")

    def test_payee_falls_back_to_description_when_name_empty(self):
        parser = _parser(
            _csv(
                _row(
                    Name="",
                    Description="Bankgutschrift auf PayPal-Konto",
                )
            )
        )
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].payee, "Bankgutschrift auf PayPal-Konto")

    def test_description_not_duplicated_in_memo_when_used_as_payee(self):
        parser = _parser(
            _csv(
                _row(
                    Name="",
                    Description="Bankgutschrift auf PayPal-Konto",
                )
            )
        )
        stmt = parser.parse()
        # Description is in payee as the fallback; shouldn't also appear in memo.
        self.assertNotIn("Description:", stmt.lines[0].memo)

    def test_dot_date_format_via_config(self):
        parser = _parser(_csv(_row(Date="02.01.2025")), date_format="%d.%m.%Y")
        stmt = parser.parse()
        self.assertEqual(stmt.lines[0].date, datetime(2025, 1, 2))

    def test_empty_statement_does_not_crash(self):
        parser = _parser(_csv())
        stmt = parser.parse()
        self.assertEqual(len(stmt.lines), 0)
        self.assertIsNone(stmt.end_date)


class AutoDetectionTests(unittest.TestCase):

    def _auto_parser(self, csv_text, date_format=None, currency=None, account=None):
        return PayPalParser(io.StringIO(csv_text), date_format, account, currency)

    def test_detects_dot_dmy_format(self):
        parser = self._auto_parser(_csv(_row(Date="02.01.2025")))
        stmt = parser.parse()
        self.assertEqual(parser.date_format, "%d.%m.%Y")
        self.assertEqual(stmt.lines[0].date, datetime(2025, 1, 2))

    def test_detects_iso_format(self):
        parser = self._auto_parser(_csv(_row(Date="2025-01-02")))
        stmt = parser.parse()
        self.assertEqual(parser.date_format, "%Y-%m-%d")
        self.assertEqual(stmt.lines[0].date, datetime(2025, 1, 2))

    def test_detects_slash_dmy_when_day_gt_12(self):
        rows = [
            _row(Date="01/01/2025", **{"Transaction ID": "T1"}),
            _row(Date="13/06/2025", **{"Transaction ID": "T2"}),
        ]
        parser = self._auto_parser(_csv(*rows))
        parser.parse()
        self.assertEqual(parser.date_format, "%d/%m/%Y")

    def test_detects_slash_mdy_when_second_slot_gt_12(self):
        rows = [
            _row(Date="01/01/2025", **{"Transaction ID": "T1"}),
            _row(Date="06/13/2025", **{"Transaction ID": "T2"}),
        ]
        parser = self._auto_parser(_csv(*rows))
        parser.parse()
        self.assertEqual(parser.date_format, "%m/%d/%Y")

    def test_slash_ambiguous_defaults_to_dmy(self):
        parser = self._auto_parser(_csv(_row(Date="01/02/2025")))
        parser.parse()
        self.assertEqual(parser.date_format, "%d/%m/%Y")

    def test_ambiguous_slash_with_usd_currency_picks_mdy(self):
        parser = self._auto_parser(_csv(_row(Date="01/02/2025", Currency="USD")))
        parser.parse()
        self.assertEqual(parser.date_format, "%m/%d/%Y")

    def test_ambiguous_slash_with_non_usd_currency_stays_dmy(self):
        parser = self._auto_parser(_csv(_row(Date="01/02/2025", Currency="GBP")))
        parser.parse()
        self.assertEqual(parser.date_format, "%d/%m/%Y")

    def test_unambiguous_dmy_beats_usd_currency_hint(self):
        rows = [
            _row(Date="01/01/2025", Currency="USD", **{"Transaction ID": "T1"}),
            _row(Date="13/06/2025", Currency="USD", **{"Transaction ID": "T2"}),
        ]
        parser = self._auto_parser(_csv(*rows))
        parser.parse()
        self.assertEqual(parser.date_format, "%d/%m/%Y")

    def test_ini_override_beats_autodetect(self):
        parser = self._auto_parser(
            _csv(_row(Date="06/13/2025")),
            date_format="%m/%d/%Y",
        )
        stmt = parser.parse()
        self.assertEqual(parser.date_format, "%m/%d/%Y")
        self.assertEqual(stmt.lines[0].date, datetime(2025, 6, 13))

    def test_detects_single_currency(self):
        parser = self._auto_parser(_csv(_row(Currency="USD")))
        stmt = parser.parse()
        self.assertEqual(stmt.currency, "USD")

    def test_detects_majority_currency_in_mixed_file(self):
        rows = [
            _row(Currency="EUR", **{"Transaction ID": "T1"}),
            _row(Currency="EUR", **{"Transaction ID": "T2"}),
            _row(Currency="USD", **{"Transaction ID": "T3"}),
        ]
        parser = self._auto_parser(_csv(*rows))
        stmt = parser.parse()
        self.assertEqual(stmt.currency, "EUR")

    def test_missing_currency_raises(self):
        parser = self._auto_parser(_csv(_row(Currency="")))
        with self.assertRaises(ParseError) as ctx:
            parser.parse()
        self.assertIn("currency", str(ctx.exception).lower())

    def test_configured_currency_beats_autodetect(self):
        parser = self._auto_parser(_csv(_row(Currency="USD")), currency="GBP")
        stmt = parser.parse()
        self.assertEqual(stmt.currency, "GBP")

    def test_default_account_id_when_not_configured(self):
        parser = self._auto_parser(_csv(_row()))
        stmt = parser.parse()
        self.assertEqual(stmt.account_id, DEFAULT_ACCOUNT_ID)

    def test_configured_account_beats_default(self):
        parser = self._auto_parser(_csv(_row()), account="MyPayPal")
        stmt = parser.parse()
        self.assertEqual(stmt.account_id, "MyPayPal")


class ChronologicalSortTests(unittest.TestCase):

    def _parser(self, csv_text, date_format="%d/%m/%Y"):
        return PayPalParser(io.StringIO(csv_text), date_format, "ACC", "EUR")

    def test_reversed_rows_are_sorted(self):
        earliest = _row(
            Date="02/01/2025",
            Time="10:00:00",
            Net="-10,00",
            Balance="90,00",
            **{"Transaction ID": "FIRST"},
        )
        latest = _row(
            Date="10/01/2025",
            Time="10:00:00",
            Net="5,00",
            Gross="5,00",
            Balance="95,00",
            **{"Transaction ID": "SECOND"},
        )
        parser = self._parser(_csv(latest, earliest))
        stmt = parser.parse()
        self.assertEqual([sl.id for sl in stmt.lines], ["FIRST", "SECOND"])

    def test_start_balance_uses_chronologically_earliest_row(self):
        earliest = _row(
            Date="02/01/2025",
            Time="10:00:00",
            Net="-10,00",
            Balance="90,00",
            **{"Transaction ID": "FIRST"},
        )
        latest = _row(
            Date="10/01/2025",
            Time="10:00:00",
            Net="5,00",
            Gross="5,00",
            Balance="95,00",
            **{"Transaction ID": "SECOND"},
        )
        parser = self._parser(_csv(latest, earliest))
        stmt = parser.parse()
        self.assertEqual(stmt.start_balance, Decimal("100.00"))
        self.assertEqual(stmt.start_date, datetime(2025, 1, 2))

    def test_same_day_rows_sorted_by_time(self):
        later_time = _row(
            Date="02/01/2025",
            Time="15:00:00",
            **{"Transaction ID": "LATER"},
        )
        earlier_time = _row(
            Date="02/01/2025",
            Time="09:00:00",
            **{"Transaction ID": "EARLIER"},
        )
        parser = self._parser(_csv(later_time, earlier_time))
        stmt = parser.parse()
        self.assertEqual([sl.id for sl in stmt.lines], ["EARLIER", "LATER"])

    def test_already_sorted_rows_preserve_order(self):
        rows = [
            _row(Date="02/01/2025", **{"Transaction ID": "A"}),
            _row(Date="03/01/2025", **{"Transaction ID": "B"}),
            _row(Date="04/01/2025", **{"Transaction ID": "C"}),
        ]
        parser = self._parser(_csv(*rows))
        stmt = parser.parse()
        self.assertEqual([sl.id for sl in stmt.lines], ["A", "B", "C"])


class CurrencyConversionTests(unittest.TestCase):
    """PayPal exports a foreign-currency purchase as four rows; the parser
    collapses the redundant foreign zero-conversion and annotates the
    statement-currency merchant-debit row with orig_currency metadata."""

    @staticmethod
    def _conversion_rows(anchor_id="ANCHOR_USD_CHARGE"):
        # USD charge (anchor) — foreign currency, -12.95 USD
        charge = _row(
            Date="02/01/2025",
            Time="10:00:00",
            Currency="USD",
            Gross="-12,95",
            Net="-12,95",
            Balance="-12,95",
            **{
                "Transaction ID": anchor_id,
                "Reference Txn ID": "B-EXT-BILLING-AGREEMENT",
            },
        )
        # USD zero-conversion — same currency, opposite sign, cancels anchor
        zero_usd = _row(
            Date="02/01/2025",
            Time="10:00:01",
            Currency="USD",
            Gross="12,95",
            Net="12,95",
            Balance="0,00",
            **{
                "Transaction ID": "USD_ZERO_CONV",
                "Reference Txn ID": anchor_id,
                "Name": "",
            },
        )
        # EUR bank credit — statement currency, positive (opposite sign of anchor)
        bank_credit = _row(
            Date="02/01/2025",
            Time="10:00:02",
            Currency="EUR",
            Gross="12,98",
            Net="12,98",
            Balance="12,98",
            **{
                "Transaction ID": "EUR_BANK_CREDIT",
                "Reference Txn ID": anchor_id,
                "Name": "",
            },
        )
        # EUR merchant debit — statement currency, negative, no Bank Name
        merchant_debit = _row(
            Date="02/01/2025",
            Time="10:00:03",
            Currency="EUR",
            Gross="-12,98",
            Net="-12,98",
            Balance="0,00",
            **{
                "Transaction ID": "EUR_MERCHANT_DEBIT",
                "Reference Txn ID": anchor_id,
                "Name": "Foreign Merchant",
            },
        )
        return [charge, zero_usd, bank_credit, merchant_debit]

    def test_multi_currency_balance_uses_statement_currency_only(self):
        # Mix: a real EUR expense + an isolated USD charge whose running
        # USD balance would corrupt start/end_balance if not filtered.
        eur_expense = _row(
            Date="03/01/2025",
            Currency="EUR",
            Gross="-5,00",
            Net="-5,00",
            Balance="-5,00",
            **{"Transaction ID": "EUR_REAL"},
        )
        usd_charge = _row(
            Date="04/01/2025",
            Currency="USD",
            Gross="-20,00",
            Net="-20,00",
            Balance="-20,00",
            **{"Transaction ID": "USD_ISOLATED"},
        )
        parser = _parser(_csv(eur_expense, usd_charge), currency="EUR")
        stmt = parser.parse()
        # start_balance derived from the EUR row only: -5 - (-5) = 0
        self.assertEqual(stmt.start_balance, Decimal("0"))
        # end_balance = 0 + (-5 EUR); the USD -20 must NOT be included
        self.assertEqual(stmt.end_balance, Decimal("-5"))

    def test_foreign_conversion_pair_collapses(self):
        rows = self._conversion_rows()
        parser = _parser(_csv(*rows), currency="EUR")
        stmt = parser.parse()
        ids = [sl.id for sl in stmt.lines]
        # Anchor charge stays; zero-conversion is dropped; both EUR legs kept.
        self.assertIn("ANCHOR_USD_CHARGE", ids)
        self.assertNotIn("USD_ZERO_CONV", ids)
        self.assertIn("EUR_BANK_CREDIT", ids)
        self.assertIn("EUR_MERCHANT_DEBIT", ids)
        self.assertEqual(len(stmt.lines), 3)

    def test_orig_currency_annotated_on_merchant_debit(self):
        rows = self._conversion_rows()
        parser = _parser(_csv(*rows), currency="EUR")
        stmt = parser.parse()
        merchant_debit = next(sl for sl in stmt.lines if sl.id == "EUR_MERCHANT_DEBIT")
        self.assertIsNotNone(merchant_debit.orig_currency)
        self.assertEqual(merchant_debit.orig_currency.symbol, "USD")
        # OFX CURRATE = statement/symbol = |12.98 EUR| / |12.95 USD|
        self.assertEqual(
            merchant_debit.orig_currency.rate,
            Decimal("12.98") / Decimal("12.95"),
        )
        # Other rows must not carry orig_currency
        anchor = next(sl for sl in stmt.lines if sl.id == "ANCHOR_USD_CHARGE")
        self.assertIsNone(anchor.orig_currency)


if __name__ == "__main__":
    unittest.main()
