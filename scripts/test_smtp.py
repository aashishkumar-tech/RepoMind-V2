"""
Quick test: verify Gmail App Password works.
Uses MIMEText with UTF-8 encoding to support emojis.
"""
import smtplib
import ssl
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── EDIT THESE ───────────────────────────────────────
GMAIL_USER = "aashishkumar.tech@gmail.com"          # ← your Gmail address
APP_PASSWORD = "hativfyckysqtjls"            # ← 16-char App Password (NO spaces)
TO_ADDR = "aashishkumar71013@gmail.com" 

# ──────────────────────────────────────────────────────


def main():
    print(f"📧 Testing Gmail SMTP for {GMAIL_USER}...")
    try:
        # Build a proper MIME message with UTF-8 support
        msg = MIMEMultipart()
        msg["From"] = GMAIL_USER
        msg["To"] = TO_ADDR
        msg["Subject"] = "RepoMind SMTP Test ✅"

        body = (
            "✅ Gmail App Password works!\n\n"
            "Your RepoMind notifier is ready to send alerts for:\n"
            "  🚨 CI failures\n"
            "  🔔 PRs awaiting review\n"
            "  ✅ Successful merges\n"
            "  ❌ Rejected fixes\n"
            "  ⚠️ Rollbacks\n\n"
            "— RepoMind\n"
        )
        msg.attach(MIMEText(body, "plain", "utf-8"))

        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as s:
            s.starttls(context=ctx)
            s.login(GMAIL_USER, APP_PASSWORD)
            print("✅ Login successful")
            s.send_message(msg)
            print(f"✅ Test email sent to {TO_ADDR} — check your inbox!")
        return 0

    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ Auth failed: {e}")
        return 1
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())