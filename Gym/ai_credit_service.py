"""
Gym/ai_credit_service.py
-------------------------
Centralized, race-safe helpers for the AI Credit wallet. Every balance
mutation goes through one of these functions so that:
  (a) writes are wrapped in a DB transaction with a row lock
      (select_for_update) — two concurrent Register Scan saves for the
      same gym can never both deduct the last credit,
  (b) every change is always paired with an AICreditTransaction audit row,
  (c) balance can never go negative.

Nothing outside this module should touch GymAICredit.balance directly.
"""

from django.db import transaction
from Gym.models import GymAICredit, AICreditTransaction


def get_or_create_wallet(gym):
    """
    Returns this gym's wallet. In normal operation the wallet already
    exists (created by the Gym post_save signal) — get_or_create here is
    just a safety net for gyms that predate this feature, and grants them
    the same 10 free credits on first access.
    """
    wallet, created = GymAICredit.objects.get_or_create(gym=gym)
    if created:
        AICreditTransaction.objects.create(
            gym=gym,
            credits=wallet.balance,
            balance_after=wallet.balance,
            reason="+10 Free Credits",
            created_by=None,
        )
    return wallet


def has_credit(gym) -> bool:
    """True if the gym has at least one AI credit available."""
    return get_or_create_wallet(gym).balance > 0


@transaction.atomic
def deduct_credit(gym, reason="Register Scan", created_by=None) -> bool:
    """
    Atomically deducts exactly 1 credit. Row-locks the wallet so two
    concurrent imports can never both succeed against a balance of 1.
    Returns False (deducts nothing) if the balance is already 0 — this
    stays defensive even though callers should already have checked
    has_credit() before starting the AI extraction.
    """
    wallet, _ = GymAICredit.objects.select_for_update().get_or_create(gym=gym)
    if wallet.balance <= 0:
        return False
    wallet.balance -= 1
    wallet.total_used += 1
    wallet.save(update_fields=["balance", "total_used", "updated_at"])
    AICreditTransaction.objects.create(
        gym=gym, credits=-1, balance_after=wallet.balance,
        reason=reason, created_by=created_by,
    )
    return True


@transaction.atomic
def add_credits(gym, amount, reason, created_by=None):
    """Adds `amount` (> 0) credits — used for grants/top-ups."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    wallet, _ = GymAICredit.objects.select_for_update().get_or_create(gym=gym)
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])
    AICreditTransaction.objects.create(
        gym=gym, credits=amount, balance_after=wallet.balance,
        reason=reason, created_by=created_by,
    )
    return wallet


@transaction.atomic
def admin_adjust_credits(gym, delta, reason, created_by=None):
    """
    Super Admin manual adjustment. `delta` may be positive (add) or
    negative (deduct); `reason` is required. A deduction larger than the
    current balance is clamped to 0 rather than going negative.
    """
    if delta == 0:
        raise ValueError("delta must be non-zero")
    if not reason or not reason.strip():
        raise ValueError("A reason is required for manual credit adjustments.")

    wallet, _ = GymAICredit.objects.select_for_update().get_or_create(gym=gym)
    new_balance = wallet.balance + delta
    if new_balance < 0:
        delta = -wallet.balance  # clamp: never below 0
        new_balance = 0
    wallet.balance = new_balance
    wallet.save(update_fields=["balance", "updated_at"])
    AICreditTransaction.objects.create(
        gym=gym, credits=delta, balance_after=wallet.balance,
        reason=reason.strip(), created_by=created_by,
    )
    return wallet