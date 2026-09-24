# Manual SMTP OTP transport test

Task 11 uses SMTP over verified implicit TLS on port 465. The explicit smoke
script sends a generated six-digit test code but **does not create a database
challenge**. The code in that test email cannot be used to log in. Normal
application delivery must call `EmailOtpDeliveryService` inside a caller-owned
database transaction.

On the Ubuntu server, from the repository directory, supply credentials only
in the current shell and run the script manually:

```bash
export SMTP_HOST='mails.tsinghua.edu.cn'
export SMTP_PORT='465'
export SMTP_USERNAME='<full school email address>'
read -rs SMTP_PASSWORD
export SMTP_PASSWORD
export SMTP_FROM='<full school email address>'
export SMTP_TEST_RECIPIENT='<allowed test recipient>'
python scripts/smtp_otp_smoke_test.py
unset SMTP_PASSWORD SMTP_USERNAME SMTP_FROM SMTP_TEST_RECIPIENT
```

The recipient must belong to an allowed school domain. The script prints the
host, recipient domain and success/failure only; it never prints the password
or code. Do not place credentials in a shell command, checked-in file, or
pytest configuration. This script is never invoked by pytest.

SMTP delivery and PostgreSQL commit are separate operations. The delivery
service sends before the caller commits: a send failure should cause the
caller to roll back, preserving the previous challenge. If delivery succeeds
but commit fails, the received code will be unusable; request another code.
