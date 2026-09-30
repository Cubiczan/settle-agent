"""Practice financial policy - the only place money terms are decided.

The LLM never invents a discount, a due date or an installment amount. It asks
this module for the options the practice has pre-approved and presents them.
Anything outside policy becomes a staff escalation.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
import hashlib
import json

CENT = Decimal("0.01")


@dataclass(frozen=True)
class Policy:
    prompt_pay_discount_pct: Decimal = Decimal("5")
    max_installments: int = 4          # longer plans need staff approval
    min_installment: Decimal = Decimal("25.00")
    small_balance_threshold: Decimal = Decimal("100.00")  # below: max 2 installments
    finance_charge: Decimal = Decimal("0")


@dataclass
class Option:
    kind: str                    # "pay_in_full" | "installments"
    installments: int
    amounts: list[str]           # exact cents per installment, as strings
    total: str
    note: str = ""
    offer_id: str = field(default="")

    def to_dict(self) -> dict:
        return asdict(self)


def _d(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT)


def split(total: Decimal, n: int) -> list[Decimal]:
    """Split into n installments; the remainder cents land on the last one."""
    base = (total / n).quantize(CENT, rounding=ROUND_DOWN)
    amounts = [base] * n
    amounts[-1] = total - base * (n - 1)
    return amounts


def _offer_id(patient_id: str, opt: Option) -> str:
    raw = json.dumps([patient_id, opt.kind, opt.installments, opt.amounts], sort_keys=True)
    return "OFR-" + hashlib.sha256(raw.encode()).hexdigest()[:10].upper()


def plan_options(patient_id: str, balance, policy: Policy = Policy(),
                 requested_monthly=None) -> dict:
    """Return the pre-approved options for this balance.

    If the patient asked for a specific monthly amount, report whether it fits
    policy. `within_policy=False` means the agent must offer the closest
    allowed option or hand off to staff (e.g. financial-assistance screening).
    """
    bal = _d(balance)
    options: list[Option] = []

    discount = (bal * policy.prompt_pay_discount_pct / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    full = Option("pay_in_full", 1, [str(bal - discount)], str(bal - discount),
                  note=f"{policy.prompt_pay_discount_pct}% prompt-pay discount (saves ${discount})")
    options.append(full)

    cap = 2 if bal < policy.small_balance_threshold else policy.max_installments
    for n in sorted({2, cap}):
        amounts = split(bal, n)
        if min(amounts) < policy.min_installment:
            continue
        options.append(Option("installments", n, [str(a) for a in amounts], str(bal),
                              note="no interest, no fees"))

    for o in options:
        o.offer_id = _offer_id(patient_id, o)

    result = {"balance": str(bal), "options": [o.to_dict() for o in options],
              "max_installments": cap, "min_installment": str(policy.min_installment)}

    if requested_monthly is not None:
        req = _d(requested_monthly)
        needed = int((bal / req).to_integral_value(rounding="ROUND_CEILING")) if req > 0 else 10**6
        result["requested"] = {
            "monthly": str(req),
            "installments_needed": needed,
            "within_policy": req >= policy.min_installment and needed <= cap,
        }
    return result
