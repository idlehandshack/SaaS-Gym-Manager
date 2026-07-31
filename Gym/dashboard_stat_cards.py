# Gym/dashboard_stat_cards.py
STAT_CARD_REGISTRY = [
    ("active_members",       "Active Members"),
    ("unregistered_members", "Unregistered Members"),
    ("today_revenue",        "Today's Revenue"),        # gst_enabled only
    ("today_collection",     "Today's Collection"),      # gst_enabled only
    ("month_revenue",        "Monthly Revenue"),         # gst_enabled only
    ("month_collection",     "Monthly Collection"),       # gst_enabled only
    ("today_income",         "Today's Income"),          # non-gst only
    ("month_income",         "Monthly Income"),          # non-gst only
    ("today_attendance",     "Today's Attendance"),
    ("pending_payments",     "Pending Payments"),
    ("pending_amount",       "Pending Amount"),
    ("expiring_today",       "Expiring Today"),
    ("total_trainers",       "Total Trainers"),
    ("total_staff",          "Total Staff"),
    ("new_members_month",    "New This Month"),
    ("renewals_today",       "Renewals Today"),
    ("ai_credits_balance",   "AI Credits Remaining"),
    ("ai_credits_used",      "AI Credits Used"),
]

STAT_CARD_KEYS = {k for k, _ in STAT_CARD_REGISTRY}