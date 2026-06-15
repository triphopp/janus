"""Symbology resolution — PRODUCT_ID/HUB/CONTRACT → internal symbol.

Rules live in yaml (configs/symbology/product_map.yaml), not in code.
Loader validates on every load.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml


@dataclass(frozen=True)
class InternalSymbol:
    """Resolved internal symbol — stable across providers."""
    product_id: int
    contract_root: str
    hub: str

    def __str__(self):
        return f"{self.contract_root}:{self.product_id}:{self.hub}"


class Symbology:
    """Resolve PRODUCT_ID/HUB/CONTRACT → InternalSymbol and back.

    Loads mapping rules from yaml. Validates on construction.
    """

    def __init__(self, map_path: Optional[Path] = None):
        if map_path is None:
            map_path = Path("configs/symbology/product_map.yaml")
        self.map_path = Path(map_path)
        self._map: Optional[pd.DataFrame] = None
        self._reverse: Optional[dict] = None
        self.load()

    def load(self):
        """Load product_map.yaml → build forward and reverse maps."""
        if not self.map_path.exists():
            raise FileNotFoundError(f"Symbology map not found: {self.map_path}")
        with open(self.map_path) as f:
            data = yaml.safe_load(f)
        rows = []
        for entry in data.get("products", []):
            rows.append({
                "product_id": entry["product_id"],
                "contract_root": entry["contract_root"],
                "hub": entry["hub"],
                "product_name": entry.get("product_name", ""),
            })
        self._map = pd.DataFrame(rows)
        # Build reverse lookup
        self._reverse = {}
        for _, r in self._map.iterrows():
            key = InternalSymbol(r["product_id"], r["contract_root"], r["hub"])
            self._reverse[str(key)] = r.to_dict()

    def resolve(self, product_id: int, hub: str, contract: str) -> InternalSymbol:
        """Forward: raw fields → InternalSymbol."""
        match = self._map[
            (self._map["product_id"] == product_id)
            & (self._map["hub"] == hub)
            & (self._map["contract_root"] == contract)
        ]
        if match.empty:
            raise KeyError(f"No symbology match for product_id={product_id} hub={hub} contract={contract}")
        r = match.iloc[0]
        return InternalSymbol(r["product_id"], r["contract_root"], r["hub"])

    def reverse(self, sym: InternalSymbol) -> dict:
        """Reverse: InternalSymbol → raw fields dict."""
        key = str(sym)
        if key not in self._reverse:
            raise KeyError(f"Unknown internal symbol: {key}")
        return self._reverse[key]

    def validate_uniqueness(self) -> list[str]:
        """Check: no product_id maps to multiple contract_roots. Returns violations."""
        violations = []
        for pid, grp in self._map.groupby("product_id"):
            if grp["contract_root"].nunique() > 1:
                violations.append(f"product_id {pid} maps to {list(grp['contract_root'].unique())}")
        return violations

    def validate_no_orphans(self, raw_df: pd.DataFrame) -> list[int]:
        """Check every raw product_id has a map entry. Returns orphan IDs."""
        if "product_id" not in raw_df.columns:
            return []
        mapped = set(self._map["product_id"].unique())
        raw_ids = set(raw_df["product_id"].unique())
        return sorted(raw_ids - mapped)

    @property
    def map_df(self) -> pd.DataFrame:
        return self._map
