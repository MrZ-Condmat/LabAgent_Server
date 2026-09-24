"""Explicit manual SMTP transport check; does not create a DB challenge."""

import os
import sys
from datetime import timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lab_agent.auth.email_otp import EmailDomainNotAllowedError, EmailDomainPolicy, OtpCrypto  # noqa: E402
from lab_agent.auth.email_sender import EmailDeliveryError  # noqa: E402
from lab_agent.auth.smtp_sender import SmtpEmailSender  # noqa: E402
from lab_agent.db.models.user import utc_now  # noqa: E402
from lab_agent.utils.config import Config  # noqa: E402


def main() -> int:
    config = Config()
    try:
        recipient = EmailDomainPolicy(config.allowed_email_domains).normalize(
            os.getenv("SMTP_TEST_RECIPIENT", "")
        )
        sender = SmtpEmailSender(config)
        code = OtpCrypto.generate_code()
        sender.send_verification_code(
            recipient=recipient,
            code=code,
            expires_at=utc_now() + timedelta(seconds=config.auth_otp_ttl_seconds),
        )
    except (EmailDomainNotAllowedError, EmailDeliveryError, ValueError):
        print("SMTP smoke test failed; check recipient and SMTP configuration.", file=sys.stderr)
        return 1
    print(f"SMTP smoke test succeeded; host={config.smtp_host}, recipient_domain={recipient.rsplit('@', 1)[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
