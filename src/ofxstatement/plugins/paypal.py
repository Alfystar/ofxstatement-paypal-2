import csv
import logging
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal as D
from typing import Dict, Iterator, List, Optional, Set, TextIO, Tuple

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
    """PayPal activity CSV → OFX converter with locale auto-detection"""

    def get_parser(self, filename: str) -> "PayPalParser":
        charset = self.settings.get("charset", "UTF-8")
        f = open(filename, "r", encoding=charset)
        # Every setting below is optional — when absent the parser infers it
        # from the CSV contents (or falls back to a constant for account_id).
        # config.ini remains authoritative whenever it supplies a value.
        dataFormat = self.settings.get("dataformat")
        defaultAccount = self.settings.get("default_account")
        defaultCurrency = self.settings.get("default_currency")
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

    date_format: Optional[str]  # type: ignore[assignment]  # base class declares str; we stay None until split_records() infers it
    filetype: Optional[str]
    unique_id_set: Set[str] = set()
    # End balance captured from the last row's Balance column before parsing.
    # Used in parse() to cross-check the running total against PayPal's own
    # authoritative post-transaction balance.
    _expected_end_balance: Optional[D]
    # Transaction-ID → original-currency annotation discovered while collapsing
    # PayPal's 4-row currency-conversion pattern. Applied in parse_record_csv.
    _orig_currency_map: Dict[str, Currency]

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
        self._orig_currency_map = {}
        if account:
            self.statement.account_id = account
        else:
            self.statement.account_id = DEFAULT_ACCOUNT_ID
            logger.debug(
                "No 'default_account' configured; using '%s'", DEFAULT_ACCOUNT_ID
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
            # Sum only lines in the statement currency. Foreign-currency rows
            # (e.g. a USD charge on an EUR statement) carry their native
            # amount and would otherwise corrupt the EUR running total. The
            # statement currency-aligned conversion row that balances the
            # foreign charge is kept, so arithmetic still matches the Balance
            # column on the final row.
            total_amount = sum(
                (
                    sl.amount
                    for sl in stmt.lines
                    if sl.amount is not None
                    and sl.currency
                    and sl.currency.symbol == stmt.currency
                ),
                D(0),
            )
            dates = [sl.date for sl in stmt.lines if sl.date is not None]
            start_dt = min(dates)
            end_dt = max(dates)
            # If no row in the statement currency was seen (e.g. config
            # overrides to a currency not present in the file), default the
            # opening balance to 0 so end_balance arithmetic still succeeds.
            if stmt.start_balance is None:
                stmt.start_balance = D(0)
            # recalculate_balance() would re-sum over ALL lines (including
            # foreign-currency) and overwrite end_balance, so we derive
            # start_date/end_date ourselves and skip it.
            stmt.start_date = start_dt
            stmt.end_date = end_dt
            stmt.end_balance = stmt.start_balance + total_amount
            logger.info(
                "Parsed %d transaction(s) from %s to %s; "
                "start_balance=%s end_balance=%s "
                "(dataformat=%s currency=%s account=%s)",
                len(stmt.lines),
                start_dt.date(),
                end_dt.date(),
                stmt.start_balance,
                stmt.end_balance,
                self.date_format,
                stmt.currency,
                stmt.account_id,
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
        if self._expected_end_balance is None or stmt.end_balance is None:
            return
        if stmt.end_balance == self._expected_end_balance:
            return
        diff = stmt.end_balance - self._expected_end_balance
        logger.warning(
            "Balance sanity check failed: computed end_balance=%s but the "
            "last row's Balance column reports %s (difference %s). Likely "
            "causes: amount parsing drift, a skipped row, or a PayPal "
            "column-layout change.",
            stmt.end_balance,
            self._expected_end_balance,
            diff,
        )

    def split_records(self) -> Iterator[List[str]]:
        """Return iterable object consisting of a line per transaction."""

        reader = csv.reader(self.fin, delimiter=",")
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
                len(header),
                len(self.valid_header),
                header,
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
        self._collapse_currency_conversions(rows)
        return iter(rows)

    def _capture_expected_end_balance(self, rows: List[List[str]]) -> None:
        """Record the last (chronologically latest) row's Balance column.

        This is PayPal's authoritative post-transaction balance; parse()
        compares it against the running total derived from line amounts
        as a drift-detection check. Only rows in the statement currency
        are considered — foreign-currency rows carry a running balance in
        their own currency bucket, which must not be compared against the
        EUR/USD/... running total we compute.
        """
        if not rows:
            return
        balance_idx = self.valid_header.index("Balance")
        currency_idx = self.valid_header.index("Currency")
        for row in reversed(rows):
            if row[currency_idx] != self.statement.currency:
                continue
            try:
                self._expected_end_balance = D(self._normalize_amount(row[balance_idx]))
            except Exception:
                # Unparseable balance is not fatal — the sanity check just
                # gets skipped. parse_record_csv will surface the real error.
                self._expected_end_balance = None
            return

    def _collapse_currency_conversions(self, rows: List[List[str]]) -> None:
        """Detect PayPal's 4-row currency-conversion pattern and collapse it.

        When a purchase is paid in a foreign currency, PayPal records four
        rows that all share the same ``Reference Txn ID`` (the anchor
        transaction's own ID):
          1. Foreign-currency charge (the anchor). TxnID = R.
          2. Foreign-currency zero-conversion: opposite-sign amount in the
             same foreign currency, cancelling row 1's foreign sub-balance.
             RefTxn = R.
          3. Statement-currency "bank credit" leg: positive amount.
             RefTxn = R.
          4. Statement-currency merchant-debit leg: negative amount that
             cancels row 3. RefTxn = R. This is the line a human recognises
             as "the actual purchase" in their statement currency.

        Bank Name is NOT a reliable discriminator for legs 3 and 4 — real
        PayPal exports have it empty on all conversion followers — so we
        identify them by sign instead: the merchant-debit shares the sign
        of the anchor (both outflows), the bank-credit is the opposite.

        This method:
          * Drops row 2 (the foreign zero-conversion) — bookkeeping noise
            that cancels row 1 in a separate foreign-currency sub-balance.
          * Populates ``self._orig_currency_map`` keyed by row 4's
            Transaction ID with a ``Currency(symbol=<foreign>, rate=...)``.
            parse_record_csv attaches it so OFX renders
            <CURRENCY>EUR</CURRENCY><ORIGCURRENCY>USD</ORIGCURRENCY>.
        """
        if not rows or not self.statement.currency:
            return
        txn_idx = self.valid_header.index("Transaction ID")
        ref_idx = self.valid_header.index("Reference Txn ID")
        cur_idx = self.valid_header.index("Currency")
        gross_idx = self.valid_header.index("Gross")

        # Build map: anchor TxnID → row index
        anchor_by_id: Dict[str, int] = {
            row[txn_idx]: i for i, row in enumerate(rows) if row[txn_idx]
        }

        # Group non-anchor rows by their RefTxn ID
        followers_by_ref: Dict[str, List[int]] = {}
        for i, row in enumerate(rows):
            ref = row[ref_idx]
            if ref and ref in anchor_by_id and anchor_by_id[ref] != i:
                followers_by_ref.setdefault(ref, []).append(i)

        drop_indices: Set[int] = set()
        for anchor_id, follower_indices in followers_by_ref.items():
            anchor_i = anchor_by_id[anchor_id]
            anchor = rows[anchor_i]
            foreign_cur = anchor[cur_idx]
            if foreign_cur == self.statement.currency:
                continue
            try:
                anchor_amt = D(self._normalize_amount(anchor[gross_idx]))
            except Exception:
                continue
            if len(follower_indices) != 3:
                continue

            # anchor is an outflow in USD (sign < 0) or an inflow (sign > 0);
            # merchant-debit shares that sign, bank-credit opposes it.
            foreign_zero = None
            bank_credit = None
            merchant_debit = None
            for fi in follower_indices:
                frow = rows[fi]
                try:
                    famt = D(self._normalize_amount(frow[gross_idx]))
                except Exception:
                    break
                if frow[cur_idx] == foreign_cur and famt == -anchor_amt:
                    foreign_zero = fi
                elif frow[cur_idx] == self.statement.currency:
                    if (famt > 0) != (anchor_amt > 0) and famt != 0:
                        bank_credit = (fi, famt)
                    elif (famt > 0) == (anchor_amt > 0) and famt != 0:
                        merchant_debit = (fi, famt)

            if foreign_zero is None or bank_credit is None or merchant_debit is None:
                continue
            bc_amt = bank_credit[1]
            md_amt = merchant_debit[1]
            # Bank credit and merchant debit must cancel each other.
            if bc_amt + md_amt != D(0):
                continue

            drop_indices.add(foreign_zero)
            md_row = rows[merchant_debit[0]]
            md_txn_id = md_row[txn_idx]
            if md_txn_id:
                # OFX CURRATE = |statement-currency amount| / |foreign amount|
                # (ratio of statement/symbol per OFX 2.2 §5.2).
                rate = abs(md_amt) / abs(anchor_amt)
                self._orig_currency_map[md_txn_id] = Currency(
                    symbol=foreign_cur, rate=rate
                )

        if drop_indices:
            kept = [row for i, row in enumerate(rows) if i not in drop_indices]
            rows.clear()
            rows.extend(kept)
            logger.info(
                "Collapsed %d foreign-currency conversion row(s); "
                "annotated %d merchant-debit row(s) with orig_currency",
                len(drop_indices),
                len(self._orig_currency_map),
            )

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
            inferred = self._detect_date_format(
                rows, date_idx, currency_hint=file_currency
            )
            if inferred is None:
                raise ParseError(
                    0,
                    "Could not auto-detect date format from CSV contents. "
                    "Set 'dataformat' in config.ini (e.g. '%d.%m.%Y').",
                )
            self.date_format = inferred
            logger.debug("Auto-detected dataformat='%s'", inferred)

        if not self.statement.currency:
            if file_currency is None:
                raise ParseError(
                    0,
                    "Could not auto-detect currency from CSV contents. "
                    "Set 'default_currency' in config.ini (e.g. 'EUR').",
                )
            self.statement.currency = file_currency
            logger.debug("Auto-detected currency='%s'", file_currency)

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
        s = value.replace("\xa0", "").replace(" ", "")
        last_comma = s.rfind(",")
        last_dot = s.rfind(".")
        if last_comma > last_dot:
            s = s.replace(".", "").replace(",", ".")
        elif last_dot > last_comma:
            s = s.replace(",", "")
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
        if "." in first:
            return "%d.%m.%Y"
        if "-" in first:
            return "%Y-%m-%d"
        if "/" not in first:
            return None

        dmy_confirmed = False
        mdy_confirmed = False
        for s in samples:
            parts = s.split("/")
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
            #
            # start_balance must only be derived from a row in the statement
            # currency — a leading foreign-currency row carries its Balance in
            # its own currency bucket, which would corrupt the EUR/USD/...
            # opening balance. For the first seen row, record start_date; only
            # set start_balance when the row matches the statement currency.
            self.statement.start_date = datetime.strptime(
                line[date_idx], self.date_format
            )
        if (
            self.statement.start_balance is None
            and line[currency_idx] == self.statement.currency
        ):
            balance_str = self._normalize_amount(line[balance_idx])
            amount_str = self._normalize_amount(line[amount_idx])
            self.statement.start_balance = D(balance_str) - D(amount_str)
            logger.debug(
                "Derived start_balance=%s from first %s row (balance=%s, net=%s)",
                self.statement.start_balance,
                self.statement.currency,
                balance_str,
                amount_str,
            )

        smt_line = StatementLine()
        smt_line.id = line[id_idx]
        smt_line.date = datetime.strptime(line[date_idx], self.date_format)
        smt_line.currency = Currency(line[currency_idx])
        smt_line.amount = D(self._normalize_amount(line[amount_idx]))
        # StatementLine has no declared `fee` attribute — this stores the
        # parsed fee dynamically so the tests can assert on it; OFX rendering
        # does not emit it.
        smt_line.fee = D(self._normalize_amount(line[fee_idx]))  # type: ignore[attr-defined]

        # If this row is the statement-currency merchant-debit leg of a
        # PayPal 4-row conversion, _collapse_currency_conversions will have
        # stashed a Currency(symbol=<foreign>, rate=<local/foreign>) here.
        # Applied so OFX renders <CURRENCY> + <ORIGCURRENCY> for consumers.
        orig = self._orig_currency_map.get(smt_line.id)
        if orig is not None:
            smt_line.orig_currency = orig

        smt_line.refnum = line[refnum_idx]
        # Bank Name is populated only when PayPal settles to/from a linked
        # bank account (deposits, withdrawals), which map to OFX XFER.
        # Otherwise we classify by sign: outflows as PAYMENT, inflows as
        # DIRECTDEP (direct deposit / credit).
        if line[bankName_idx]:
            smt_line.trntype = "XFER"
        else:
            smt_line.trntype = "PAYMENT" if smt_line.amount < 0 else "DIRECTDEP"

        # `payee` → OFX <NAME> → short counterparty label that downstream
        # consumers (GnuCash's "Beschreibung", HomeBank's "Payee") show as
        # the headline of the transaction. `memo` → OFX <MEMO> → longer
        # free-form context shown in a separate column (GnuCash's
        # "Buchungstext"). Keeping them distinct avoids the same blob
        # appearing in both columns.
        name_idx = self.valid_header.index("Name")
        desc_idx = self.valid_header.index("Description")
        smt_line.payee = line[name_idx] or line[desc_idx]

        # Memo carries the richer context (email, invoice, reference IDs,
        # bank details) minus Name/Description which are already in payee.
        memoLine: List[str] = []
        for column_name in [
            "Description",
            "From Email Address",
            "Gross",
            "Fee",
            "Invoice Number",
            "Reference Txn ID",
            "Bank Name",
        ]:
            # Description is in payee when Name is empty; skip in that case
            # to avoid duplication.
            if column_name == "Description" and not line[name_idx]:
                continue
            memo_idx = self.valid_header.index(column_name)
            if len(line[memo_idx]):
                memoLine.append(f"{column_name}:{line[memo_idx]}")
        smt_line.memo = "|".join(memoLine)

        logger.debug(
            "Parsed row id=%s date=%s amount=%s trntype=%s",
            smt_line.id,
            smt_line.date.date(),
            smt_line.amount,
            smt_line.trntype,
        )
        return smt_line
