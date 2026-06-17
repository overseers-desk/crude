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
- **Payouts group (verified live):** all snake_case. Paths: `GET /api/v1/beneficiaries` (+ `/{id}`),
  writes `POST /api/v1/beneficiaries/create|update/{id}|delete/{id}`; `GET /api/v1/transfers`
  (+ `/{id}`), `POST /api/v1/transfers/create`; `GET /api/v1/fx/rates/current`,
  `GET /api/v1/fx/conversions` (+ `/{id}`), `POST /api/v1/fx/conversions/create`. A beneficiary's
  bank fields are nested under `beneficiary.bank_details` (`account_name`, `account_currency`,
  `bank_country_code`, ...); a conversion uses `conversion_id`, `buy/sell_currency`, `buy/sell_amount`,
  `client_rate`, `status`, `created_at`, `settlement_cutoff_at`.
- **FX endpoints are date-versioned.** `GET /fx/rates/current` and the `/fx/conversions` endpoints
  return `400 incorrect_version` unless an `x-api-version` header is sent; `2024-06-30` works
  (`FxAPI` sends it on every call). The other groups answer on the account's default version.
- **Write convention:** money/data writes are `POST .../create|update/{id}|delete/{id}` (not
  PUT/DELETE). A transfer or conversion body needs an idempotency `request_id`; crude fills a uuid4
  when the caller omits one (a caller wanting retry-idempotency supplies their own).
- **Payments Acceptance group (verified live, enabled on this account):** all snake_case, under
  `/api/v1/pa/`: `payment_intents` (+ `/{id}`, writes `/create`, `/{id}/confirm|capture|cancel`),
  `refunds`, `customers`, `payment_consents` (read-only), `payment_links`. No version header needed
  (unlike FX). A payment-intent uses `id`, `amount`, `currency`, `merchant_order_id`, `status`,
  `captured_amount`, `created_at`. A reusable payment link carries `default_currency` rather than a
  fixed `currency`. payment-intent/refund/payment-link create take an idempotency `request_id`.

## Command surface

Core treasury reads (read-only):

    crude-airwallex login                                   # confirm credentials, report token expiry
    crude-airwallex account get
    crude-airwallex balance current
    crude-airwallex balance history [--currency] [--from] [--to] [--limit]
    crude-airwallex transaction list [--currency] [--status] [--from] [--to] [--all] [--limit]
    crude-airwallex transaction get <id>

Payouts (reads, plus confirm-gated money/data writes):

    crude-airwallex beneficiary list [--entity-type] [--from] [--to] [--all] [--limit]
    crude-airwallex beneficiary get <id>
    crude-airwallex beneficiary create (--data | -f | stdin) [--yes]
    crude-airwallex beneficiary update <id> (--data | -f | stdin) [--yes]
    crude-airwallex beneficiary delete <id> [--yes]
    crude-airwallex transfer list [--status] [--from] [--to] [--all] [--limit]
    crude-airwallex transfer get <id>
    crude-airwallex transfer create (--data | -f | stdin) [--yes]     # MOVES REAL MONEY
    crude-airwallex fx-rate current --buy <ccy> --sell <ccy> [--amount]
    crude-airwallex conversion list [--from] [--to] [--all] [--limit]
    crude-airwallex conversion get <id>
    crude-airwallex conversion create (--data | -f | stdin) [--yes]   # MOVES REAL MONEY

Payments Acceptance (the `pa` group; reads, plus confirm-gated money writes):

    crude-airwallex pa payment-intent list [--status] [--from] [--to] [--all] [--limit]
    crude-airwallex pa payment-intent get <id>
    crude-airwallex pa payment-intent create (--data | -f | stdin) [--yes]            # REQUESTS REAL MONEY
    crude-airwallex pa payment-intent confirm|capture|cancel <id> (--data|-f|stdin) [--yes]
    crude-airwallex pa refund list/get ; pa refund create (--data | -f | stdin) [--yes]   # MOVES REAL MONEY
    crude-airwallex pa customer list/get/create/update/delete
    crude-airwallex pa payment-consent list/get
    crude-airwallex pa payment-link list/get ; pa payment-link create (--data|-f|stdin) [--yes]

Add `--json` to any read for the raw API object. `--from`/`--to` take `YYYY-MM-DD` local dates.
Money-moving verbs (`transfer create`, `conversion create`, `pa payment-intent create/confirm/capture`,
`pa refund create`, `pa payment-link create`) prompt unless `--yes`.

Issuing is added in a later module.
