# settle-agent — Lean 4 verification notes

**Artifact:** `Settle.lean` (938 lines, 50 theorems) — a self-contained
Lean 4.34.1 model, **core library only** (no Mathlib, no imports beyond the
prelude). No `sorry` / `admit` / custom axioms; `#print axioms` on the
headline theorems shows only `propext`, `Classical.choice`, `Quot.sound`.

**Compile:** `~/.elan/bin/lean Settle.lean` → exit 0 (a few deprecation /
unused-variable warnings only).

## What the code actually is (model follows the code, not the pitch)

settle-agent is a Python medical-billing collections agent. The ledger is
**read-only**; "settlement" is not a funds transfer — it is a verified
patient accepting a policy-issued payment offer (pay-in-full or
installments), which mints a hosted payment link and sends a confirmation
email. Money moves off-platform. The model therefore proves properties of
the *enrollment* state machine, the *policy engine's arithmetic*, the
*ledger balance computation*, and the *audit chain* — there is no
on-platform money movement to conserve, and the model says so explicitly
(Part 5b).

Money is modeled in **integer cents** (`Int`). The code uses `Decimal`
quantized to `CENT` (`settle/ledger.py` l. 29), so integer cents are an
exact model of every quantity except where noted in Finding 6.

## Theorem → source mapping

### Part 1 — Identity verification & lockout (`settle/tools.py`)

Model: `VState` (verified / attempts / locked), `verifyOk`, `verifyBad`,
`guarded`. Sources: `_need_verified` ll. 60–67 (locked checked first, then
verified), `verify_identity` ll. 74–95 (match sets `verified`; mismatch
increments attempts; at 3 the conversation locks and a staff ticket opens).

| Theorem | Property |
|---|---|
| `guarded_iff` | The guard used by every gated tool is exactly `verified ∧ ¬locked`. |
| `verifyOk_spec`, `verifyBad_spec` | Exact transition semantics of a match / mismatch. |
| `verifyBad_preserves_verified` | A failed attempt never un-verifies a session. |
| `locked_absorbing` | Once locked, neither verify step can fire. |
| `reachV_invariant` | In every reachable state: `attempts ≤ 3 ∧ (locked ↔ attempts = 3)`. |
| `success_requires_attempts_lt_three` | A successful verify requires attempts < 3 beforehand. |
| `reachV_locked_no_step` | A locked reachable state is a dead end for verification. |

### Part 2 — Policy engine (`settle/policy.py`)

Model: integer-cent versions of `Policy` (ll. 19–27: 5% prompt-pay discount,
≤ 4 installments, ≥ $25.00/installment, $100.00 small-balance threshold →
cap 2), `split` (ll. 43–49), `plan_options` (ll. 56–93).

| Theorem | Property |
|---|---|
| `capFor_bounds` | Installment cap is always 2 or 4. |
| `discountC_nonneg`, `discountC_le` | Discount is in `[0, balance]` (ROUND_HALF_UP at l. 67, modeled exactly). |
| `splitCents_sum` | **Installments sum to the balance exactly** — remainder lands on the last payment (l. 49). |
| `splitCents_ge_base`, `splitCents_base_mem` | Every installment ≥ the floor share; the floor share is attained. |
| `optionsFor_inst_spec` | Exact characterization of issued installment offers: `n ∈ {2, cap}`, amounts are `split balance n`, every amount ≥ $25.00. |
| `optionsFor_inst_sum` | Every issued installment plan totals **exactly** the balance it was issued against. |
| `optionsFor_inst_n_le` | No plan exceeds the cap. |
| `optionsFor_inst_amounts_min` | Every installment of an issued plan is ≥ $25.00 (the l. 84 filter is sound). |
| `optionsFor_payfull_spec`, `payfull_amount_le` | Pay-in-full is always offered, at `balance − discount`, never above the balance. |
| `neededC_mul_ge`, `withinPolicy_needed_le` | The `requested_monthly` path (ll. 86–93): `needed = ceil(bal/req)` really covers the balance, and `within_policy` implies `needed ≤ cap`. |

### Part 3 — Offers & acceptance (`settle/tools.py` ll. 143–176, `settle/router.py`)

Model: `issueS` (get_payment_options, offers replaced wholesale at l. 147),
`acceptS` (accept_offer, ll. 151–176: gates = verified ∧ offer ∈ current
offers ∧ AFFIRMATIVE matches the last inbound message, l. 27/l. 161).
`EState.ever` tracks every offer ever issued — history the proof needs and
the code does not keep.

| Theorem | Property |
|---|---|
| `issueS_spec` | Issuance fires only when verified; replaces offers; history accumulates. |
| `acceptS_eq` / `acceptS_spec` | Acceptance fires only with all three gates, and then records exactly the issued offer's snapshot (id + amounts). |
| `acceptS_none_of_not_guarded` / `_of_not_aff` / `_of_unknown` | Each gate individually blocks acceptance; a blocked acceptance changes nothing (returns `none`, no plan, no email). |
| `acceptS_none_after_reissue` | Re-issuing offers retires old offer ids — they can no longer be accepted. |
| **`acceptS_repeatable`** | **At-most-once FAILS** (see Finding 1): after a successful acceptance, the same acceptance succeeds *again* on the resulting state — plan re-recorded, `emails = original + 2`. |
| `reachE_inv` | The invariant that *does* hold in every reachable state: every current offer was really issued; any recorded plan names an offer that was issued at some point in this conversation, was accepted with an affirmative message while verified; a plan implies ≥ 1 confirmation email. |

### Part 4 — Ledger accounting (`settle/ledger.py` ll. 38–53)

Model: `appliedSum` (intended per-encounter sum) vs `appliedDict` (the
code's fold — a dictionary comprehension at l. 41, last write wins).

| Theorem | Property |
|---|---|
| `appliedDict_eq_appliedSum_of_single` | **If** every encounter receives at most one payment, the code's applied amounts equal the true sums — balances correct, nothing double-counted, nothing dropped. The hypothesis is never checked in the code. |
| **`appliedDict_counterexample`** | Without that hypothesis it breaks, by computation: payments of $10.00 + $20.00 to one encounter → code reports **$20.00** applied; truth is **$30.00** (Finding 4). |
| `countEnc_eq_zero`, `appliedDictFrom_no_match`, `appliedSum_no_match` | Supporting lemmas. |

### Part 5 — Audit chain (`settle/audit.py` ll. 31–53) & conservation

Model: digest abstracted as uninterpreted `H (seq, prev, contents)`;
`buildFrom` replays `record` (seq = position, prev = last hash or GENESIS
`"0"*64`, l. 14/l. 40); `verifyFrom` replays `verify` (ll. 46–53).

| Theorem | Property |
|---|---|
| `verifyFrom_buildFrom`, `build_verify` | Chains built by `record` always verify. |
| `verifyFrom_sound` | **Soundness / tamper-evidence:** anything `verify` accepts is genuinely linked (seq, back-pointer, hash all check out) — altering any entry breaks verification. |
| `Linked.verify` | Completeness: a genuinely linked chain always verifies. |
| `build_linked` | Built chains are linked. |
| `liftStep_ledger_preserved` (Part 5b) | **Conservation across the settlement boundary:** every conversation step (issue / accept / verify, success or failure) leaves the ledger view — hence every balance derived from it — bit-for-bit unchanged, matching README l. 56 ("The agent never moves money or edits the ledger"). Combined with `optionsFor_inst_sum`, a plan's total never exceeds the obligation it was issued against. |

## Findings — discrepancies & risks (code followed over docs)

1. **No idempotency on `accept_offer` — at-most-once enrollment FAILS.**
   `accept_offer` (tools.py ll. 151–176) never checks `state.plan`. A
   second affirmative message re-runs the whole effect chain: plan
   overwritten (l. 168), a second payment link minted, a second
   confirmation email sent. Proved as `acceptS_repeatable`. The hosted
   link token is a deterministic HMAC over `patient|offer|amount`
   (router.py), so the *same* link is re-issued — the duplicate is
   invisible downstream except for the extra email and audit entries.

2. **Partial-failure ordering inside acceptance.** The effect order is
   payment-link → set `state.plan` → send email. If the email send
   throws, the plan is already recorded but the patient was never
   confirmed, and no "enrolled" audit entry distinguishes the state.
   There is no compensating rollback; a retry hits Finding 1.

3. **Verify attempts never reset on success** (tools.py ll. 74–95).
   `verify_attempts` persists for the conversation's lifetime
   (Dynamo TTL 30 days, `settle/state.py`). Two early typos mean one
   later typo locks the patient out even after a successful session —
   the model makes this visible: `locked ↔ attempts = 3` is a
   *lifetime* invariant (`reachV_invariant`), not a per-session one.

4. **Duplicate applied payments vanish from balance math**
   (ledger.py l. 41). The `applied` dict comprehension keys by
   encounter, so a second payment to the same encounter overwrites
   the first; the dropped payment is excluded from the owed balance
   entirely (it is not in `unapplied` either — l. 46 only catches
   empty `applied_to`). Balances shown to patients — and the balances
   offers are built from — are overstated by the dropped amount.
   Proved: `appliedDict_counterexample`; correctness holds only under
   an at-most-one-payment-per-encounter hypothesis the code never
   checks (`appliedDict_eq_appliedSum_of_single`).

5. **No positive-balance guard in `plan_options`** (policy.py ll. 56–93).
   Pay-in-full is offered unconditionally, so a zero or negative
   balance (overpaid account) yields a zero/negative "pay in full"
   offer that `accept_offer` will happily enroll. (In the model,
   `bal_nonneg_of_inst` shows installment offers at least force the
   balance argument non-negative — pay-in-full has no such floor.)

6. **`requested_monthly` is a float in a Decimal pipeline**
   (tools.py l. 143: `requested_monthly: float | None`). Everywhere
   else money is `Decimal` quantized to cents (ledger.py l. 29). The
   float feeds `needed = ceil(bal / req)` in policy.py, so the
   installment count for a requested monthly amount can be off by one
   at float-representation boundaries. The model uses exact integer
   ceiling division (`neededC_mul_ge` proves the *intended* semantics).

7. **Offer ids are 40-bit truncated, deterministic, and never expire**
   (policy.py ll. 51–53: `"OFR-" + sha256(...)[:10]`). Acceptance
   (tools.py l. 154) checks only that the id is in the *current* offer
   dict — never that the balance is unchanged since issuance. A stale
   snapshot is retired only when `get_payment_options` happens to be
   called again (`acceptS_none_after_reissue` proves retirement
   works *when* re-issuance occurs); nothing forces re-issuance
   before acceptance.

8. **Consent pattern is broad** (tools.py l. 27): `AFFIRMATIVE`
   accepts bare `1`, `2`, `3`, `ok`, `sí` as enrollment consent for
   whichever offer id the agent supplies in the same turn. Combined
   with Finding 1, a stray "ok" in a later turn can re-enroll.

9. **`escalate_to_staff` is not verification-gated** (tools.py
   ll. 196–207) — unlike the payment tools it never calls
   `_need_verified`, so an unverified (or locked-out) party can open
   staff tickets with arbitrary summaries.

10. **Audit `record` is read-then-append** (audit.py ll. 31–44): seq
    and prev are computed from the loaded chain, so two concurrent
    records for one conversation can fork the chain. The Dynamo sink
    mitigates with a conditional put (`attribute_not_exists(seq)`);
    the in-memory sink does not. The Lean model proves chain
    soundness/completeness for a single sequential writer only.

Minor: `split` with `n = 0` would divide by zero, but it is
unreachable from `plan_options` (candidates are ≥ 2); the model's
`splitCents` inherits the same convention. Test coverage is thin:
only `tests/test_guardrails.py` (12 tests) exercises any of this.

## Modeling choices & limits

- `aff` (the AFFIRMATIVE regex) is an abstract `String → Bool`
  parameter — the model proves gate behavior for *any* such pattern.
- The digest `H` is uninterpreted: audit theorems hold for any hash
  function, so they capture linkage, not collision resistance.
- Amounts are integer cents; Decimal `money()` quantization
  (ROUND_HALF_UP at the discount, ledger.py l. 29 / policy.py l. 67)
  is modeled exactly; the float path of Finding 6 is modeled by its
  intended exact semantics, not by float arithmetic.
- Channel/router identity mapping and Dynamo persistence are out of
  scope except where noted above.
