"""
Transactional email through Resend.

Used for workspace invitation and password-reset emails.
"""

import logging
from html import escape

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)


class EmailNotConfiguredError(RuntimeError):
    pass


def _layout(title: str, body_html: str, button_text: str, button_url: str, footer: str) -> str:
    return f"""<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif;color:#0f172a;">
    <div style="max-width:560px;margin:40px auto;background:white;border:1px solid #e2e8f0;border-radius:16px;padding:32px;">
      <h1 style="margin:0 0 12px;font-size:24px;">{escape(title)}</h1>
      <div style="font-size:15px;line-height:1.6;color:#475569;">{body_html}</div>
      <div style="margin:28px 0;">
        <a href="{escape(button_url)}" style="display:inline-block;padding:12px 20px;background:#4f46e5;color:white;text-decoration:none;border-radius:8px;font-weight:600;">{escape(button_text)}</a>
      </div>
      <p style="font-size:13px;line-height:1.5;color:#64748b;">{escape(footer)}</p>
      <p style="margin-top:28px;font-size:12px;color:#94a3b8;">If you weren't expecting this email, you can safely ignore it.</p>
    </div>
  </body>
</html>"""


async def send_email(to: str, subject: str, html: str) -> str | None:
    if not settings.resend_api_key:
        raise EmailNotConfiguredError("RESEND_API_KEY is not configured")

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from,
                "to": [to],
                "subject": subject,
                "html": html,
            },
        )

    if response.status_code >= 400:
        logger.error("Resend API error %s: %s", response.status_code, response.text)
        raise RuntimeError("Failed to send email")

    try:
        return response.json().get("id")
    except ValueError:
        return None


async def send_workspace_invitation_email(
    email: str,
    workspace_name: str,
    invitation_url: str,
) -> str | None:
    html = _layout(
        "You're invited to Samvadini",
        (
            "You've been invited to join "
            f"<strong>{escape(workspace_name)}</strong> as a workspace member."
        ),
        "Accept Invitation",
        invitation_url,
        "This invitation expires in 7 days.",
    )

    return await send_email(
        email,
        f"You're invited to join {workspace_name} on Samvadini",
        html,
    )


async def send_password_reset_email(email: str, reset_url: str, expires_minutes: int) -> str | None:
    html = _layout(
        "Reset your password",
        "We received a request to reset the password for your Samvadini account.",
        "Set a new password",
        reset_url,
        f"This link expires in {expires_minutes} minutes and can be used once.",
    )

    return await send_email(email, "Reset your Samvadini password", html)
