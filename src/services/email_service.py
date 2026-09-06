import os
import smtplib
import logging
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("sthxtechnologies-email")

class EmailService:
    @staticmethod
    def send_otp_email(to_email: str, code: str, purpose: str = "EMAIL_VERIFICATION") -> bool:
        """
        Sends a 6-digit OTP email via Brevo Email API (https://api.brevo.com/v3/smtp/email).
        Reads config from environment variables:
        - BREVO_API_KEY
        - EMAIL_FROM
        - EMAIL_FROM_NAME
        Fallbacks to SMTP or Mock logging if BREVO_API_KEY is not available.
        """
        brevo_api_key = os.getenv("BREVO_API_KEY", "")
        email_from = os.getenv("EMAIL_FROM", "support@sthxtechnologies.com")
        email_from_name = os.getenv("EMAIL_FROM_NAME", "STHX Technologies Gym Portal")

        if purpose == "PASSWORD_RESET":
            subject = "Reset Your STHX Technologies Password"
            title = "Password Reset Request"
            message_intro = "We received a request to reset your password for your STHX Technologies account."
        else:
            subject = "Verify Your STHX Technologies Account"
            title = "Verify Your Email Address"
            message_intro = "Thank you for registering on the STHX Technologies Gym Management Portal."

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8">
          <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #050B14; color: #E2E8F0; margin: 0; padding: 40px 20px; }}
            .container {{ max-width: 540px; margin: 0 auto; background: #0F172A; border: 1px solid #1E293B; border-radius: 20px; overflow: hidden; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5); }}
            .header {{ background: linear-gradient(135deg, #0066FF 0%, #00F0FF 100%); padding: 30px 20px; text-align: center; color: #FFFFFF; }}
            .header h1 {{ margin: 0; font-size: 26px; font-weight: 900; letter-spacing: 2px; text-transform: uppercase; }}
            .content {{ padding: 32px 28px; text-align: center; }}
            .title {{ font-size: 20px; font-weight: 700; color: #FFFFFF; margin-top: 0; margin-bottom: 12px; }}
            .text {{ font-size: 14px; color: #94A3B8; line-height: 1.6; margin-bottom: 24px; }}
            .otp-box {{ background: #1E293B; border: 2px solid #0066FF; border-radius: 14px; padding: 18px 24px; font-size: 36px; font-weight: 900; letter-spacing: 10px; color: #00F0FF; font-family: 'Courier New', Courier, monospace; display: inline-block; margin: 16px 0; box-shadow: 0 0 20px rgba(0, 102, 255, 0.3); }}
            .notice {{ font-size: 12px; font-weight: 600; color: #F59E0B; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2); padding: 10px 16px; border-radius: 10px; margin-top: 20px; display: inline-block; }}
            .footer {{ background: #090E17; border-top: 1px solid #1E293B; padding: 20px; text-align: center; font-size: 11px; color: #64748B; }}
          </style>
        </head>
        <body>
          <div class="container">
            <div class="header">
              <h1>STHX Technologies</h1>
              <p style="margin: 4px 0 0 0; font-size: 12px; opacity: 0.9;">Gym Management Portal</p>
            </div>
            <div class="content">
              <h2 class="title">{title}</h2>
              <p class="text">{message_intro}<br>Please use the 6-digit verification code below to proceed:</p>
              
              <div class="otp-box">{code}</div>
              
              <div>
                <span class="notice">⏱️ This code is valid for 10 minutes only.</span>
              </div>
              
              <p style="font-size: 12px; color: #64748B; margin-top: 28px;">If you did not request this verification code, please safely ignore this email.</p>
            </div>
            <div class="footer">
              &copy; STHX Technologies Gym Management System. All rights reserved.
            </div>
          </div>
        </body>
        </html>
        """

        plain_text = f"{title}\n\n{message_intro}\n\nYour 6-digit verification code: {code}\n\nValid for 10 minutes.\nIf you did not request this code, please ignore this email."

        # 1. Primary Delivery: Brevo Email API
        if brevo_api_key:
            try:
                res = requests.post(
                    "https://api.brevo.com/v3/smtp/email",
                    headers={
                        "api-key": brevo_api_key,
                        "Content-Type": "application/json"
                    },
                    json={
                        "sender": {
                            "name": email_from_name,
                            "email": email_from
                        },
                        "to": [
                            {
                                "email": to_email
                            }
                        ],
                        "subject": subject,
                        "htmlContent": html_content
                    },
                    timeout=10
                )
                if res.status_code in [200, 201]:
                    logger.info(f"Successfully sent OTP email ({purpose}) to {to_email} via Brevo API.")
                    return True
                else:
                    logger.warning(f"Brevo API returned status {res.status_code}: {res.text}")
            except Exception as e:
                logger.error(f"Failed to send email via Brevo API: {e}")

        # 2. Fallback Delivery: Resend API if configured
        resend_api_key = os.getenv("RESEND_API_KEY", "")
        if resend_api_key:
            try:
                res = requests.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {resend_api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "from": f"{email_from_name} <{email_from}>",
                        "to": [to_email],
                        "subject": subject,
                        "html": html_content
                    },
                    timeout=10
                )
                if res.status_code in [200, 201]:
                    logger.info(f"Successfully sent OTP email ({purpose}) to {to_email} via Resend API.")
                    return True
            except Exception as e:
                logger.error(f"Failed to send email via Resend API: {e}")

        # 3. Fallback Delivery: SMTP
        smtp_username = os.getenv("SMTP_USERNAME", "")
        smtp_password = os.getenv("SMTP_PASSWORD", "")
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        if smtp_username and smtp_password:
            try:
                msg = MIMEMultipart("alternative")
                msg["From"] = f"{email_from_name} <{email_from}>"
                msg["To"] = to_email
                msg["Subject"] = subject
                msg.attach(MIMEText(plain_text, "plain"))
                msg.attach(MIMEText(html_content, "html"))

                if smtp_port == 465:
                    with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=10) as server:
                        server.login(smtp_username, smtp_password)
                        server.send_message(msg)
                else:
                    with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                        server.starttls()
                        server.login(smtp_username, smtp_password)
                        server.send_message(msg)

                logger.info(f"Successfully sent OTP email to {to_email} via SMTP.")
                return True
            except Exception as e:
                logger.error(f"Failed to send email via SMTP: {e}")

        # 4. Development Fallback Logger
        logger.warning(f"[MOCK MAIL FALLBACK] To: {to_email} | Purpose: {purpose} | Code: {code}")
        print("\n" + "="*50)
        print(f"[STHX Technologies BREVO EMAIL MOCK] To: {to_email}")
        print(f"Subject: {subject}")
        print(f"Verification Code: {code}")
        print("="*50 + "\n")
        return False

    @staticmethod
    def send_verification_code(to_email: str, code: str) -> bool:
        """Backward compatible helper method."""
        return EmailService.send_otp_email(to_email, code, purpose="EMAIL_VERIFICATION")

    @staticmethod
    def send_password_reset_email(to_email: str, name: str, reset_url: str) -> bool:
        """
        Sends a Password Reset Link email via Brevo Email API (https://api.brevo.com/v3/smtp/email).
        Reads config from environment variables:
        - BREVO_API_KEY
        - EMAIL_FROM (default: tahah2269@gmail.com)
        - EMAIL_FROM_NAME (default: STHX Technologies)
        """
        brevo_api_key = os.getenv("BREVO_API_KEY", "")
        email_from = os.getenv("EMAIL_FROM", "tahah2269@gmail.com")
        email_from_name = os.getenv("EMAIL_FROM_NAME", "STHX Technologies")

        subject = "Reset Your Password - STHX Technologies"
        title = "Password Reset Request"
        recipient_name = name or "User"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8">
          <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #050B14; color: #E2E8F0; margin: 0; padding: 40px 20px; }}
            .container {{ max-width: 540px; margin: 0 auto; background: #0F172A; border: 1px solid #1E293B; border-radius: 20px; overflow: hidden; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5); }}
            .header {{ background: linear-gradient(135deg, #0066FF 0%, #00F0FF 100%); padding: 30px 20px; text-align: center; color: #FFFFFF; }}
            .header h1 {{ margin: 0; font-size: 26px; font-weight: 900; letter-spacing: 2px; text-transform: uppercase; }}
            .content {{ padding: 32px 28px; text-align: center; }}
            .title {{ font-size: 20px; font-weight: 700; color: #FFFFFF; margin-top: 0; margin-bottom: 12px; }}
            .text {{ font-size: 14px; color: #94A3B8; line-height: 1.6; margin-bottom: 24px; }}
            .btn {{ background: linear-gradient(135deg, #0066FF 0%, #00F0FF 100%); color: #FFFFFF !important; font-size: 15px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 12px; display: inline-block; margin: 20px 0; letter-spacing: 0.5px; box-shadow: 0 4px 14px rgba(0, 102, 255, 0.4); }}
            .notice {{ font-size: 12px; font-weight: 600; color: #F59E0B; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2); padding: 10px 16px; border-radius: 10px; margin-top: 20px; display: inline-block; }}
            .link-text {{ font-size: 11px; color: #64748B; word-break: break-all; margin-top: 15px; }}
            .footer {{ background: #090E17; border-top: 1px solid #1E293B; padding: 20px; text-align: center; font-size: 11px; color: #64748B; }}
          </style>
        </head>
        <body>
          <div class="container">
            <div class="header">
              <h1>STHX Technologies</h1>
              <p style="margin: 4px 0 0 0; font-size: 12px; opacity: 0.9;">Gym Management System</p>
            </div>
            <div class="content">
              <h2 class="title">{title}</h2>
              <p class="text">Hello {recipient_name},<br>We received a request to reset your password for your STHX Technologies account. Click the button below to set a new password:</p>
              
              <a href="{reset_url}" class="btn" target="_blank">Reset Password</a>
              
              <div>
                <span class="notice">⏱️ This link is valid for 1 hour and can only be used once.</span>
              </div>
              
              <p class="link-text">If the button doesn't work, copy and paste this link into your browser:<br><a href="{reset_url}" style="color: #00F0FF;">{reset_url}</a></p>

              <p style="font-size: 12px; color: #64748B; margin-top: 28px;">If you did not request a password reset, please safely ignore this email.</p>
            </div>
            <div class="footer">
              &copy; STHX Technologies Gym Management System. All rights reserved.
            </div>
          </div>
        </body>
        </html>
        """

        if brevo_api_key:
            try:
                res = requests.post(
                    "https://api.brevo.com/v3/smtp/email",
                    headers={
                        "api-key": brevo_api_key,
                        "Content-Type": "application/json"
                    },
                    json={
                        "sender": {
                            "name": email_from_name,
                            "email": email_from
                        },
                        "to": [
                            {
                                "email": to_email,
                                "name": recipient_name
                            }
                        ],
                        "subject": subject,
                        "htmlContent": html_content
                    },
                    timeout=10
                )
                if res.status_code in [200, 201]:
                    logger.info("Successfully sent password reset email via Brevo API.")
                    return True
                else:
                    logger.warning(f"Brevo API returned status {res.status_code}")
            except Exception as e:
                logger.error(f"Failed to send password reset email via Brevo API: {e}")

        logger.warning("[MOCK MAIL FALLBACK] Password reset email dispatch requested.")
        return False

