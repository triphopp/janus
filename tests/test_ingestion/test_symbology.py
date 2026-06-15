"""Symbology tests — silent-killer cases (section 12).

These tests MUST pass before merge.
Symbology errors don't throw — they silently corrupt backtests.
"""

import pytest
import pandas as pd
from ingestion.symbology import Symbology, InternalSymbol
from ingestion.settlement_loader import parse_pipe_row


REAL_BRENT_ROW = (
    "9/25/2024|North Sea|Brent Crude Futures|11/1/2024|B|C|10.0000|"
    "63.46000|-1.71000|9/25/2024|254|0.01000|0.00000"
)


class TestSymbologyUniqueness:
    """PRODUCT_ID must map to exactly one contract_root + hub."""

    def test_product_id_unique_per_contract(self, symbology):
        """product_id 254 must not map to both B (Brent) and CL (WTI)."""
        df = symbology.map_df
        for pid, rows in df.groupby("product_id"):
            assert rows["contract_root"].nunique() == 1, \
                f"product_id {pid} ambiguous: maps to {list(rows['contract_root'].unique())}"

    def test_no_duplicate_entries(self, symbology):
        """No duplicate rows in product map."""
        df = symbology.map_df
        dupes = df.duplicated(subset=["product_id", "hub", "contract_root"]).sum()
        assert dupes == 0, f"Found {dupes} duplicate entries"


class TestSymbologyRoundTrip:
    """resolve() → InternalSymbol → reverse() must return original keys."""

    def test_round_trip_brent(self, symbology):
        """Brent settle row: resolve then reverse must match."""
        sym = symbology.resolve(254, "North Sea", "B")
        back = symbology.reverse(sym)
        assert back["product_id"] == 254
        assert back["hub"] == "North Sea"
        assert back["contract_root"] == "B"

    def test_unknown_product_raises(self, symbology):
        """Resolving unknown product_id must raise KeyError."""
        with pytest.raises(KeyError):
            symbology.resolve(99999, "Mars", "XX")


class TestSymbologyNoOrphan:
    """Every row in raw data must find a mapping."""

    def test_no_orphan_after_join(self, symbology):
        """Simulated raw_df — all product_ids must be in map."""
        raw = pd.DataFrame({
            "product_id": [254, 425],
            "hub": ["North Sea", "Cushing"],
            "contract_root": ["B", "CL"],
        })
        orphans = symbology.validate_no_orphans(raw)
        assert len(orphans) == 0, f"Orphan product_ids: {orphans}"

    def test_orphan_detected(self, symbology):
        """Unmapped product_id must be flagged."""
        raw = pd.DataFrame({
            "product_id": [254, 99999],
            "hub": ["North Sea", "Mars"],
            "contract_root": ["B", "XX"],
        })
        orphans = symbology.validate_no_orphans(raw)
        assert 99999 in orphans


class TestRealRowParsing:
    """Parse actual pipe-delimited row → correct instrument_type, contract_root, strike."""

    def test_real_brent_option_row(self):
        """Row: 9/25/2024|North Sea|Brent Crude Futures|11/1/2024|B|C|10|63.46|...
        Must parse as: instrument_type='option', contract_root='B', strike=10, right='C'."""
        row = parse_pipe_row(REAL_BRENT_ROW)
        assert row["instrument_type"] == "option"
        assert row["contract_root"] == "B"
        assert row["strike"] == 10.0
        assert row["right"] == "C"

    def test_real_row_has_product_id(self):
        """Product ID must be extracted correctly."""
        row = parse_pipe_row(REAL_BRENT_ROW)
        assert row["product_id"] == 254

    def test_real_row_date_parsed(self):
        """US dates must parse correctly (month/day/year)."""
        row = parse_pipe_row(REAL_BRENT_ROW)
        assert row["as_of_date"].month == 9
        assert row["as_of_date"].day == 25
        assert row["as_of_date"].year == 2024
