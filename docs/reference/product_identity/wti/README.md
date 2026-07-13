# WTI Product Identity Reference

This reference pack keeps the evidence for distinguishing the current WTI feed
from CME `LO`/`LC` naming and from ICE European-style WTI products.

## Current Conclusion

For the local settlement file `data/WTI.csv`, the observed tuple is:

```text
PRODUCT_ID=425
HUB=WTI
PRODUCT=WTI Crude Futures
CONTRACT=T
CONTRACT TYPE=C/P/F
```

The option rows (`C` and `P`) should be interpreted as ICE WTI
American-style options on ICE WTI Crude Futures (`T`). The current Janus export
root `LO` is a CME-equivalent label, not the source-native ICE identity.

## Why Product ID Alone Is Not Enough

`PRODUCT_ID=425` is present in the feed file. The ICE MFT data dictionary
confirms that settlement snapshot `Product ID` is a unique numerical identifier
of `ProductName`, but the public ICE product specification pages use different
public product page ids, such as `213` for WTI Crude Futures and `908` for WTI
American-Style Options. Treat `425` as a provider/feed-specific id and require
the full tuple:

```text
provider + product_id + hub + product_name + contract + instrument_type
```

## Evidence Files

- `evidence/local-wti-product-id-profile.csv` shows that local `PRODUCT_ID=425`
  appears only with `WTI / WTI Crude Futures / T` and contract types `C`, `P`,
  and `F`.
- `evidence/ice-mft-data-dictionary-key-fields.csv` summarizes the ICE MFT data
  dictionary rows that define `Product ID`, `Contract Type`,
  `IMPLIED_VOLATILITY`, and `DELTA FACTOR`.
- `evidence/ice-wti-key-fields.csv` summarizes the ICE product specs that
  identify `T` as WTI Crude Futures and the matching American-style option.
- `evidence/cme-wti-option-code-key-rows.csv` extracts the CME `LO`, `LC`, and
  weekly WTI option rows from the CME strike/exercise workbook.
- `source_documents/` contains the downloaded official source snapshots.
- `MANIFEST.yaml` records URLs, hashes, confirmed claims, and the recommended
  Janus mapping.

## Mapping Policy

Use source-native identity first:

```yaml
product_identity:
  provider: ice_settlement_file
  source_product_id: 425
  hub: WTI
  source_product_name: WTI Crude Futures
  source_contract: T
  underlying_root: T
  exercise_style: american
  source_product_identity: ICE WTI American-Style Options
  cme_equivalent_root: LO
```

Use `cme_equivalent_root: LO` only when a downstream consumer explicitly wants
CME-style symbols. Do not infer `LO`, `LC`, or ICE `T/WUL/TDE` from
`pricing.model`.

## Open Evidence Gap

The public ICE MFT guide confirms what `Product ID` means. The local settlement
feed confirms that the WTI rows carry `PRODUCT_ID=425`. A public product-master
document explicitly listing `425 -> WTI Crude Futures` has not been found.

## Publishing Note

The files in `source_documents/` are retained as local source snapshots for
internal verification. Check the original providers' terms before publishing
these files outside the Janus workspace.
