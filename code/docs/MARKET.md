# Market data contract

The tool supports any explicitly configured issuer/listing; an SEC CIK is optional for market-only work. A ticker alone is not sufficient to infer the listing exchange, currency, or provider symbol. `market_prices` accepts `entity`, exact `start_date`, `end_date`, optional `cutoff`, `source=auto|replay|live`, and `price_type=close|adjusted_close`.

## Offline import (schema version 1)

`PriceSnapshot.from_path(path, expected_hash=None)` validates an original JSON file and records its SHA-256. `ToolRegistry(..., market_data=path)` exposes it to the agent. The following is a **synthetic format example**, not observed market data or a recommended security:

```json
{
  "schema_version": 1,
  "provider": "Your licensed provider",
  "source_url": "https://example.org/original-prices",
  "license": "Describe the actual permission and restrictions",
  "license_url": "https://example.org/actual-provider-terms",
  "retrieved_at": "2025-02-04T20:00:00Z",
  "instruments": [{
    "entity": "MYISSUER",
    "symbol": "EXAMPLE.LON",
    "exchange": "XLON",
    "currency": "GBP",
    "timezone": "Europe/London",
    "price_type": "close",
    "adjustment": {"splits": "none", "dividends": "none"},
    "rows": [
      {"date": "2025-01-31", "value": 10,
       "available_at": "2025-01-31T17:00:00Z",
       "availability_basis": "Replace with the actual evidence for availability"},
      {"date": "2025-02-03", "value": 11,
       "available_at": "2025-02-03T17:00:00Z",
       "availability_basis": "Replace with the actual evidence for availability"}
    ]
  }]
}
```

An optional `cik` must be ten digits. There must be one series per entity and price type, so multiple share classes need explicit distinct entity identifiers. Values are positive finite numbers in base currency per share; pence must be converted to GBP before import and the conversion documented by the importer. No automatic currency or share-class conversion is made. `date` is the local trading-session date. Rows need not be consecutive calendar days; weekends/holidays are not substituted, and a missing weekday is not assumed to be an exchange holiday. Both exact endpoints must exist and be available at the cutoff.

Split/dividend definitions must each be `none`, `adjusted`, or `unknown`. An adjusted series also needs `adjustment.as_of` (a precise timezone-aware timestamp). Every row's `available_at` must be at least this vintage, preventing a present-day back-adjusted history from being claimed as available before a future split/dividend. Availability cannot precede the session's local date or exceed `retrieved_at`. `availability_basis` records why a timestamp is credible; validation cannot authenticate an importer's assertion. Optional row fields `split_coefficient` and `dividend` retain provider events. Source URLs may not contain credentials.

## Optional live adapter

```python
from verifin.market import AlphaVantageDailyAdapter
adapter = AlphaVantageDailyAdapter(
    api_key=caller_supplied_key,
    instruments=[{
        "entity": "MYISSUER", "symbol": "PROVIDER_SYMBOL",
        "exchange": "XNAS", "currency": "USD",
        "timezone": "America/New_York"
    }],
    allow_network=False, allow_premium=False, outputsize="compact")
tools = ToolRegistry(store, replay_dir, cache_dir=cache_dir,
                     market_adapter=adapter, allow_network=False)
```

The CLI can load a configuration JSON containing `instruments`, `outputsize`, and `allow_premium`, then construct this adapter. Keep `api_key` out of the file; pass only the explicitly supplied environment value. Both registry and adapter network flags must be enabled for a request. No SEC contact identity is needed for this separate provider; none is fabricated or reused.

The research CLI accepts `--market-data /path/to/validated-prices.json` for imports and `--market-config /path/to/provider-config.json` for adapter configuration. The provider key comes only from `ALPHAVANTAGE_API_KEY`; CLI configuration rejects embedded key fields. `ask`, `evaluate` and `serve` expose the provider option; the matched planning experiment accepts frozen `--market-data` and does not permit a mutable live adapter. An import remains the selected local source in `auto` mode; `source=live` bypasses the import and explicitly refreshes the configured provider.

The adapter uses the official `TIME_SERIES_DAILY` endpoint for raw close. Compact output is the default. Full history or `TIME_SERIES_DAILY_ADJUSTED` requires explicit `allow_premium=True`; provider entitlement is still checked by the provider. No account, subscription, live prices or paid entitlement was exercised in implementation tests. The interface and its restrictions were checked against the [official daily-data documentation](https://www.alphavantage.co/documentation/) on 2026-10-15. Data remain subject to the [provider's terms](https://www.alphavantage.co/terms_of_service/); no redistribution right is inferred.

Each action makes at most one HTTPS request to a fixed provider endpoint. There are no redirects, hidden retries or automatic alternate sources. Quota, entitlement, malformed bodies, unknown identities and missing credentials return explicit failures. The key is only attached to the outbound request; public provenance URLs and the durable index exclude it. Error bodies that may echo secrets are not persisted. Successful raw responses are hash-addressed in `cache_dir`; reopening a registry can reuse them. Explicit `source=live` always requests a refresh, whereas cached data remain labelled `cached_live`.

The live adapter conservatively uses the response's actual observation time for every row's availability. It does not backdate a current response into a historical information set, particularly for adjusted prices. Thus an old cutoff may reject a newly fetched historical daily series. A licensed point-in-time import with credible availability metadata is needed for a stricter historical backtest.

## Legacy bundled prices and calculations

The two inherited Yahoo chart snapshots remain a finite replay convenience. They now expose the provider's listing timezone and local session dates. Their split basis and redistribution permission are unverified, clearly marked in evidence; their old date-only availability assumption remains explicitly labelled. They are not a general live Yahoo API integration.

`price_return` computes `(end/start - 1)*100` on compatible saved operands. A raw-price change can be distorted by a split; a dividend-adjusted close incorporates the provider's adjustment convention. Neither is automatically a fully specified investor total return, causal event effect, or investment recommendation. Price-type, source, listing and adjustment basis must agree; mixed currencies/listings/vintages are refused.

Tests in `tests/test_market.py` use synthetic format fixtures and mocked HTTP solely to verify the adapter and failure contracts. They are not evidence of live provider access or market-data accuracy.
