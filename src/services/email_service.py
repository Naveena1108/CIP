"""
Email delivery service for AI CRISS / CIP backend authentication.
Supports real institutional SMTP delivery and development console fallback.
Complies with CIP Phase 2 OTP requirements (Section 17).
"""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from src.runtime_env import is_deployed_environment

logger = logging.getLogger("ai_criss.email")

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "CIP Intelligence <no-reply@cip.edu>").strip()
SMTP_TLS = os.getenv("SMTP_TLS", "true").lower() in ("true", "1", "yes")


def render_otp_html(recipient_email: str, otp_code: str, purpose: str = "login") -> str:
    """Render Burgundy-styled HTML email matching CIP institutional design."""
    purpose_title = "Sign In Verification" if purpose == "login" else (
        "Account Activation" if purpose == "signup" else "Password Reset"
    )
    spaced_code = " &nbsp; ".join(list(otp_code))

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>CIP Verification Code</title>
</head>
<body style="margin: 0; padding: 0; background-color: #F8F7F4; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #252124;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #F8F7F4; padding: 40px 15px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" max-width="520" style="max-width: 520px; background-color: #FFFFFF; border-radius: 12px; border: 1px solid #E5DFD9; box-shadow: 0 4px 16px rgba(92, 27, 42, 0.06); overflow: hidden;">
          <!-- Header Banner -->
          <tr>
            <td style="background-color: #7A2438; padding: 24px 32px; text-align: left;">
              <span style="display: inline-block; background-color: rgba(255, 255, 255, 0.18); border: 1px solid rgba(255, 255, 255, 0.3); border-radius: 6px; padding: 3px 10px; font-size: 11px; font-weight: 700; color: #FFFFFF; letter-spacing: 1.5px; text-transform: uppercase;">CIP</span>
              <h1 style="margin: 10px 0 0 0; color: #FFFFFF; font-size: 18px; font-weight: 600; letter-spacing: -0.2px;">Crisis Intelligence Platform</h1>
            </td>
          </tr>
          
          <!-- Content Body -->
          <tr>
            <td style="padding: 32px 32px 24px 32px;">
              <div style="font-size: 13px; font-weight: 600; color: #7A2438; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 8px;">{purpose_title}</div>
              <p style="margin: 0 0 18px 0; font-size: 15px; line-height: 1.5; color: #252124;">
                A single-use verification code was requested for your institutional account (<strong>{recipient_email}</strong>).
              </p>
              
              <!-- OTP Box -->
              <div style="background-color: #F3F0ED; border: 1px solid #E5DFD9; border-radius: 8px; padding: 24px; text-align: center; margin: 24px 0;">
                <div style="font-size: 11px; font-weight: 600; color: #6F686B; text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 10px;">Your 6-Digit Code</div>
                <div style="font-size: 32px; font-weight: 700; color: #7A2438; letter-spacing: 8px; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace;">
                  {otp_code}
                </div>
                <div style="font-size: 12px; color: #9A9295; margin-top: 10px;">Expires in 5 minutes &bull; Single-use only</div>
              </div>

              <p style="margin: 18px 0 0 0; font-size: 13px; line-height: 1.5; color: #6F686B;">
                Enter this code on the CIP authentication screen to proceed. Never share this code with anyone. Institutional administrators and CIP operators will never ask for your verification code.
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color: #F8F7F4; border-top: 1px solid #E5DFD9; padding: 18px 32px; font-size: 11px; line-height: 1.4; color: #9A9295; text-align: center;">
              This is an automated notification from the CIP Identity &amp; Access Authority.<br>
              If you did not initiate this request, no action is required; the code will expire automatically.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


class EmailDeliveryResult:
    """Result container that evaluates as boolean and exposes error details."""
    def __init__(self, success: bool, error: Optional[str] = None, provider: Optional[str] = None):
        self.success = success
        self.error = error
        self.provider = provider

    def __bool__(self) -> bool:
        return self.success

    def __repr__(self) -> str:
        return f"<EmailDeliveryResult success={self.success} provider={self.provider} error={self.error}>"


def send_otp_email(
    recipient_email: str,
    otp_code: str,
    purpose: str = "login",
) -> EmailDeliveryResult:
    """
    Deliver single-use verification code to the recipient's email address.
    Uses SMTP or Resend API when configured via environment variables.
    Strictly adheres to CIP security policies: never logs plaintext OTPs.
    Returns EmailDeliveryResult (evaluates as True if succeeded, False if failed).
    """
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_from = os.getenv("SMTP_FROM", "CIP Intelligence <no-reply@cip.edu>").strip()
    smtp_tls = os.getenv("SMTP_TLS", "true").lower() in ("true", "1", "yes")
    resend_api_key = os.getenv("RESEND_API_KEY", "").strip()

    subject = f"Your CIP Verification Code"
    plain_text = (
        f"Your CIP verification code is: {otp_code}\n\n"
        f"This code was requested for {recipient_email} for {purpose}.\n"
        f"It is valid for 5 minutes and single-use only.\n"
        f"If you did not request this code, please ignore this email."
    )
    html_content = render_otp_html(recipient_email, otp_code, purpose)

    # 1. Attempt SMTP delivery if configured
    if smtp_host:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Your CIP Verification Code: {otp_code}"
            msg["From"] = smtp_from
            msg["To"] = recipient_email

            part1 = MIMEText(plain_text, "plain", "utf-8")
            part2 = MIMEText(html_content, "html", "utf-8")
            msg.attach(part1)
            msg.attach(part2)

            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                if smtp_tls:
                    server.starttls()
                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)
                server.send_message(msg)

            logger.info(f"Successfully dispatched OTP email via SMTP ({smtp_host}:{smtp_port}) to {recipient_email} (purpose: {purpose}).")
            return EmailDeliveryResult(True, provider="SMTP")
        except Exception as exc:
            err_msg = f"SMTP error ({smtp_host}:{smtp_port}): {type(exc).__name__} - {exc}"
            logger.error(f"Failed to dispatch OTP email via SMTP for {recipient_email}: {err_msg}")
            return EmailDeliveryResult(False, error=err_msg, provider="SMTP")

    # 2. Attempt Resend API if configured
    if resend_api_key:
        try:
            import httpx
            from_addr = smtp_from if ("@" in smtp_from and "no-reply@cip.edu" not in smtp_from) else "CIP Verification <onboarding@resend.dev>"
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_api_key}", "Content-Type": "application/json"},
                json={
                    "from": from_addr,
                    "to": [recipient_email],
                    "subject": f"Your CIP Verification Code",
                    "html": html_content,
                    "text": plain_text,
                },
                timeout=10.0,
            )
            if resp.status_code in (200, 201):
                logger.info(f"Successfully dispatched OTP email via Resend API to {recipient_email} (purpose: {purpose}).")
                return EmailDeliveryResult(True, provider="RESEND")
            else:
                err_msg = f"Resend API returned HTTP {resp.status_code}: {resp.text}"
                logger.error(f"Resend API email dispatch failed for {recipient_email}: {err_msg}")
                return EmailDeliveryResult(False, error=err_msg, provider="RESEND")
        except Exception as exc:
            err_msg = f"Resend API exception: {type(exc).__name__} - {exc}"
            logger.error(f"Resend API email dispatch failed for {recipient_email}: {err_msg}")
            return EmailDeliveryResult(False, error=err_msg, provider="RESEND")

    # 3. No email provider configured
    is_prod = is_deployed_environment()
    if is_prod:
        err_msg = (
            "No email provider configured in production environment. "
            "Required: either RESEND_API_KEY or (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)."
        )
        logger.error(f"Cannot dispatch verification email to {recipient_email}: {err_msg}")
        return EmailDeliveryResult(False, error=err_msg, provider="NONE")

    # In local development without credentials: simulate delivery safely without logging raw code
    logger.info(f"[CIP DEV SIMULATION] Verification code dispatched for {recipient_email} (purpose: {purpose.upper()}, expires in 5m).")
    return EmailDeliveryResult(True, provider="DEV_SIMULATION")
