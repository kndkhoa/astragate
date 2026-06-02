"""
Billing router — placeholder.

Stripe Checkout and webhook endpoints implemented in Tasks 24 & 25.
All routes under this router require JWT authentication.

Note: The Stripe webhook endpoint (/api/billing/webhook) will need to be
excluded from auth when implemented (it uses Stripe signature verification instead).
"""
from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


# Placeholder — implemented in Tasks 24 & 25
# POST /api/billing/topup
# GET  /api/billing/balance
# GET  /api/billing/transactions
# POST /api/billing/webhook  (will override dependency — uses Stripe signature auth)
