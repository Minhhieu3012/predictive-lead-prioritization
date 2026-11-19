# ML Pre-t0 Feature Store — implementation decisions for leader review

**Scope:** `[ML] Xây dựng pre_t0_store.py`  
**State:** Implementation choices for review; existing US-01 and US-04 rules take
precedence. This note does not amend those approved project rules.

## Existing rules followed

- US-01 requires every scoring input's `feature_event_time` and
  `feature_available_at` to be at or before `t0_time`. Equality is accepted.
- US-04 excludes personal names, phone/email, sensitive demographics, and all
  free-text notes from scoring. Only structured business data may be used.
- The ingestion schema already carries `account_key`, point-in-time timestamps,
  structured enquiry fields, and source-supplied account age/purchase-count
  fields. `clean_enquiries` is the deduplicated source.

## Decisions made for this implementation

1. **Point-in-time enforcement:** A record is eligible for the feature table
   only when `t0_time`, `feature_event_time`, and `feature_available_at` parse
   as timestamps and both feature timestamps are `<= t0_time`. Invalid or
   post-t0 timestamp records are not materialized as features; a lineage row
   records a fixed rejection code. Supplied account age and prior-purchase
   counts must be non-negative integers when present. The current source has
   one event/availability timestamp pair per enquiry, so that pair gates the
   entire allowlisted row.
2. **Identifier handling:** `account_key` is read only for in-memory historical
   joins. It is not stored in `pre_t0_features` or feature lineage, returned by
   the builder, or printed in logs. `enquiry_id` remains only as the row key for
   joining features to their outcome target; it is not a model feature.
   Existing ingestion and deduplication continue retaining `account_key`
   because their current behavior depends on it.
3. **Free-text and PII:** The feature builder selects a fixed source-column
   allowlist and never reads outcomes, quarantine files, `*_DO_NOT_USE`, or
   free-text fields.
4. **Account aggregates:** `prior_enquiry_count` and
   `prior_distinct_product_family_count` use earlier enquiries for the same
   transient account key only when the earlier enquiry's `t0_time` is before
   the target `t0_time` and its feature event/availability timestamps are no
   later than the target `t0_time`. All eligible history in `clean_enquiries`
   is used; no additional lookback window is invented.
5. **New versus unobserved accounts:** With no complete account-history
   coverage field, no prior enquiry does not prove that the account is new.
   The store reports `NO_PRIOR_ENQUIRY_OBSERVED` and null aggregates instead
   of fabricating zero history or a `NEW` label. Missing account key is
   `UNKNOWN`.
6. **Purchase history:** The source has a `prior_purchase_count_at_t0` field,
   which is retained as a supplied structured feature if present. The current
   schema has no transaction table to independently recompute verified
   purchases; prior enquiries and qualification outcomes are not treated as
   purchases.
7. **Feature cap and storage:** The model feature allowlist contains 13
   columns, below the 30-column maximum. `enquiry_id`, feature version, and
   build timestamp are metadata, not model inputs. Feature values and
   point-in-time lineage are persisted in separate SQLite tables.
8. **Refresh behavior:** Each build replaces the two generated feature-store
   tables atomically from `clean_enquiries`; it does not modify source tables.

## Review questions / constraints

- Confirm whether the 30-column limit applies only to model inputs or also to
  identifier/lineage metadata.
- Confirm whether the retained source `prior_purchase_count_at_t0` is trusted
  for production. The checked-in synthetic generator assigns this value
  randomly, so the generated data is not evidence of production validity.
- If the product requires a true `NEW` account classification, supply an
  authoritative account creation date or complete account-history coverage
  marker. Until then `NO_PRIOR_ENQUIRY_OBSERVED` is deliberately not called
  `NEW`.
- If verified past purchases are required as aggregate features, provide an
  authorized transaction source and its event/availability timestamps.
- The current US-04 text permits an anonymized account key for structured
  processing, while this feature store omits it from persistence. If policy
  intends removal from the entire ingestion/deduplication database, that is a
  separate change because current deduplication groups by `account_key`.

## Implementation boundary

This store prepares features only. It does not train a model, read or transform
target labels, change the Rules Baseline, or infer labels from `Unknown` /
`IMMATURE` outcomes.

Run `python -m src.ml.pre_t0_store` after validated ingestion and
deduplication. The command refreshes `pre_t0_features` and
`pre_t0_feature_lineage` in the configured SQLite database; tests use temporary
databases.
