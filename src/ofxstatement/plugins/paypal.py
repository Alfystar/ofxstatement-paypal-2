import csv
import logging
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal as D
from typing import Iterator, List, Optional, Set, TextIO, Tuple

from ofxstatement import statement
from ofxstatement.exceptions import ParseError
from ofxstatement.parser import CsvStatementParser
from ofxstatement.plugin import Plugin
from ofxstatement.statement import Currency, Statement, StatementLine


logger = logging.getLogger(__name__)

# Fallback account id when config.ini sets no 'default_account'. The OFX
# account identifier is a label for downstream consumers, not a PayPal account
# number, so a stable constant is acceptable.
DEFAULT_ACCOUNT_ID = "PayPal"


class PayPalPlugin(Plugin):

    def get_parser(self, filename: str) -> "PayPalParser":
        charset = self.settings.get("charset", "UTF-8")
        f = open(filename, 'r', encoding=charset)
        # Every setting below is optional — when absent the parser infers it
        # from the CSV contents (or falls back to a constant for account_id).
        # config.ini remains authoritative whenever it supplies a value.
        dataFormat = self.settings.get("dataformat")
        defaultAccount = self.settings.get("default_account")
        defaultCurrency = self.settings.get("default_currency")

        logger.info(
            "Opening PayPal CSV %s (charset=%s, dataformat=%s, account=%s, currency=%s)",
            filename, charset,
            dataFormat or "<auto-detect>",
            defaultAccount or f"<default: {DEFAULT_ACCOUNT_ID}>",
            defaultCurrency or "<auto-detect>",
        )

        return PayPalParser(f, dataFormat, defaultAccount, defaultCurrency)


class PayPalParser(CsvStatementParser):
    # Canonical column layout of PayPal's activity CSV export. Only the ORDER
    # and LENGTH of this list are load-bearing — the string values are English
    # labels used by the code to look up column indices, but PayPal localises
    # the actual header row (Datum/Uhrzeit in German, Date/Heure in French,
    # etc.). Validation in split_records() therefore checks column count, not
    # names. If PayPal ever reorders columns, this list must be updated.
    valid_header: List[str] = [
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

    date_format: Optional[str]
    filetype: Optional[str]
    unique_id_set: Set[str] = set()
    # End balance captured from the last row's Balance column before parsing.
    # Used in parse() to cross-check the running total against PayPal's own
    # authoritative post-transaction balance.
    _expected_end_balance: Optional[D]

    def __init__(
        self,
        filePaypal: TextIO,
        dataFormat: Optional[str],
        account: Optional[str],
        currency: Optional[str],
    ) -> None:
        super().__init__(filePaypal)
        # date_format and currency stay None here if not configured; they are
        # populated in split_records() after scanning the CSV.
        self.date_format = dataFormat
        self.filetype = None
        self._expected_end_balance = None
        if account:
            self.statement.account_id = account
        else:
            self.statement.account_id = DEFAULT_ACCOUNT_ID
            logger.info(
                "No 'default_account' configured; using '%s'. "
                "Override via 'ofxstatement edit-config' if needed.",
                DEFAULT_ACCOUNT_ID,
            )
        self.statement.currency = currency

    def _setFileType(self) -> None:
        self.filetype = "csv"

    def parse(self) -> Statement:
        """Main entry point for parsers.

        super() implementation will call split_records and parse_record to
        process the file.
        """
        self._setFileType()
        stmt = super(PayPalParser, self).parse()
        if stmt.lines:
            total_amount = sum(sl.amount for sl in stmt.lines)
            stmt.end_balance = stmt.start_balance + total_amount
            stmt.end_date = max(sl.date for sl in stmt.lines)
            # recalculate_balance() derives start_date via min() over line
            # dates and would raise on an empty statement — guarded above.
            statement.recalculate_balance(stmt)
            logger.info(
                "Parsed %d transaction(s) from %s to %s; start_balance=%s end_balance=%s",
                len(stmt.lines), stmt.start_date, stmt.end_date,
                stmt.start_balance, stmt.end_balance,
            )
            self._check_balance_consistency(stmt)
        else:
            logger.warning("PayPal CSV contained a header but no transaction rows")
        return stmt

    def _check_balance_consistency(self, stmt: Statement) -> None:
        """Warn if the running total diverges from PayPal's post-tx balance.

        PayPal's Balance column on the last row is the authoritative
        post-transaction balance. If (start_balance + Σ amounts) doesn't
        equal that value, something drifted — likely `_normalize_amount`
        mishandling a new locale, a silently skipped row, or a column
        layout change that isn't caught by header validation.
        """
        if self._expected_end_balance is None:
            return
        if stmt.end_balance == self._expected_end_balance:
            return
        diff = stmt.end_balance - self._expected_end_balance
        logger.warning(
            "Balance sanity check failed: computed end_balance=%s but the "
            "last row's Balance column reports %s (difference %s). Likely "
            "causes: amount parsing drift, a skipped row, or a PayPal "
            "column-layout change.",
            stmt.end_balance, self._expected_end_balance, diff,
        )

    def split_records(self) -> Iterator[List[str]]:
        """Return iterable object consisting of a line per transaction."""

        reader = csv.reader(self.fin, delimiter=',')
        header = next(reader, None)
        if header is None:
            logger.error("PayPal CSV is empty: no header row found")
            raise ParseError(0, "PayPal CSV is empty: no header row found")
        if len(header) != len(self.valid_header):
            # Header labels are locale-dependent, so we can only validate the
            # shape. A mismatch means either PayPal changed the export format
            # or the file isn't a PayPal activity CSV at all.
            logger.error(
                "Unexpected PayPal CSV header: got %d columns, expected %d. Header row: %r",
                len(header), len(self.valid_header), header,
            )
            raise ParseError(
                1,
                f"Unexpected PayPal CSV header: got {len(header)} columns, "
                f"expected {len(self.valid_header)}. The column layout may "
                f"have changed or the wrong file was supplied.",
            )
        logger.debug("PayPal CSV header validated (%d columns)", len(header))

        # Data rows are materialised so we can scan them for auto-detection
        # of the date format and currency before parse_record_csv() runs.
        # PayPal exports rarely exceed a few thousand rows, so the memory
        # cost is negligible.
        rows: List[List[str]] = list(reader)
        self._auto_detect_settings(rows)
        # Enforce chronological order. parse_record_csv derives start_balance
        # from the first row it sees (Balance − Net), which is only correct
        # when that row is the earliest transaction. PayPal's export is
        # usually sorted, but this guarantees correctness regardless.
        self._sort_rows_chronologically(rows)
        self._capture_expected_end_balance(rows)
        return iter(rows)

    def _capture_expected_end_balance(self, rows: List[List[str]]) -> None:
        """Record the last (chronologically latest) row's Balance column.

        This is PayPal's authoritative post-transaction balance; parse()
        compares it against the running total derived from line amounts
        as a drift-detection check.
        """
        if not rows:
            return
        balance_idx = self.valid_header.index("Balance")
        try:
            self._expected_end_balance = D(self._normalize_amount(rows[-1][balance_idx]))
        except Exception:
            # Unparseable balance is not fatal — the sanity check just
            # gets skipped. parse_record_csv will surface the real error.
            self._expected_end_balance = None

    def _auto_detect_settings(self, rows: List[List[str]]) -> None:
        """Infer date_format and statement.currency from the CSV body.

        Values supplied via config.ini are honoured as overrides: detection
        only runs when the corresponding attribute is still unset.

        Currency is detected first (regardless of whether the statement
        currency is overridden) because it also serves as a tiebreaker for
        ambiguous slash date formats — a USD-dominated file is much more
        likely to be MDY than DMY.
        """
        if not rows:
            return

        currency_idx = self.valid_header.index("Currency")
        file_currency = self._detect_currency(rows, currency_idx)

        if self.date_format is None:
            date_idx = self.valid_header.index("Date")
            inferred = self._detect_date_format(rows, date_idx, currency_hint=file_currency)
            if inferred is None:
                raise ParseError(
                    0,
                    "Could not auto-detect date format from CSV contents. "
                    "Set 'dataformat' in config.ini (e.g. '%d.%m.%Y').",
                )
            self.date_format = inferred
            logger.info(
                "Auto-detected dataformat='%s' from CSV contents. "
                "Set 'dataformat' in config.ini to override.",
                inferred,
            )

        if not self.statement.currency:
            if file_currency is None:
                raise ParseError(
                    0,
                    "Could not auto-detect currency from CSV contents. "
                    "Set 'default_currency' in config.ini (e.g. 'EUR').",
                )
            self.statement.currency = file_currency
            logger.info(
                "Auto-detected currency='%s' from CSV contents. "
                "Set 'default_currency' in config.ini to override.",
                file_currency,
            )

    def _sort_rows_chronologically(self, rows: List[List[str]]) -> None:
        """Sort rows in place by (Date, Time) ascending.

        Rows with unparseable dates are pushed to the end so sorting never
        raises; parse_record_csv() will surface the real error when it
        attempts to parse them.
        """
        if len(rows) < 2:
            return
        date_idx = self.valid_header.index("Date")
        time_idx = self.valid_header.index("Time")
        original = list(rows)
        rows.sort(key=lambda r: self._chronological_key(r, date_idx, time_idx))
        if rows != original:
            logger.info("Sorted %d transaction row(s) chronologically", len(rows))

    def _chronological_key(
        self, row: List[str], date_idx: int, time_idx: int
    ) -> Tuple[int, datetime]:
        try:
            dt = datetime.strptime(row[date_idx], self.date_format or "")
        except (ValueError, IndexError, TypeError):
            return (1, datetime.max)
        time_str = row[time_idx] if time_idx < len(row) else ""
        try:
            t = datetime.strptime(time_str, "%H:%M:%S").time()
            dt = dt.replace(hour=t.hour, minute=t.minute, second=t.second)
        except (ValueError, TypeError):
            pass
        return (0, dt)

    @staticmethod
    def _normalize_amount(value: str) -> str:
        """Normalise a locale-formatted amount string for Decimal parsing.

        PayPal exports amounts in the locale of the account, so a single
        file may contain:
          * European: '1.234,56', '1 234,56', '1\u00a0234,56' (dot / space /
            NBSP thousands, comma decimal)
          * US: '1,234.56' (comma thousands, dot decimal)
          * No thousands: '1234,56', '1234.56', '-9,99', '0'

        Strategy: strip all whitespace (regular space and NBSP), then treat
        whichever of ',' or '.' appears LAST as the decimal separator and
        remove every occurrence of the other character (thousands).
        """
        s = value.replace('\xa0', '').replace(' ', '')
        last_comma = s.rfind(',')
        last_dot = s.rfind('.')
        if last_comma > last_dot:
            s = s.replace('.', '').replace(',', '.')
        elif last_dot > last_comma:
            s = s.replace(',', '')
        return s

    @staticmethod
    def _detect_date_format(
        rows: List[List[str]],
        date_idx: int,
        currency_hint: Optional[str] = None,
    ) -> Optional[str]:
        """Infer a strptime format string from sampled date cells.

        Strategy:
          * '.' separator → European DMY ('%d.%m.%Y').
          * '-' separator → ISO YMD ('%Y-%m-%d').
          * '/' separator → evidence-driven DMY/MDY detection.
            A day-slot value > 12 in any row pins the format. If evidence
            is missing (all days ≤ 12), currency_hint='USD' disambiguates
            to MDY; otherwise we default to DMY, which is PayPal's format
            for every non-US export.
        """
        samples = [r[date_idx] for r in rows if r and date_idx < len(r) and r[date_idx]]
        if not samples:
            return None
        first = samples[0]
        if '.' in first:
            return "%d.%m.%Y"
        if '-' in first:
            return "%Y-%m-%d"
        if '/' not in first:
            return None

        dmy_confirmed = False
        mdy_confirmed = False
        for s in samples:
            parts = s.split('/')
            if len(parts) != 3:
                continue
            try:
                first_slot, second_slot = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if first_slot > 12:
                dmy_confirmed = True
            if second_slot > 12:
                mdy_confirmed = True

        if dmy_confirmed and not mdy_confirmed:
            return "%d/%m/%Y"
        if mdy_confirmed and not dmy_confirmed:
            return "%m/%d/%Y"
        # Ambiguous (or conflicting) — fall back to currency-based heuristic.
        if currency_hint == "USD":
            return "%m/%d/%Y"
        return "%d/%m/%Y"

    @staticmethod
    def _detect_currency(rows: List[List[str]], currency_idx: int) -> Optional[str]:
        """Infer statement-level currency from sampled Currency cells.

        A PayPal account can hold multiple currencies (balances per
        currency), but OFX wants a single statement-level currency — so
        we pick the most frequent value across the file. The per-line
        StatementLine.currency still carries the exact code for each
        transaction, so mixed-currency files remain lossless.
        """
        samples = [
            r[currency_idx]
            for r in rows
            if r and currency_idx < len(r) and r[currency_idx]
        ]
        if not samples:
            return None
        return Counter(samples).most_common(1)[0][0]

    def fix_amount(self, value: str) -> str:
        dbt_re = r"(.*)(Dr)$"
        cdt_re = r"Cr$"
        dbt_subst = "-\\1"
        cdt_subst = ""
        result = re.sub(dbt_re, dbt_subst, value, 0)
        result = re.sub(cdt_re, cdt_subst, result, 0)

        # Consider "--" as a reversal entry
        reversal_re = r"^--"
        reversal_subst = ""
        return re.sub(reversal_re, reversal_subst, result, 0)

    def parse_record(self, line: List[str]) -> Optional[StatementLine]:
        """Parse given transaction line and return StatementLine object."""
        if self.filetype == "csv":
            return self.parse_record_csv(line)
        else:
            return self.parse_record_pdf(line)

    def parse_record_pdf(self, line: List[str]) -> Optional[StatementLine]:
        return None

    def parse_record_csv(self, line: List[str]) -> StatementLine:
        id_idx = self.valid_header.index("Transaction ID")
        date_idx = self.valid_header.index("Date")
        amount_idx = self.valid_header.index("Net")
        fee_idx = self.valid_header.index("Fee")
        currency_idx = self.valid_header.index("Currency")
        balance_idx = self.valid_header.index("Balance")
        refnum_idx = self.valid_header.index("Invoice Number")
        bankName_idx = self.valid_header.index("Bank Name")

        assert self.date_format is not None, "split_records() must populate date_format"

        if not self.statement.start_date:
            # PayPal's "Balance" column is the running balance AFTER the row's
            # Net amount has been applied, so the opening balance of the
            # statement is (first row's Balance) − (first row's Net).
            # split_records() sorts rows chronologically, so the first row we
            # see is guaranteed to be the earliest transaction.
            self.statement.start_date = datetime.strptime(line[date_idx], self.date_format)
            balance_str = self._normalize_amount(line[balance_idx])
            amount_str = self._normalize_amount(line[amount_idx])
            self.statement.start_balance = D(balance_str) - D(amount_str)
            logger.debug(
                "Derived start_balance=%s from first row (balance=%s, net=%s)",
                self.statement.start_balance, balance_str, amount_str,
            )

        smt_line = StatementLine()
        smt_line.id = line[id_idx]
        smt_line.date = datetime.strptime(line[date_idx], self.date_format)
        smt_line.currency = Currency(line[currency_idx])
        smt_line.amount = D(self._normalize_amount(line[amount_idx]))
        smt_line.fee = D(self._normalize_amount(line[fee_idx]))

        smt_line.refnum = line[refnum_idx]
        # Bank Name is populated only when PayPal settles to/from a linked
        # bank account (deposits, withdrawals), which map to OFX XFER.
        # Otherwise we classify by sign: outflows as PAYMENT, inflows as
        # DIRECTDEP (direct deposit / credit).
        if line[bankName_idx]:
            smt_line.trntype = "XFER"
        else:
            smt_line.trntype = "PAYMENT" if smt_line.amount < 0 else "DIRECTDEP"

        # Memo concatenates the populated source columns into a single
        # pipe-delimited string so the downstream OFX consumer keeps the
        # full context (counterparty, description, invoice, etc.) in one
        # searchable field.
        memoLine: List[str] = []
        for column_name in [
            "Name",
            "From Email Address",
            "Description",
            "Gross",
            "Fee",
            "Invoice Number",
            "Reference Txn ID",
            "Bank Name",
        ]:
            memo_idx = self.valid_header.index(column_name)
            if len(line[memo_idx]):
                memoLine.append(f"{column_name}:{line[memo_idx]}")
        smt_line.memo = "|".join(memoLine)

        logger.debug(
            "Parsed row id=%s date=%s amount=%s trntype=%s",
            smt_line.id, smt_line.date.date(), smt_line.amount, smt_line.trntype,
        )
        return smt_line
