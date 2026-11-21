# PLPT-57: Gate G1 Implementation Decisions

## Status

- **PROVISIONAL — implemented to unblock development**
- **REQUIRES LEAD REVIEW — confirm before production acceptance**
- **IMPLEMENTED — verified in code and tests**

This document records engineering decisions made provisionally. It is not evidence that Gate G1 is formally approved for production.

## 1. Task Background and Business Objective

Gate G1 determines whether `PREDICTIVE_ML` may be activated instead of the existing deterministic `RULES_BASELINE`. Activation is intended to require statistically supported predictive performance improvement, without changing unrelated queue-allocation behavior.

This gate measures predictive performance only. It does not establish that ML causes better business outcomes.

## 2. Original Acceptance Criteria

The original requirements prohibit ML activation unless:

1. Precision@K lift is at least `1.20x`.
2. The lower bound of the 95% confidence interval is greater than `0`.

The project also describes Precision@K lift of at least `1.20x` over the rules baseline.

**PROVISIONAL — REQUIRES LEAD REVIEW:** The phrase “CI lower bound > 0” is ambiguous unless the metric scale is identified. This implementation interprets the CI as a confidence interval for relative lift.

## 3. Architecture Findings

- `src/rules/rules_engine.py` implements the deterministic rules baseline and sets `RULES_BASELINE`.
- `src/queue_allocation.py` accepts both supported scoring modes but previously had no gate enforcement.
- `src/api/main.py` currently produces baseline scores and does not contain a model-serving branch.
- `src/ml/pre_t0_store.py` provides leakage-safe pre-t0 features.
- `src/models/explainer.py` provides ML factor explanations and a non-causal disclaimer.
- No existing production Precision@K, lift, bootstrap, or model-activation registry implementation was found.

## 4. Selected Statistical Interpretation

**PROVISIONAL — REQUIRES LEAD REVIEW:** Use relative lift:

```text
P_ml = Precision@K for PREDICTIVE_ML
P_baseline = Precision@K for RULES_BASELINE
lift = P_ml / P_baseline
neutral lift = 1.0x
```

The provisional unlock condition is:

```text
lift >= 1.20
and ci_95_lift_lower_bound > 1.0
```

If the Lead intends absolute Precision@K difference instead, the neutral threshold is `0` and the implementation contract must change explicitly. The implementation does not mix the two interpretations.

## 5. Relative Lift Formula

```text
lift = precision_at_k_ml / precision_at_k_baseline
```

The baseline precision must be finite and greater than zero. NaN, infinity, invalid range values, and invalid CI values are failures.

## 6. Paired Bootstrap Method

**IMPLEMENTED:** `src/ml/gate_g1_decider.py` contains `paired_bootstrap_lift_ci()`.

The paired bootstrap implementation:

1. Accepts paired ML and baseline binary relevance indicators for exactly `K` positions.
2. Resamples the same row indices for both systems.
3. Recalculates paired Precision@K:

```text
P_ml* = positives_ml* / K
P_baseline* = positives_baseline* / K
lift* = P_ml* / P_baseline*
```

4. Uses fixed-seed Python `random` sampling for deterministic percentile bounds.
5. Uses the 2.5th and 97.5th percentile of successful relative-lift resamples.
6. Discards resamples with zero baseline Precision@K.
7. Rejects degenerate output rather than inventing a CI.

This implementation is useful for paired top-K relevance indicators. It does not calculate rankings from raw model scores and does not replace a full independent-test-set evaluation pipeline.

## 7. Minimum Sample Size

**PROVISIONAL — REQUIRES LEAD REVIEW:** The implementation uses `200` paired evaluation samples as the default minimum. The value is configurable through `minimum_sample_size`.

Two hundred samples are an engineering threshold based on existing project documentation, not a statistically proven guarantee of adequate power or interval precision.

## 8. Invalid and Zero-Baseline Handling

**IMPLEMENTED:**

- Baseline precision exactly zero returns `ZERO_BASELINE_PRECISION`.
- Missing, NaN, infinite, or out-of-range precision values fail.
- Invalid `K` or insufficient sample size fail.
- Missing or reversed CI bounds fail.
- Different ML/baseline population IDs fail.
- Missing population provenance fails.
- Unsupported CI method fails.
- Paired bootstrap degenerate resamples are rejected.
- A failed gate always selects `RULES_BASELINE`.

## 9. Gate PASS/FAIL Behavior

**IMPLEMENTED:** `evaluate_gate_g1()` returns:

- `gate_status = "PASS"` and `selected_strategy = "PREDICTIVE_ML"` only when both criteria pass.
- `gate_status = "FAIL"` and `selected_strategy = "RULES_BASELINE"` for every invalid or failed evaluation.

The fallback reason identifies the unmet evidence condition. It does not state that ML is proven ineffective.

## 10. Non-Causal Warning Policy

**IMPLEMENTED:** Every fallback result includes:

```text
NON_CAUSAL_WARNING: This evaluation measures predictive performance only. The observed Precision@K improvement does not establish causal impact on business outcomes.
```

Allocation rejects or rewrites unauthorized `PREDICTIVE_ML` items to `RULES_BASELINE` and attaches the warning.

## 11. Integration Points

**IMPLEMENTED:**

- `src/ml/gate_g1_decider.py` is the centralized decision component.
- `src/queue_allocation.py` accepts an optional gate decision and prevents PML activation unless it passes.
- `src/api/main.py` is unchanged because it has no real ML evaluation or activation contract; it continues to use the rules baseline.
- `tests/test_gate_g1_decider.py` covers positive, negative, invalid, bootstrap, and allocation integration cases.
- CF-24 now validates CI/lift in addition to lift.

No model registry or production model serving flow is fabricated.

## 12. Technical Assumptions and Unresolved Questions

- **REQUIRES LEAD REVIEW:** Confirm that relative lift is the intended CI scale.
- **REQUIRES LEAD REVIEW:** Confirm whether `1.0x` is the neutral CI threshold.
- **REQUIRES LEAD REVIEW:** Confirm `200` as the minimum sample size.
- Confirm the required independent test-set population and exact model/baseline population identifiers.
- Confirm bootstrap sample count and percentile convention for final policy.
- The real model evaluation pipeline must provide paired top-K relevance data or produce a provenance-backed CI from the same population.
- The current manifest approval status is not yet automatically consumed by a production API activation path.

## 13. Risks and Limitations

- Relative lift can become unstable or undefined when baseline Precision@K is close to zero.
- Small or correlated samples may yield misleading bootstrap intervals.
- Paired bootstrap does not establish causality.
- The existing project has no real ML ranking/serving pipeline, so this gate is currently a component and allocation guard, not a complete model deployment workflow.
- A CI supplied by a caller is accepted only when metadata identifies the paired-bootstrap percentile method and common population.
- External callers must ensure their supplied CI is independently and correctly calculated.

## 14. Lead Review Checklist

- [ ] Confirm relative lift versus absolute Precision@K difference.
- [ ] Confirm neutral threshold `1.0x` for relative lift.
- [ ] Confirm minimum lift `1.20x`.
- [ ] Confirm 200-sample provisional threshold.
- [ ] Confirm sample-population provenance requirements.
- [ ] Confirm bootstrap K/relevance-indicator semantics.
- [ ] Confirm production model-evaluation pipeline supplies valid paired evidence.
- [ ] Confirm manifest approval and gate decision become part of the deployed activation process.
- [ ] Confirm the non-causal warning text and warning observability.
