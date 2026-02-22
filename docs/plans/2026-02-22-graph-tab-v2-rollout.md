# Graph Tab v2 Rollout Checklist (Draft)

Epic: `filigree-7cc838`

This is the early rollout/governance draft for `filigree-27022f`.
It will be finalized into release-readiness documentation under `filigree-eed3ee`.

## Rollout Controls

1. Feature flag: `FILIGREE_GRAPH_V2_ENABLED`
2. API compatibility switch: `FILIGREE_GRAPH_API_MODE=legacy|v2`
3. Request-level override: `/api/graph?mode=legacy|v2`

## Safety Gates

1. Compatibility gate:
   - legacy shape still served correctly when `mode=legacy`.
   - v2 shape verified by contract tests.
2. Performance gate:
   - backend query latency within budget for baseline dataset.
   - frontend render/update within budget and large-graph fallback visible.
3. Rollback gate:
   - toggling feature flag to off immediately restores legacy behavior.
   - toggling compatibility switch to `legacy` restores legacy API response.

## Operational Checklist

1. Pre-release
   - verify backend regression tests for graph modes pass
   - verify frontend baseline interaction tests pass
   - verify explicit fallback messaging appears on truncation
2. Canary
   - enable flag in one test environment
   - monitor query_ms + truncation rate + dashboard errors
   - collect usability walkthrough notes (blocker investigation task)
3. Broad rollout
   - set default mode to `v2` in non-critical environments first
   - keep legacy override enabled through stabilization window
4. Finalization
   - publish final runbook with outcomes and post-release validation summary

## Rollback Triggers

1. sustained graph render failures in dashboard.
2. significant performance regression beyond agreed p95 budgets.
3. critical incompatibility reported by existing `/api/graph` consumers.

## Rollback Steps

1. Set `FILIGREE_GRAPH_V2_ENABLED=0`.
2. Set `FILIGREE_GRAPH_API_MODE=legacy`.
3. Confirm `/api/graph` returns legacy payload and graph view functions.
4. Open incident follow-up issues for root cause and remediation.

