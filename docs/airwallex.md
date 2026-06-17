# crude-airwallex

CRUD-style CLI over the Airwallex REST API, under the `crude-airwallex <resource> <verb>` grammar.

## Configuration

`~/.config/crude/config.toml`:

```toml
[airwallex]
client_id = "..."
api_key = "..."
# environment = "demo"   # optional; "demo"/"sandbox" -> api-demo.airwallex.com, else production
# on_behalf_of = "..."   # optional; connected-account id for platform (Scale) accounts
```

Named accounts use `[airwallex.<name>]`; select with `--account/-a <name>` or `$CRUDE_ACCOUNT`.

## Authentication

`POST /api/v1/authentication/login` with headers `x-client-id` and `x-api-key` (empty body)
returns a bearer `token` and `expires_at`. There is no OAuth consent, no redirect, and no refresh
token; the bearer lasts ~30 minutes and crude re-logs-in when it expires. The bearer is cached in
`$XDG_STATE_HOME/crude/airwallex_token[_<account>].json` (mode 0600); losing it costs one re-login.
A platform account acting on a connected account sends `x-on-behalf-of`.

## Verified API behaviour (observed live, 2026-06-17)

These were confirmed against a live production account; treat them as ground truth for new modules.

- **Auth:** the login model above is correct; `expires_at` is an ISO-8601 UTC instant.
- **List envelope:** page endpoints return `{"items":[...], "has_more": bool}` with `page_num`
  (0-based) + `page_size`. `balances/current` returns a bare list.
- **Field-name casing is NOT uniform across endpoints.** Verify each endpoint's real keys live
  rather than assuming a convention:
  - `balances/current` is snake_case: `currency`, `total_amount`, `available_amount`,
    `pending_amount`, `reserved_amount`, `prepayment_amount`, `account_type`.
  - `balances/history` is snake_case: `currency`, `amount`, `balance`, `source_type`, `source`,
    `description`, `fee`, `posted_at`, `id`, `account_type`.
  - `account` is snake_case: `id`, `nickname`, `status`, `created_at`, `account_details`, ...
  - `financial_transactions` is **camelCase**: `id`, `currency`, `amount`, `net`, `fee`, `status`,
    `transactionType`, `sourceType`, `createdAt`, `settledAt`, `estimatedSettledAt`, `currencyPair`,
    `clientRate`, `sourceId`, `fundingSourceId`, `batchId`, `description`.
- **Timestamps** are ISO-8601 UTC with milliseconds and a `+00:00` offset, e.g.
  `2026-06-17T07:53:44.068+00:00`. crude renders all timestamps in the machine's local timezone
  (`crude_common.localtime`) and reads typed `--from/--to` as local days converted to UTC.
- **Date filter:** `financial_transactions` filters on `from_created_at` / `to_created_at`
  (snake_case query params, ISO-8601 UTC); confirmed filtering (a 2020 window returned 0, a recent
  window returned the page cap). The `--to` bound is the exclusive next-local-midnight, so a
  half-open `[from, to)` range covers the whole to-date regardless of zone.

## Command surface

Core treasury reads (Step 1, shipped):

    crude-airwallex login                                   # confirm credentials, report token expiry
    crude-airwallex account get
    crude-airwallex balance current
    crude-airwallex balance history [--currency] [--from] [--to] [--limit]
    crude-airwallex transaction list [--currency] [--status] [--from] [--to] [--all] [--limit]
    crude-airwallex transaction get <id>

Add `--json` to any read for the raw API object. `--from`/`--to` take `YYYY-MM-DD` local dates.

Payouts, Payments Acceptance, and Issuing are added in later modules.
