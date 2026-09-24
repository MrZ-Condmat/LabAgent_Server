"""Explicit, one-time bootstrap of the first administrator account."""

import argparse

from lab_agent.auth.admin_users import AdminBootstrapError, BootstrapAdminService
from lab_agent.auth.email_otp import EmailDomainNotAllowedError
from lab_agent.db.session import database_session
from lab_agent.repositories.admin_users import AdminUserRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lab_agent.admin")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin", help="Promote an existing user only if no active admin exists")
    bootstrap.add_argument("email", help="School email of a user who already completed OTP login")
    args = parser.parse_args(argv)

    try:
        with database_session() as session:
            result = BootstrapAdminService(AdminUserRepository(session)).bootstrap_first_admin(args.email)
    except (AdminBootstrapError, EmailDomainNotAllowedError) as exc:
        parser.exit(2, f"Bootstrap rejected: {exc}\n")

    print(f"Bootstrap administrator created: {result.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
