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


def send_otp_email(
    recipient_email: str,
    otp_code: str,
    purpose: str = "login",
) -> bool:
    """
    Deliver single-use verification code to the recipient's email address.
    Uses SMTP when configured via environment variables.
    Falls back to structured console/logger output for development and testing.
    """
    subject = f"Your CIP Verification Code: {otp_code}"
    plain_text = (
        f"Your CIP verification code is: {otp_code}\n\n"
        f"This code was requested for {recipient_email} for {purpose}.\n"
        f"It is valid for 5 minutes and single-use only.\n"
        f"If you did not request this code, please ignore this email."
    )
    html_content = render_otp_html(recipient_email, otp_code, purpose)

    # If SMTP is configured, attempt real SMTP transmission
    if SMTP_HOST:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = SMTP_FROM
            msg["To"] = recipient_email

            part1 = MIMEText(plain_text, "plain", "utf-8")
            part2 = MIMEText(html_content, "html", "utf-8")
            msg.attach(part1)
            msg.attach(part2)

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                if SMTP_TLS:
                    server.starttls()
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)

            logger.info(f"Successfully dispatched OTP email via SMTP to {recipient_email} (purpose: {purpose}).")
            return True
        except Exception as exc:
            logger.error(f"Failed to dispatch OTP email via SMTP ({SMTP_HOST}:{SMTP_PORT}): {exc}. Falling back to logger.")

    # Development & test fallback: log clearly so developers and operators can verify immediately
    banner = (
        "\n" + "=" * 80 + "\n"
        f" [CIP SECURITY AUTHENTICATION NOTIFICATION]\n"
        f"  Recipient: {recipient_email}\n"
        f"  Purpose:   {purpose.upper()}\n"
        f"  OTP Code:  {otp_code}\n"
        f"  Expiry:    5 minutes (single-use)\n"
        + "=" * 80 + "\n"
    )
    print(banner, flush=True)
    logger.info(f"[CIP OTP] Generated verification code {otp_code} for {recipient_email} ({purpose}).")
    return True
