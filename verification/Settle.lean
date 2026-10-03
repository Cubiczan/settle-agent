/-
  Settle.lean — a Lean 4 (core library only) model of the settle-agent
  settlement flow: identity verification with lockout, the policy engine
  that issues payment offers, offer acceptance (enrollment), the ledger
  accounting behind `account_summary`, and the hash-chained audit log.

  The code is a medical-billing collections agent (settle/*.py).  The
  agent never writes to the practice ledger: "settlement" here means a
  patient accepting a policy-issued payment offer (pay in full at a
  prompt-pay discount, or installments), which mints a hosted payment
  link and a confirmation email.  Money itself moves off-platform.

  All money is modelled in integer cents.  The code uses `Decimal`
  quantized to cents throughout settle/ledger.py and settle/policy.py,
  so integer cents are an exact model of every amount except the
  `requested_monthly` float input (see NOTES.md).

  No `sorry` / `admit` / custom axioms.  Compile with:
      ~/.elan/bin/lean Settle.lean
-/

namespace Settle

/-! ## Part 1 — Identity verification and lockout

    Models settle/state.py `ConversationState` (ll. 17–37, fields
    `verified`, `verify_attempts`, `locked`) and settle/tools.py
    `verify_identity` (ll. 74–95) plus the `_need_verified` guard
    (ll. 60–67) that every account tool runs first:

    * a locked conversation rejects verification outright;
    * a DOB match sets `verified` (attempts are NOT reset);
    * a mismatch increments `verify_attempts`; reaching 3 locks the
      conversation and pages staff (the ticket effect is outside the
      state model);
    * every other tool requires `verified ∧ ¬locked`, checked in that
      order (locked first). -/

/-- The verification-relevant slice of `ConversationState`. -/
structure VState where
  verified : Bool
  attempts : Nat
  locked : Bool
deriving Repr, DecidableEq

/-- `verify_identity` with a correct DOB (tools.py ll. 77–85):
    blocked when locked, otherwise sets `verified`. -/
def verifyOk (s : VState) : Option VState :=
  if s.locked then none else some { s with verified := true }

/-- `verify_identity` with a wrong DOB (tools.py ll. 86–95):
    blocked when locked, otherwise increments attempts and locks at 3. -/
def verifyBad (s : VState) : Option VState :=
  if s.locked then none
  else some { s with attempts := s.attempts + 1, locked := 3 ≤ s.attempts + 1 }

/-- The `_need_verified` predicate (tools.py ll. 60–67): a tool body may
    run only in a verified, unlocked conversation. -/
def guarded (s : VState) : Bool := s.verified && !s.locked

theorem guarded_iff {s : VState} :
    guarded s = true ↔ s.verified = true ∧ s.locked = false := by
  simp [guarded, Bool.and_eq_true]

theorem verifyOk_spec {s s' : VState} (h : verifyOk s = some s') :
    s.locked = false ∧ s' = { s with verified := true } := by
  unfold verifyOk at h
  split at h
  · simp at h
  · rename_i hc
    refine ⟨?_, (Option.some.inj h).symm⟩
    cases h2 : s.locked with
    | false => rfl
    | true => exact (hc h2).elim

theorem verifyBad_spec {s s' : VState} (h : verifyBad s = some s') :
    s.locked = false ∧ s' = { s with attempts := s.attempts + 1, locked := 3 ≤ s.attempts + 1 } := by
  unfold verifyBad at h
  split at h
  · simp at h
  · rename_i hc
    refine ⟨?_, (Option.some.inj h).symm⟩
    cases h2 : s.locked with
    | false => rfl
    | true => exact (hc h2).elim

/-- A failed attempt never changes the verification flag: in particular
    a mismatch cannot verify anyone, and it cannot un-verify either. -/
theorem verifyBad_preserves_verified {s s' : VState}
    (h : verifyBad s = some s') : s'.verified = s.verified := by
  obtain ⟨_, rfl⟩ := verifyBad_spec h
  rfl

/-- Lockout is absorbing for the verification machine (tools.py
    ll. 77–79 and `_need_verified` ll. 61–63): from a locked state no
    verification transition exists at all. -/
theorem locked_absorbing {s : VState} (h : s.locked = true) :
    verifyOk s = none ∧ verifyBad s = none := by
  simp [verifyOk, verifyBad, h]

/-- Reachable verification states: from a fresh conversation, closing
    over the two verification transitions.  (Other tools do not mutate
    this slice of the state.) -/
inductive ReachV : VState → Prop where
  | init : ReachV ⟨false, 0, false⟩
  | ok : ReachV s → verifyOk s = some s' → ReachV s'
  | bad : ReachV s → verifyBad s = some s' → ReachV s'

/-- The lockout invariant: attempts never exceed 3, and the conversation
    is locked iff attempts have reached exactly 3. -/
def VInv (s : VState) : Prop := s.attempts ≤ 3 ∧ (s.locked = true ↔ s.attempts = 3)

theorem VInv.stepOk {s s' : VState} (hI : VInv s) (h : verifyOk s = some s') :
    VInv s' := by
  obtain ⟨-, rfl⟩ := verifyOk_spec h
  exact hI

theorem VInv.stepBad {s s' : VState} (hI : VInv s) (h : verifyBad s = some s') :
    VInv s' := by
  obtain ⟨hl, rfl⟩ := verifyBad_spec h
  obtain ⟨hle, hiff⟩ := hI
  have hne : s.attempts ≠ 3 := by
    intro he
    have hlock : s.locked = true := hiff.mpr he
    rw [hl] at hlock
    simp at hlock
  have hlt : s.attempts < 3 := by omega
  refine ⟨by show s.attempts + 1 ≤ 3; omega, ?_⟩
  constructor
  · intro hlock
    have h3 : 3 ≤ s.attempts + 1 := by simpa using hlock
    show s.attempts + 1 = 3
    omega
  · intro h3
    have h3' : s.attempts + 1 = 3 := h3
    show (decide (3 ≤ s.attempts + 1)) = true
    exact decide_eq_true (by omega)

theorem reachV_invariant {s : VState} (h : ReachV s) : VInv s := by
  induction h with
  | init => exact ⟨by decide, by decide⟩
  | ok _ hstep ih => exact VInv.stepOk ih hstep
  | bad _ hstep ih => exact VInv.stepBad ih hstep

/-- A successful verification can only happen with attempts to spare:
    success requires an unlocked state, and unlocked ⇒ attempts < 3. -/
theorem success_requires_attempts_lt_three {s s' : VState}
    (hr : ReachV s) (h : verifyOk s = some s') : s.attempts < 3 := by
  obtain ⟨hl, -⟩ := verifyOk_spec h
  obtain ⟨hle, hiff⟩ := reachV_invariant hr
  by_cases hc : s.attempts = 3
  · have hlock : s.locked = true := hiff.mpr hc
    rw [hlock] at hl
    exact absurd hl (by decide)
  · omega

/-- Once locked, a reachable conversation is frozen: no verification
    transition fires, hence `verified`/`attempts` can never change again
    (and the guarded tools stay blocked via `locked_absorbing`). -/
theorem reachV_locked_no_step {s : VState} (_hr : ReachV s)
    (hl : s.locked = true) :
    verifyOk s = none ∧ verifyBad s = none :=
  locked_absorbing hl

/-! ## Part 2 — The policy engine (settle/policy.py)

    Money terms come only from here (policy.py ll. 1–6, 56–93):
    constants from the `Policy` dataclass (ll. 20–27): 5% prompt-pay
    discount, at most 4 installments, $25.00 minimum installment,
    $100.00 small-balance threshold (below it the cap is 2).

    Modelled in integer cents: discount 5% of balance rounded half-up
    (ll. 66–67), installment split (ll. 43–49), offer list construction
    (ll. 63–80) and the `requested_monthly` policy check (ll. 82–92). -/

/-- $25.00 minimum installment, in cents (policy.py l. 24). -/
def minInstallmentC : Int := 2500

/-- $100.00 small-balance threshold, in cents (policy.py l. 25). -/
def smallThresholdC : Int := 10000

/-- Installment cap (policy.py ll. 24, 71): 2 for small balances,
    otherwise `max_installments` = 4. -/
def capFor (bal : Int) : Nat := if bal < smallThresholdC then 2 else 4

theorem capFor_bounds (bal : Int) : 2 ≤ capFor bal ∧ capFor bal ≤ 4 := by
  unfold capFor
  split <;> omega

/-- Prompt-pay discount in cents: `bal * 5%` rounded half-up
    (policy.py ll. 66–67 use Decimal ROUND_HALF_UP; for a non-negative
    balance `⌊(5·bal + 50)/100⌋` is exactly that). -/
def discountC (bal : Int) : Int := (bal * 5 + 50) / 100

theorem discountC_nonneg {bal : Int} (h : 0 ≤ bal) : 0 ≤ discountC bal := by
  unfold discountC
  exact Int.ediv_nonneg (by omega) (by omega)

theorem discountC_le {bal : Int} (h : 0 ≤ bal) : discountC bal ≤ bal := by
  have hmul : (bal * 5 + 50) / 100 * 100 ≤ bal * 5 + 50 :=
    Int.ediv_mul_le _ (by omega)
  unfold discountC
  omega

/-- `split` (policy.py ll. 43–49): divide `total` into `n` parts; the
    per-part base is `total / n` rounded down and the remainder cents
    land on the last part: `[base]*(n-1) ++ [total - base*(n-1)]`.
    For `total ≥ 0`, integer division is the code's ROUND_DOWN. -/
def splitCents (total : Int) (n : Nat) : List Int :=
  List.replicate (n - 1) (total / (n : Int)) ++
    [total - (total / (n : Int)) * ((n : Int) - 1)]

theorem sum_replicate_int (n : Nat) (a : Int) :
    (List.replicate n a).sum = (n : Int) * a := by
  induction n with
  | zero => simp
  | succ k ih =>
    rw [List.replicate_succ, List.sum_cons, ih]
    have hc : ((k + 1 : Nat) : Int) = (k : Int) + 1 := by omega
    rw [hc, Int.add_mul, Int.one_mul]
    omega

/-- Conservation of the split: the installments sum to the balance
    exactly — no interest, no fees (`finance_charge = 0`, policy.py
    l. 26).  Holds for every total and every `n`. -/
theorem splitCents_sum (total : Int) (n : Nat) : (splitCents total n).sum = total := by
  cases n with
  | zero => simp [splitCents]
  | succ k =>
    have h1 : ((k + 1 - 1 : Nat) : Int) = (k : Int) := by omega
    have h2 : ((k + 1 : Nat) : Int) - 1 = (k : Int) := by omega
    unfold splitCents
    rw [List.sum_append, sum_replicate_int, h1, List.sum_cons, List.sum_nil,
      Int.add_zero, h2, Int.mul_comm ((k : Int)) (total / ((k + 1 : Nat) : Int))]
    omega

/-- Every part of the split is at least the base part: the base parts
    are equal to it and the last part (which absorbs the remainder) is
    ≥ it for a non-negative total. -/
theorem splitCents_ge_base {total : Int} {n : Nat} (ht : 0 ≤ total) (hn : 1 ≤ n) :
    ∀ a ∈ splitCents total n, total / (n : Int) ≤ a := by
  intro a ha
  have hnpos : (0 : Int) < (n : Int) := by exact_mod_cast hn
  have hmul : (total / (n : Int)) * (n : Int) ≤ total :=
    Int.ediv_mul_le _ (show (n : Int) ≠ 0 by omega)
  have hmul' : (total / (n : Int)) * ((n : Int) - 1) = (total / (n : Int)) * (n : Int) - (total / (n : Int)) := by rw [Int.mul_sub, Int.mul_one]
  simp only [splitCents, List.mem_append, List.mem_singleton, List.mem_replicate] at ha
  rcases ha with ⟨_, rfl⟩ | rfl
  · omega
  · rw [hmul']; omega

/-- The base part itself occurs in the split whenever `n ≥ 2` (as it
    always is for installment offers).  Together with
    `splitCents_ge_base`, the list minimum of a split is its base —
    which is why the code's `min(amounts)` filter in `plan_options`
    (policy.py l. 75) is exactly a check on the base part. -/
theorem splitCents_base_mem {total : Int} {n : Nat} (hn : 2 ≤ n) :
    total / (n : Int) ∈ splitCents total n := by
  unfold splitCents
  exact List.mem_append.mpr (Or.inl (List.mem_replicate.mpr ⟨by omega, rfl⟩))

/-- The two offer kinds (policy.py l. 31). -/
inductive Kind where
  | payFull
  | installments
deriving Repr, DecidableEq

/-- An offer (policy.py `Option`, ll. 30–36): amounts and total in
    cents. -/
structure Offer where
  kind : Kind
  n : Nat
  amounts : List Int
  total : Int
deriving Repr

/-- Candidate installment counts: the set `{2, cap}` (policy.py l. 72). -/
def instCandidates (bal : Int) : List Nat :=
  if capFor bal = 2 then [2] else [2, capFor bal]

theorem instCandidates_spec {bal : Int} {n : Nat} (h : n ∈ instCandidates bal) :
    n = 2 ∨ n = capFor bal := by
  unfold instCandidates at h
  split at h
  · simp at h; exact Or.inl h
  · simp at h; exact h

/-- `plan_options` (policy.py ll. 63–80), in cents: a pay-in-full offer
    at the discounted total, plus one installment offer per candidate
    count whose split passes the minimum-installment filter (l. 75;
    by `splitCents_ge_base`/`splitCents_base_mem` the filter on
    `min(amounts)` is the condition `2500 ≤ bal / n` used here). -/
def optionsFor (bal : Int) : List Offer :=
  ⟨.payFull, 1, [bal - discountC bal], bal - discountC bal⟩ ::
  (instCandidates bal).filterMap (fun (n : Nat) =>
    if minInstallmentC ≤ bal / (n : Int)
    then some ⟨.installments, n, splitCents bal n, bal⟩
    else none)

/-- Full specification of an installment offer that `plan_options`
    actually returns. -/
theorem optionsFor_inst_spec {bal : Int} {o : Offer}
    (h : o ∈ optionsFor bal) (hk : o.kind = .installments) :
    (o.n = 2 ∨ o.n = capFor bal) ∧ o.amounts = splitCents bal o.n ∧
    o.total = bal ∧ minInstallmentC ≤ bal / (o.n : Int) := by
  simp only [optionsFor, List.mem_cons, List.mem_filterMap] at h
  rcases h with rfl | ⟨n, hn, hcond⟩
  · simp at hk
  · split at hcond
    · next hge =>
      simp only [Option.some.injEq] at hcond
      obtain ⟨rfl, rfl, rfl, rfl⟩ := hcond
      exact ⟨instCandidates_spec hn, rfl, rfl, hge⟩
    · simp at hcond

/-- No long plans: an offered installment count never exceeds the cap,
    and never exceeds 4 (2 for small balances). -/
theorem optionsFor_inst_n_le {bal : Int} {o : Offer}
    (h : o ∈ optionsFor bal) (hk : o.kind = .installments) :
    o.n ≤ capFor bal ∧ o.n ≤ 4 := by
  obtain ⟨hn, -, -, -⟩ := optionsFor_inst_spec h hk
  have hb := capFor_bounds bal
  rcases hn with h2 | h2 <;> rw [h2] <;> omega

/-- Conservation for offered plans: installment amounts sum to the
    balance exactly (`total` on the offer is the balance, policy.py
    l. 76). -/
theorem optionsFor_inst_sum {bal : Int} {o : Offer}
    (h : o ∈ optionsFor bal) (hk : o.kind = .installments) :
    o.amounts.sum = bal ∧ o.total = bal := by
  obtain ⟨-, ham, htotal, -⟩ := optionsFor_inst_spec h hk
  exact ⟨by rw [ham]; exact splitCents_sum bal o.n, htotal⟩

/-- If an installment offer passed the policy filter, the balance was
    non-negative (the filter forces `bal / n ≥ 2500` with `n ≥ 2`). -/
theorem bal_nonneg_of_inst {bal : Int} {o : Offer}
    (h : o ∈ optionsFor bal) (hk : o.kind = .installments) : 0 ≤ bal := by
  obtain ⟨hn, -, -, hge⟩ := optionsFor_inst_spec h hk
  unfold minInstallmentC at hge
  have hnI : (2 : Int) ≤ (o.n : Int) := by
    rcases hn with h2 | hcap
    · rw [h2]; omega
    · rw [hcap]; exact_mod_cast (capFor_bounds bal).1
  have hmul : (o.n : Int) * (bal / (o.n : Int)) ≤ bal := by
    have h := Int.ediv_mul_le bal (show (o.n : Int) ≠ 0 by omega)
    rwa [Int.mul_comm] at h
  have h3 : (o.n : Int) * (2500 : Int) ≤ (o.n : Int) * (bal / (o.n : Int)) :=
    Int.mul_le_mul_of_nonneg_left hge (by omega)
  omega

/-- The minimum-installment gate actually gates: every amount in an
    offered installment plan is at least $25.00. -/
theorem optionsFor_inst_amounts_min {bal : Int} {o : Offer}
    (h : o ∈ optionsFor bal) (hk : o.kind = .installments) :
    ∀ a ∈ o.amounts, minInstallmentC ≤ a := by
  obtain ⟨-, ham, -, hge⟩ := optionsFor_inst_spec h hk
  have hb := bal_nonneg_of_inst h hk
  have hn1 : 1 ≤ o.n := by
    rcases (optionsFor_inst_spec h hk).1 with h2 | hcap
    · rw [h2]; omega
    · have hb2 := (capFor_bounds bal).1; omega
  intro a ha
  rw [ham] at ha
  have hB := splitCents_ge_base hb hn1 a ha
  omega

/-- The pay-in-full offer is the discounted balance (policy.py
    ll. 66–69), and for a non-negative balance it never exceeds the
    balance and is itself non-negative after the discount bounds. -/
theorem optionsFor_payfull_spec {bal : Int} {o : Offer}
    (h : o ∈ optionsFor bal) (hk : o.kind = .payFull) :
    o.amounts = [bal - discountC bal] ∧ o.total = bal - discountC bal := by
  simp only [optionsFor, List.mem_cons, List.mem_filterMap] at h
  rcases h with rfl | ⟨n, -, hcond⟩
  · exact ⟨rfl, rfl⟩
  · split at hcond
    · simp only [Option.some.injEq] at hcond
      obtain ⟨rfl, -, -, -⟩ := hcond
      simp at hk
    · simp at hcond

theorem payfull_amount_le {bal : Int} (hb : 0 ≤ bal) :
    bal - discountC bal ≤ bal ∧ 0 ≤ discountC bal :=
  ⟨by have := discountC_nonneg hb; omega, discountC_nonneg hb⟩

/-- Installments needed for a requested monthly amount: ceiling
    division (policy.py l. 87, Decimal ROUND_CEILING; equal to
    `(bal + req - 1) / req` for `bal ≥ 0 < req`), or the code's
    sentinel `10^6` for a non-positive request. -/
def neededC (bal req : Int) : Int :=
  if req ≤ 0 then 1000000 else (bal + req - 1) / req

/-- The ceiling property: the computed number of installments really
    covers the balance.  (Holds for every balance, including negative:
    `(bal + req - 1) / req` with floor division is `⌈bal / req⌉`.) -/
theorem neededC_mul_ge {bal req : Int} (hr : 0 < req) :
    bal ≤ neededC bal req * req := by
  have hlt := Int.lt_ediv_add_one_mul_self (bal + req - 1) hr
  have hexp : ((bal + req - 1) / req + 1) * req
      = (bal + req - 1) / req * req + req := by rw [Int.add_mul, Int.one_mul]
  rw [hexp] at hlt
  unfold neededC
  simp only [if_neg (by omega : ¬ req ≤ 0)]
  omega

/-- `within_policy` (policy.py ll. 88–91): the requested monthly amount
    is at least the minimum installment and the needed installment
    count fits the cap. -/
def withinPolicy (bal req : Int) : Bool :=
  minInstallmentC ≤ req && neededC bal req ≤ (capFor bal : Int)

theorem withinPolicy_needed_le {bal req : Int}
    (h : withinPolicy bal req = true) : neededC bal req ≤ 4 := by
  have hb := capFor_bounds bal
  simp only [withinPolicy, Bool.and_eq_true] at h
  have h2 : neededC bal req ≤ (capFor bal : Int) := of_decide_eq_true h.2
  have hcap : (capFor bal : Int) ≤ 4 := by exact_mod_cast hb.2
  omega


/-! ## Part 3 — Offers and acceptance / enrollment

`settle/tools.py` ll. 143–176 and `settle/router.py` (state lifecycle).

`issueS` models `get_payment_options`: on a verified session it
REPLACES `state.offers` wholesale (tools.py l. 147).  Alongside the
live offer list we track `ever`, the set of every (id, amounts) ever
issued in this conversation — the history the at-most-once discipline
would need, and which the code itself does NOT keep.

`acceptS` models `accept_offer` (tools.py ll. 151–176): gates are
verified-and-unlocked (`_need_verified`), the offer id among the
CURRENT offers, and an affirmative reply pattern (l. 27, l. 161).
On success the code records the plan and sends the confirmation
email — with NO check for an already-existing plan (ll. 167–176):
a second acceptance overwrites the plan and emails again. -/

/-- An accepted plan: the issued offer's id and amount snapshot,
    plus the consenting message (the audit "confirmation"). -/
structure PlanRec where
  id : String
  amounts : List Int
  confirmation : String
deriving Repr

/-- Conversation state relevant to settlement: verification state,
    current offers, all offers ever issued, the current plan, and
    the number of confirmation emails sent. -/
structure EState where
  v : VState
  offers : List (String × Offer)
  ever : List (String × List Int)
  plan : Option PlanRec
  emails : Nat
deriving Repr

/-- Offer lookup by id (`{o.offer_id: o for o in state.offers}`
    consulted at tools.py l. 154). -/
def findOffer (id : String) : List (String × Offer) → Option Offer
  | [] => none
  | (k, o) :: rest => if k = id then some o else findOffer id rest

theorem findOffer_some_mem {id : String} {o : Offer} :
    ∀ {l : List (String × Offer)}, findOffer id l = some o → (id, o) ∈ l := by
  intro l
  induction l with
  | nil => intro h; cases h
  | cons p rest ih =>
    intro h
    obtain ⟨k, o'⟩ := p
    simp only [findOffer] at h
    by_cases hk : k = id
    · subst hk
      rw [if_pos rfl] at h
      have hoo : o' = o := Option.some.inj h
      subst hoo
      exact List.mem_cons_self
    · rw [if_neg hk] at h
      exact List.mem_cons_of_mem _ (ih h)

section Enrollment

variable (aff : String → Bool)

/-- `get_payment_options` (tools.py ll. 143–149): gated on
    verification; replaces the offer list; history accumulates. -/
def issueS (s : EState) (new : List (String × Offer)) : Option EState :=
  if guarded s.v then
    some { s with offers := new,
                  ever := s.ever ++ new.map (fun p => (p.1, p.2.amounts)) }
  else none

theorem issueS_spec {s s' : EState} {new : List (String × Offer)}
    (h : issueS s new = some s') :
    guarded s.v = true ∧
    s' = { s with offers := new,
                  ever := s.ever ++ new.map (fun p => (p.1, p.2.amounts)) } := by
  unfold issueS at h
  split at h
  · rename_i hc
    exact ⟨hc, (Option.some.inj h).symm⟩
  · simp at h

/-- `accept_offer` (tools.py ll. 151–176): all three gates, then the
    plan is recorded (overwriting any existing plan) and a
    confirmation email is counted.  Offer ids are looked up only in
    the CURRENT offer list — there is no balance re-check and no
    `state.plan` check. -/
def acceptS (s : EState) (id : String) (msg : String) : Option EState :=
  if guarded s.v && aff msg then
    (findOffer id s.offers).map
      (fun o => { s with plan := some ⟨id, o.amounts, msg⟩,
                         emails := s.emails + 1 })
  else none

theorem acceptS_eq {s : EState} {id msg : String} {o : Offer}
    (hg : guarded s.v = true) (ha : aff msg = true)
    (ho : findOffer id s.offers = some o) :
    acceptS aff s id msg =
      some { s with plan := some ⟨id, o.amounts, msg⟩,
                    emails := s.emails + 1 } := by
  simp [acceptS, hg, ha, ho]

theorem acceptS_none_of_not_guarded {s : EState} {id msg : String}
    (hg : ¬ guarded s.v = true) : acceptS aff s id msg = none := by
  have hc : ¬ (guarded s.v && aff msg) = true := by
    intro hcc
    simp only [Bool.and_eq_true] at hcc
    exact hg hcc.1
  unfold acceptS
  rw [if_neg hc]

theorem acceptS_none_of_not_aff {s : EState} {id msg : String}
    (ha : ¬ aff msg = true) : acceptS aff s id msg = none := by
  have hc : ¬ (guarded s.v && aff msg) = true := by
    intro hcc
    simp only [Bool.and_eq_true] at hcc
    exact ha hcc.2
  unfold acceptS
  rw [if_neg hc]

theorem acceptS_none_of_unknown {s : EState} {id msg : String}
    (ho : findOffer id s.offers = none) : acceptS aff s id msg = none := by
  unfold acceptS
  split
  · rw [ho, Option.map_none]
  · rfl

/-- Success specification: acceptance fires only with verification,
    an affirmative message, and a currently-issued offer — and then
    it sets the plan to exactly that offer's snapshot. -/
theorem acceptS_spec {s s' : EState} {id msg : String}
    (h : acceptS aff s id msg = some s') :
    guarded s.v = true ∧ aff msg = true ∧
    ∃ o, findOffer id s.offers = some o ∧
      s' = { s with plan := some ⟨id, o.amounts, msg⟩,
                    emails := s.emails + 1 } := by
  by_cases hg : guarded s.v = true
  · by_cases ha : aff msg = true
    · cases ho : findOffer id s.offers with
      | none =>
        rw [acceptS_none_of_unknown aff ho] at h
        simp at h
      | some o =>
        rw [acceptS_eq aff hg ha ho] at h
        exact ⟨hg, ha, o, rfl, (Option.some.inj h).symm⟩
    · rw [acceptS_none_of_not_aff aff ha] at h
      simp at h
  · rw [acceptS_none_of_not_guarded aff hg] at h
    simp at h

/-- Once offers are re-issued (replacing the list), ids from the old
    snapshot are no longer acceptable (tools.py l. 147 + l. 154). -/
theorem acceptS_none_after_reissue {s s' : EState}
    {new : List (String × Offer)} {id msg : String}
    (h : issueS s new = some s') (hlookup : findOffer id s'.offers = none) :
    acceptS aff s' id msg = none := by
  obtain ⟨-, rfl⟩ := issueS_spec h
  exact acceptS_none_of_unknown aff hlookup

/-- **The at-most-once violation, as a theorem about the code's own
    transition**: after a successful acceptance, the SAME acceptance
    succeeds AGAIN on the resulting state — offers and verification
    are untouched by `accept_offer` — minting a second confirmation
    (emails = original + 2) and re-recording the plan.  The code has
    no idempotency guard on `state.plan` (tools.py ll. 151–176). -/
theorem acceptS_repeatable {s s' : EState} {id msg : String}
    (h : acceptS aff s id msg = some s') :
    ∃ s'', acceptS aff s' id msg = some s'' ∧
           s''.emails = s.emails + 2 ∧ s''.plan = s'.plan := by
  obtain ⟨hg, ha, o, ho, rfl⟩ := acceptS_spec aff h
  refine ⟨{ s with plan := some ⟨id, o.amounts, msg⟩,
                   emails := s.emails + 2 }, ?_, rfl, rfl⟩
  exact acceptS_eq aff hg ha ho

/-- Reachable conversation states. -/
inductive ReachE (aff : String → Bool) : EState → Prop where
  | init : ReachE aff ⟨⟨false, 0, false⟩, [], [], none, 0⟩
  | vok {s : EState} {v' : VState} :
      ReachE aff s → verifyOk s.v = some v' → ReachE aff { s with v := v' }
  | vbad {s : EState} {v' : VState} :
      ReachE aff s → verifyBad s.v = some v' → ReachE aff { s with v := v' }
  | issue {s s' : EState} {new : List (String × Offer)} :
      ReachE aff s → issueS s new = some s' → ReachE aff s'
  | accept {s s' : EState} {id msg : String} :
      ReachE aff s → acceptS aff s id msg = some s' → ReachE aff s'

/-- The enrollment invariant that DOES hold: every current offer
    was really issued; any recorded plan names an offer that was
    issued at some point in this conversation, was accepted with an
    affirmative message while verified; and a recorded plan implies
    at least one confirmation email.  (What it cannot say: that the
    plan is unique, or that `emails = 1` — see `acceptS_repeatable`.) -/
def EInv (aff : String → Bool) (s : EState) : Prop :=
  (∀ p ∈ s.offers, (p.1, p.2.amounts) ∈ s.ever) ∧
  (∀ pl, s.plan = some pl →
     (pl.id, pl.amounts) ∈ s.ever ∧ aff pl.confirmation = true ∧
       s.v.verified = true) ∧
  (s.plan ≠ none → 1 ≤ s.emails)

theorem reachE_inv {aff : String → Bool} {s : EState} (h : ReachE aff s) :
    EInv aff s := by
  induction h with
  | init =>
    refine ⟨fun p hp => by simp at hp, fun pl hp => by simp at hp,
            fun hne => (hne rfl).elim⟩
  | vok _ hstep ih =>
    obtain ⟨h1, h2, h3⟩ := ih
    obtain ⟨-, rfl⟩ := verifyOk_spec hstep
    refine ⟨h1, fun pl hp => ?_, h3⟩
    obtain ⟨hmem, haff, -⟩ := h2 pl hp
    exact ⟨hmem, haff, rfl⟩
  | vbad _ hstep ih =>
    obtain ⟨h1, h2, h3⟩ := ih
    obtain ⟨-, rfl⟩ := verifyBad_spec hstep
    refine ⟨h1, fun pl hp => ?_, h3⟩
    obtain ⟨hmem, haff, hv⟩ := h2 pl hp
    exact ⟨hmem, haff, hv⟩
  | issue _ hstep ih =>
    obtain ⟨hg, rfl⟩ := issueS_spec hstep
    obtain ⟨h1, h2, h3⟩ := ih
    refine ⟨?_, fun pl hp => ?_, h3⟩
    · intro p hp
      show (p.1, p.2.amounts) ∈ _ ++ _
      exact List.mem_append.mpr
        (Or.inr (List.mem_map.mpr ⟨p, hp, rfl⟩))
    · obtain ⟨hmem, haff, hv⟩ := h2 pl hp
      exact ⟨List.mem_append.mpr (Or.inl hmem), haff, hv⟩
  | accept _ hstep ih =>
    obtain ⟨hg, ha, o, ho, rfl⟩ := acceptS_spec aff hstep
    obtain ⟨h1, h2, h3⟩ := ih
    have hmem := h1 _ (findOffer_some_mem ho)
    have hver := (guarded_iff.mp hg).1
    refine ⟨h1, fun pl hp => ?_, fun _ => Nat.succ_le_succ (Nat.zero_le _)⟩
    have hpl := Option.some.inj hp
    subst hpl
    exact ⟨hmem, ha, hver⟩

end Enrollment

/-! ## Part 4 — Ledger accounting: the `applied` dictionary

`settle/ledger.py` ll. 38–53 (`account_summary`).  The Python builds
`applied` as a DICTIONARY COMPREHENSION over payments (l. 41):

    applied = {p["applied_to"]: money(p["amount"]) for p in payments}

so if two payments are applied to the SAME encounter, the second
silently overwrites the first, and the dropped payment vanishes from
the balance math entirely (the ledger itself is read-only, so the
money is still there — the reported balance is just wrong, on the
high side).  The intended math is a per-encounter SUM
(`appliedSum` below).  The two agree iff every encounter receives at
most one payment — the hypothesis the code never checks. -/

/-- A payment, reduced to the two fields the accounting uses:
    which encounter it is applied to, and its amount (cents). -/
structure Payment where
  enc : String
  amount : Int
deriving Repr, DecidableEq

/-- The intended accounting: total applied to an encounter. -/
def appliedSum : List Payment → String → Int
  | [], _ => 0
  | p :: rest, e => (if p.enc = e then p.amount else 0) + appliedSum rest e

/-- The code's accounting: a fold whose accumulator is overwritten
    by each payment naming the encounter (last wins), starting at
    the "no payment" default 0. -/
def appliedDictFrom (init : Int) : List Payment → String → Int
  | [], _ => init
  | p :: rest, e => appliedDictFrom (if p.enc = e then p.amount else init) rest e

/-- The code's per-encounter applied amount (ledger.py l. 41). -/
def appliedDict (ps : List Payment) (e : String) : Int :=
  appliedDictFrom 0 ps e

/-- How many payments name a given encounter. -/
def countEnc : List Payment → String → Nat
  | [], _ => 0
  | p :: rest, e => (if p.enc = e then 1 else 0) + countEnc rest e

theorem countEnc_cons (p : Payment) (rest : List Payment) (e : String) :
    countEnc (p :: rest) e = (if p.enc = e then 1 else 0) + countEnc rest e :=
  rfl

theorem appliedDictFrom_no_match {init : Int} {e : String} :
    ∀ {ps : List Payment}, (∀ p ∈ ps, p.enc ≠ e) →
      appliedDictFrom init ps e = init := by
  intro ps
  induction ps generalizing init with
  | nil => intro _; rfl
  | cons p rest ih =>
    intro h
    have hp : p.enc ≠ e := h p (List.mem_cons_self)
    have hrest : ∀ q ∈ rest, q.enc ≠ e :=
      fun q hq => h q (List.mem_cons_of_mem _ hq)
    show appliedDictFrom (if p.enc = e then p.amount else init) rest e = init
    rw [if_neg hp]
    exact ih hrest

theorem appliedSum_no_match {e : String} :
    ∀ {ps : List Payment}, (∀ p ∈ ps, p.enc ≠ e) → appliedSum ps e = 0 := by
  intro ps
  induction ps with
  | nil => intro _; rfl
  | cons p rest ih =>
    intro h
    have hp : p.enc ≠ e := h p (List.mem_cons_self)
    have hrest : ∀ q ∈ rest, q.enc ≠ e :=
      fun q hq => h q (List.mem_cons_of_mem _ hq)
    show (if p.enc = e then p.amount else 0) + appliedSum rest e = 0
    rw [if_neg hp, ih hrest, Int.zero_add]

theorem countEnc_eq_zero {e : String} :
    ∀ {ps : List Payment}, countEnc ps e = 0 →
      ∀ p ∈ ps, p.enc ≠ e := by
  intro ps
  induction ps with
  | nil => intro _ p hp; simp at hp
  | cons p rest ih =>
    intro h q hq
    rw [countEnc_cons] at h
    have hsum : ((if p.enc = e then 1 else 0) + countEnc rest e) = 0 := h
    have hrest0 : countEnc rest e = 0 := by
      by_cases hc : p.enc = e
      · rw [if_pos hc] at hsum; omega
      · rw [if_neg hc, Nat.zero_add] at hsum; exact hsum
    cases List.mem_cons.mp hq with
    | inl hhead =>
      subst hhead
      by_cases hc : q.enc = e
      · rw [if_pos hc] at hsum; omega
      · exact hc
    | inr htail => exact ih hrest0 q htail

/-- **Correctness of the code's accounting, under its unchecked
    hypothesis**: if every encounter receives at most one payment,
    the dictionary and the sum agree — balances are computed
    correctly, no double-counting and nothing dropped. -/
theorem appliedDict_eq_appliedSum_of_single {e : String} :
    ∀ {ps : List Payment}, (∀ x, countEnc ps x ≤ 1) →
      appliedDict ps e = appliedSum ps e := by
  intro ps
  induction ps with
  | nil => intro _; rfl
  | cons p rest ih =>
    intro h
    have hrest_le : ∀ x, countEnc rest x ≤ 1 := by
      intro x
      have hx := h x
      rw [countEnc_cons] at hx
      omega
    by_cases hc : p.enc = e
    · -- Head matches: the ≤ 1 hypothesis forces countEnc rest e = 0.
      have hcount := h e
      rw [countEnc_cons, if_pos hc] at hcount
      have hrest0 : countEnc rest e = 0 := by omega
      have hnomatch : ∀ q ∈ rest, q.enc ≠ e := countEnc_eq_zero hrest0
      show appliedDictFrom (if p.enc = e then p.amount else 0) rest e =
        (if p.enc = e then p.amount else 0) + appliedSum rest e
      rw [if_pos hc, appliedDictFrom_no_match hnomatch,
          appliedSum_no_match hnomatch, Int.add_zero]
    · show appliedDictFrom (if p.enc = e then p.amount else 0) rest e =
        (if p.enc = e then p.amount else 0) + appliedSum rest e
      rw [if_neg hc, Int.zero_add]
      exact ih hrest_le

/-- **The failure, computed**: two payments of $10.00 and $20.00
    applied to the same encounter — the code reports $20.00 applied;
    $30.00 was actually applied.  The account balance shown to the
    patient (and used to build settlement offers) is overstated by
    the dropped $10.00. -/
theorem appliedDict_counterexample :
    appliedDict [⟨"E1", 1000⟩, ⟨"E1", 2000⟩] "E1" = 2000 ∧
    appliedSum [⟨"E1", 1000⟩, ⟨"E1", 2000⟩] "E1" = 3000 := by
  decide

/-! ## Part 5 — The audit hash chain and ledger conservation

`settle/audit.py` ll. 31–53.  `record` appends an entry with
`seq = len(prior)`, `prev = last hash or GENESIS ("0"*64)`, and
`hash = sha256(payload)`; `verify` replays the chain checking all
three links.  We abstract the digest as an uninterpreted `H`
(seq, prev-hash, digest-of-contents). -/

section Audit

variable (H : Nat → Int → Int → Int)

/-- One audit entry (audit.py `AuditEntry`, ll. 13–21): sequence
    number, previous hash, digest of the event contents, own hash.
    Hashes are modeled as integers; GENESIS is `"0"*64` in code. -/
structure Entry where
  seq : Nat
  prev : Int
  dig : Int
  hash : Int
deriving Repr, DecidableEq

def GENESIS : Int := 0

/-- `record` replayed: the chain built by successive appends. -/
def buildFrom : Nat → Int → List Int → List Entry
  | _, _, [] => []
  | k, prev, d :: ds =>
    ⟨k, prev, d, H k prev d⟩ :: buildFrom (k + 1) (H k prev d) ds

def build (ds : List Int) : List Entry := buildFrom H 0 GENESIS ds

/-- `verify` (audit.py ll. 46–53): every entry's sequence, previous
    hash, and own hash check out, recursively. -/
def verifyFrom : Nat → Int → List Entry → Bool
  | _, _, [] => true
  | k, prev, e :: es =>
    (e.seq == k) && (e.prev == prev) && (e.hash == H e.seq e.prev e.dig) &&
      verifyFrom (k + 1) e.hash es

/-- The chain predicate `verify` checks for: properly linked. -/
inductive Linked (H : Nat → Int → Int → Int) : Nat → Int → List Entry → Prop where
  | nil : Linked H k prev []
  | cons : e.seq = k → e.prev = prev → e.hash = H e.seq e.prev e.dig →
      Linked H (k + 1) e.hash es → Linked H k prev (e :: es)

/-- Chains built by `record` always verify. -/
theorem verifyFrom_buildFrom : ∀ (ds : List Int) (k : Nat) (prev : Int),
    verifyFrom H k prev (buildFrom H k prev ds) = true := by
  intro ds
  induction ds with
  | nil => intro k prev; rfl
  | cons d ds ih =>
    intro k prev
    show ((k == k) && (prev == prev) && (H k prev d == H k prev d) &&
      verifyFrom H (k + 1) (H k prev d) (buildFrom H (k + 1) (H k prev d) ds)) = true
    rw [beq_self_eq_true, beq_self_eq_true, beq_self_eq_true, ih]
    decide

theorem build_verify (ds : List Int) :
    verifyFrom H 0 GENESIS (build H ds) = true :=
  verifyFrom_buildFrom H ds 0 GENESIS

/-- **Soundness**: anything `verify` accepts is genuinely linked —
    sequence numbers, back-pointers, and hashes all check out.
    (Tamper-evidence: altering any entry's contents, hash, or link
    breaks the conjunction, so `verify` returns false.) -/
theorem verifyFrom_sound : ∀ {k : Nat} {prev : Int} {es : List Entry},
    verifyFrom H k prev es = true → Linked H k prev es := by
  intro k prev es
  induction es generalizing k prev with
  | nil => intro _; exact Linked.nil
  | cons e es ih =>
    intro h
    simp only [verifyFrom, Bool.and_eq_true, beq_iff_eq] at h
    obtain ⟨⟨⟨hseq, hprev⟩, hhash⟩, hrest⟩ := h
    exact Linked.cons hseq hprev hhash (ih hrest)

/-- **Completeness**: a genuinely linked chain always verifies. -/
theorem Linked.verify {k : Nat} {prev : Int} {es : List Entry}
    (h : Linked H k prev es) : verifyFrom H k prev es = true := by
  induction h with
  | nil => rfl
  | cons hseq hprev hhash _ ih =>
    rw [hhash, hseq, hprev] at ih
    simp only [verifyFrom, hseq, hprev, hhash, beq_self_eq_true, ih,
      Bool.and_true]

theorem build_linked (ds : List Int) : Linked H 0 GENESIS (build H ds) :=
  verifyFrom_sound H (build_verify H ds)

end Audit

/-! ## Part 5b — Conservation across the settlement boundary

The ledger is read-only (README l. 56: "The agent never moves money
or edits the ledger"); every settlement step is a function of the
conversation state alone.  We model a step as a lift
`EState → Option EState` over the product with a ledger view, and
prove the ledger component is invariant — settlement activity,
successful or failed, never changes a ledger balance.  `viewBalance`
is the balance the code SHOULD report (per-encounter sums), for
comparison with the dictionary-based figure of Part 4. -/

/-- A read-only ledger snapshot: per-encounter patient-responsibility
    totals (ledger.py ll. 33–35) and recorded payments. -/
structure LedgerView where
  prTotals : List (String × Int)
  payments : List Payment
deriving Repr

/-- The intended total balance across encounters. -/
def viewBalance (lv : LedgerView) : Int :=
  (lv.prTotals.map (fun p => p.2 - appliedSum lv.payments p.1)).sum

/-- Lift a conversation step over the ledger view: the ledger is an
    untouched passenger. -/
def liftStep (f : EState → Option EState) (sys : EState × LedgerView) :
    Option (EState × LedgerView) :=
  (f sys.1).map (fun s' => (s', sys.2))

/-- **Conservation**: any lifted step that succeeds leaves the
    ledger view — hence every balance derived from it — unchanged.
    A failed step returns `none` and likewise changes nothing.
    Together with `splitCents_sum` (Part 2: an accepted plan's
    installments total exactly the balance it was issued against),
    total settled never exceeds total obligated. -/
theorem liftStep_ledger_preserved {f : EState → Option EState}
    {sys sys' : EState × LedgerView}
    (h : liftStep f sys = some sys') : sys'.2 = sys.2 := by
  unfold liftStep at h
  cases hf : f sys.1 with
  | none => rw [hf] at h; simp at h
  | some s' =>
    rw [hf] at h
    have h2 : (s', sys.2) = sys' := Option.some.inj h
    exact (congrArg Prod.snd h2).symm

end Settle
