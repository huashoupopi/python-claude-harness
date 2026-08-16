"""Startup wiring.

Everything the dispatcher is allowed to serve at runtime is listed here.
Ops flips entries in and out during incidents, so the list is the source of
truth rather than whatever happens to be lying around on disk.
"""

ENABLED = [
    "order_placed",
    "order_shipped",
    "order_cancelled",
    "payment_captured",
    "payment_failed",
    "user_signup",
    "user_deleted",
    "email_bounced",
    "cart_abandoned",
    "review_posted",
    "refund_issued",
    "chargeback_opened",
]
