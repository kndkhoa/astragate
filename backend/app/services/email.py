"""
Resend email service — async email dispatch with HTML templates.
"""
import anyio
import resend
from decimal import Decimal
from typing import Any, Dict

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

# Configure Resend key
resend.api_key = settings.RESEND_API_KEY

# Determine if we should run in Mock Mode (no key configured)
IS_MOCK_EMAIL = not settings.RESEND_API_KEY or settings.RESEND_API_KEY.startswith("change-me")


def _send_sync(params: Dict[str, Any]) -> Any:
    """Synchronous send method executed in a separate thread."""
    return resend.Emails.send(params)


async def send_email(to_email: str, subject: str, html_content: str) -> None:
    """
    Send an email asynchronously using anyio's thread-pool runner.
    Falls back to structured log emission in mock mode.
    """
    if IS_MOCK_EMAIL:
        logger.info(
            "email_mock_sent",
            to=to_email,
            subject=subject,
            body_preview=html_content[:200] + "...",
        )
        return

    params = {
        "from": "AstraGate <onboarding@resend.dev>",
        "to": to_email,
        "subject": subject,
        "html": html_content,
    }

    try:
        await anyio.to_thread.run_sync(_send_sync, params)
        logger.info("email_sent_successfully", to=to_email, subject=subject)
    except Exception as exc:
        logger.error(
            "email_send_failed",
            to=to_email,
            subject=subject,
            error=str(exc),
            exc_info=True,
        )


# ── Email Templates ───────────────────────────────────────────────────────────


async def send_welcome(user_email: str, virtual_key_prefix: str) -> None:
    """Send welcome email to a newly registered user with quickstart instructions."""
    subject = "Welcome to AstraGate — Quick Start Guide"
    html = f"""
    <h1>Welcome to AstraGate!</h1>
    <p>We are excited to help you unify your LLM API calls with high performance and margin markup control.</p>
    <p>Your default API Key has been generated with prefix: <code>{virtual_key_prefix}...</code></p>
    
    <h3>Quick Start:</h3>
    <pre>
    curl -X POST https://api.astragate.io/v1/chat/completions \\
      -H "Authorization: Bearer YOUR_API_KEY" \\
      -H "Content-Type: application/json" \\
      -d '{{
        "model": "llama-3.1-8b",
        "messages": [{{"role": "user", "content": "Hello AstraGate!"}}]
      }}'
    </pre>
    
    <p>Log in to your <a href="http://localhost:3000/dashboard">Dashboard</a> to manage keys and view usage analytics.</p>
    """
    await send_email(user_email, subject, html)


async def send_low_credit(user_email: str, balance: Decimal, topup_url: str) -> None:
    """Send alert to a customer when their credit balance drops below 20% of their last top-up."""
    subject = "Action Required: Low Credit Balance Warning"
    html = f"""
    <h3>Your AstraGate credit balance is running low!</h3>
    <p>Your current balance is: <strong>${balance:.4f} USD</strong></p>
    <p>To prevent API call disruptions, please top up your account balance.</p>
    <p><a href="{topup_url}" style="display:inline-block;background:#000;color:#fff;padding:8px 16px;text-decoration:none;border-radius:4px;">Top Up Credits Now</a></p>
    """
    await send_email(user_email, subject, html)


async def send_payment_confirmation(user_email: str, amount: Decimal, new_balance: Decimal) -> None:
    """Send receipt to a customer after successful Stripe top-up."""
    subject = "Payment Confirmed — AstraGate Credits Added"
    html = f"""
    <h3>Thank you for your payment!</h3>
    <p>We have successfully added <strong>${amount:.2f} USD</strong> credits to your account.</p>
    <p>Your updated credit balance is: <strong>${new_balance:.4f} USD</strong></p>
    <p>Go to your <a href="http://localhost:3000/dashboard">Dashboard</a> to view transaction details.</p>
    """
    await send_email(user_email, subject, html)


async def send_provider_warning(admin_email: str, provider_name: str, balance: Decimal, threshold: Decimal) -> None:
    """Send alert to admin when an upstream provider balance falls below the warning threshold."""
    subject = f"Warning: Provider '{provider_name}' balance is low"
    html = f"""
    <h3>Upstream Provider Balance Low</h3>
    <p>The balance for provider <strong>{provider_name}</strong> is: <strong>${balance:.4f} USD</strong></p>
    <p>This is below your warning threshold of: <strong>${threshold:.2f} USD</strong>.</p>
    <p>Please top up your provider account to avoid service interruption.</p>
    """
    await send_email(admin_email, subject, html)


async def send_provider_hard_stop(admin_email: str, provider_name: str, balance: Decimal) -> None:
    """Send urgent alert to admin when an upstream provider balance falls below the hard stop threshold."""
    subject = f"URGENT: Hard Stop Activated for Provider '{provider_name}'"
    html = f"""
    <h2 style="color:red;">Hard Stop Activated</h2>
    <p>The balance for provider <strong>{provider_name}</strong> has fallen to: <strong>${balance:.4f} USD</strong></p>
    <p>AstraGate has activated a **Hard Stop** for this provider. All subsequent calls to this provider will be blocked or routed to fallback.</p>
    <p>Please nạp tiền to the provider account immediately and manually release the hard stop in the admin dashboard.</p>
    """
    await send_email(admin_email, subject, html)
