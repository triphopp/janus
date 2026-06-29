"""Futures adapter — roll, term structure, session handling, scheduled events.

Generic for all futures instrument families.
Instrument specifics in config only.
"""

from typing import Tuple

import numpy as np
import pandas as pd

from .base import AdapterBase


class FuturesAdapter(AdapterBase):
    """Prepare futures data for core pipeline.

    Key concerns:
    - Continuous futures construction (roll adjustment)
    - Term structure features
    - Scheduled event flagging (generic — reads cfg['event_calendars'])
    - Session/time alignment
    """

    def prepare(self, raw_df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
        df = raw_df.copy()

        # ── Build continuous futures ──
        df = self.build_continuous_futures(df)

        # ── Flag scheduled events ──
        df = self.flag_scheduled_events(df)

        # ── Compute term structure ──
        df = self.compute_term_structure(df)

        # ── Returns ──
        df = df.sort_values(["as_of_date"])
        df["return_std"] = df["price_std"].pct_change()

        # ── Realized vol ──
        vol_window = self.cfg.get("vol_window", 21)
        df["vol_std"] = df["return_std"].rolling(vol_window, min_periods=5).std()

        # ── Build cfg ──
        cfg = {
            **self.cfg,
            "price_col": "price_std",
            "vol_col": "vol_std",
            "return_col": "return_std",
            "vol_window": vol_window,
            "trend_window": self.cfg.get("trend_window", 126),
            "purge_bars": self.cfg.get("purge_bars", 5),
            "regime_axes": self.cfg.get("regime_axes", ["vol_regime", "term_structure"]),
        }

        return df, cfg

    def build_continuous_futures(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """Build continuous futures price series.

        Roll convention: switch from front month to next at roll_days before expiry.
        Backward-adjust: add roll difference to preserve price continuity.
        """
        roll_days = self.cfg.get("roll_days", 5)
        df = raw_df.copy()

        if "delivery_month" not in df.columns or "expiry" not in df.columns:
            # Simple: use price as-is
            df["price_std"] = df.get("price", df.get("raw_close", 0))
            return df

        df = df.sort_values(["product_id", "as_of_date"])

        # Identify front-month contract at each date
        # For each date, use the contract with earliest expiry that hasn't rolled yet
        df["dte"] = (df["expiry"] - df["as_of_date"]).dt.days

        # Flag roll period
        df["is_roll_period"] = df["dte"] <= roll_days

        # Price as-is (for now — full continuous construction in futures_options_adapter)
        df["price_std"] = df.get("price", df.get("raw_close", 0))

        return df

    def flag_scheduled_events(self, df: pd.DataFrame) -> pd.DataFrame:
        """Flag dates that coincide with scheduled events.

        Generic — event calendar paths come from cfg['event_calendars'].
        The function does not know the business meaning of each event file.
        """
        event_files = self.cfg.get("event_calendars", [])
        if not event_files:
            return df

        from core.event_calendar import load_event_calendar, pit_event_mask

        df = df.copy()
        df["scheduled_event"] = False
        if "decision_time" not in df.columns:
            if "available_at" in df.columns:
                df["decision_time"] = pd.to_datetime(df["available_at"], utc=True)
            else:
                df["decision_time"] = pd.to_datetime(df["as_of_date"], utc=True)
        decision_time = pd.to_datetime(df["decision_time"], utc=True)
        row_dates = pd.to_datetime(df["as_of_date"]).dt.date

        for event_file in event_files:
            try:
                events = load_event_calendar(event_file, self.cfg)
            except (FileNotFoundError, KeyError, ValueError):
                continue  # Event file may not exist / be malformed for this instrument
            if events.empty:
                continue
            # PIT join: an event only flags a row once it was knowable by decision_time.
            # Events with unknown availability (NaT) never flag — they cannot leak.
            known_same_day_event = pit_event_mask(row_dates, decision_time, events)
            df["scheduled_event"] = df["scheduled_event"] | known_same_day_event

        # Per event type (for regime axes)
        event_regimes = self.cfg.get("event_regimes", [])
        for er in event_regimes:
            df[er] = df["scheduled_event"]  # Simplified; real impl per-event

        return df

    def compute_term_structure(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute term structure features.

        Contango: M2 > M1 (positive slope). Backwardation: M2 < M1 (negative).
        """
        df = df.copy()

        if "delivery_month" not in df.columns:
            df["term_structure_slope"] = 0.0
            return df

        # Compute spread only from futures rows; options share the same date index
        # and would create duplicates when set_index("as_of_date") is called.
        if "instrument_type" in df.columns:
            future_mask = df["instrument_type"].eq("future")
            if not future_mask.any():
                future_mask = df["instrument_type"].astype("string").str.lower().eq("future").fillna(False)
            fut_only = df.loc[future_mask, ["as_of_date", "delivery_month", "price_std"]].copy()
        else:
            fut_only = df[["as_of_date", "delivery_month", "price_std"]].copy()

        if fut_only.empty:
            df["term_structure_slope"] = 0.0
            return df

        fut_only = (
            fut_only.dropna(subset=["as_of_date", "delivery_month"])
            .sort_values(["as_of_date", "delivery_month"], kind="mergesort")
            .drop_duplicates(["as_of_date", "delivery_month"], keep="first")
        )
        fut_only["_rank"] = fut_only.groupby("as_of_date", sort=False).cumcount() + 1

        front = fut_only.loc[fut_only["_rank"] == 1].set_index("as_of_date")["price_std"]
        second = fut_only.loc[fut_only["_rank"] == 2].set_index("as_of_date")["price_std"]

        # Spread as fraction of front
        spread = (second - front) / front.replace(0, np.nan)
        df["term_structure_slope"] = df["as_of_date"].map(spread).fillna(0.0)

        return df
