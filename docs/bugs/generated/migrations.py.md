## Summary
`[api-misuse]` `apply_pending_migrations()` can roll back unrelated caller work if invoked while a transaction is already open.

## Severity
- Severity: major
- Priority: P1

## Evidence
- `src/filigree/migrations.py:218` starts a hard `BEGIN IMMEDIATE` unconditionally.
- `src/filigree/migrations.py:230` unconditionally calls `conn.rollback()` on any exception.
- If the caller already has an active transaction, `BEGIN IMMEDIATE` raises (`cannot start a transaction within a transaction`), and the rollback at line 230 rolls back the caller’s pending writes too.

## Root Cause Hypothesis
The function assumes it always owns transaction boundaries, but it never checks `conn.in_transaction` before opening/rolling back a migration transaction.

## Suggested Fix
Add a precondition at function entry (or before each step) to reject active transactions, e.g. raise `ValueError` if `conn.in_transaction` is true, and only rollback when this function actually started the transaction (track with a flag).

---
## Summary
`[api-misuse]` Migration runner does not restore the connection’s original `PRAGMA foreign_keys` state; it always forces `ON`.

## Severity
- Severity: minor
- Priority: P2

## Evidence
- `src/filigree/migrations.py:217` sets `PRAGMA foreign_keys=OFF` before migration.
- `src/filigree/migrations.py:235` always sets `PRAGMA foreign_keys=ON` in `finally`.
- There is no capture/restore of prior FK mode, so callers that intentionally had FK checks disabled get silently changed behavior after migration.

## Root Cause Hypothesis
The runner treats FK enforcement as a fixed post-condition, not caller-owned connection state.

## Suggested Fix
Capture initial FK mode once (`PRAGMA foreign_keys`) and restore that exact value in `finally` instead of hard-coding `ON`.