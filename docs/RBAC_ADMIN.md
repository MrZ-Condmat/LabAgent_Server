# RBAC and administrator user management

LabAgent has two roles: `user` and `admin`. A first successful school-email OTP
login creates an active `user`. Existing users keep their role on subsequent
logins. An administrator can manage users and access the Admin page; the role
does not grant access to another user's private conversations or messages.

## Bootstrap the first administrator

The person must first sign in with Email OTP so that their User row exists.
Then, from the Ubuntu project root, the server maintainer runs:

```bash
cd /data/zmr/projects/labAgent_Server
/home/zmr/miniforge3/bin/conda run -n labagent_server \
  python -m lab_agent.admin bootstrap-admin '<existing-school-email>'
```

The CLI uses the same email normalization and domain policy as OTP login. It
does not create a User, session, or OTP challenge. It rejects an inactive or
missing user and refuses to run once any active administrator exists. After
bootstrap, role and active-state changes must be made by a signed-in admin.
No administrator email is hardcoded.

## Admin operations

The Streamlit Admin page is shown only to active administrators. It lists email,
display name, role, active state, creation time and last login, and provides
confirmed Promote, Demote, Enable and Disable actions. The FastAPI gateway also
exposes `GET /admin/users`, `PATCH /admin/users/{user_id}/role`, and
`PATCH /admin/users/{user_id}/active`. These endpoints use the existing opaque
HttpOnly session cookie; request bodies contain only the target change.

Both the API and service layer check the session-derived `CurrentUser`. Before
mutating users, the service takes a PostgreSQL transaction-scoped advisory lock,
then reloads the actor and target under row locks. This serializes bootstrap,
role changes and active-state changes. The service rejects self-demotion,
self-disablement and removal of the last active administrator. Reusing an old
cookie does not restore an outdated role: each request/rerun resolves the User
from PostgreSQL, and the service rechecks the database row.

Administrator user management does not change ConversationRepository or
MessageRepository ownership checks. Disabling a user makes their existing
session invalid on its next validation. No new migration or environment
variable is required; Alembic head remains `0004_email_otp_auth_foundation`.
