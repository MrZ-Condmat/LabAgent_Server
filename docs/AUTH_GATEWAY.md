# FastAPI authentication gateway

Task 13 adds a JSON API and a host-only, HttpOnly browser session cookie. It
does not add a login page or change the Streamlit application.

## Routes

| Route | Purpose |
| --- | --- |
| `POST /auth/request-code` | Issue a challenge and send its code through SMTP; returns challenge ID and expiry only. |
| `POST /auth/verify-code` | Verify the code, create or reuse a User and UserSession, then set the cookie. |
| `GET /auth/me` | Validate the cookie against the persistent UserSession. |
| `POST /auth/logout` | Revoke the session and delete the cookie; safe to repeat. |
| `GET /healthz` | Process liveness only; does not access the database or SMTP. |

The raw session token appears only in `Set-Cookie`, never in JSON. The database
stores its HMAC digest. Cookies use `HttpOnly`, `SameSite=Lax`, `Path=/`, and no
`Domain` attribute. Their `Expires` and `Max-Age` derive from the actual
UserSession expiry. `AUTH_COOKIE_NAME` defaults to `labagent_session`.

## Running locally

Install the updated `requirements.txt` and configure the existing PostgreSQL,
OTP, session, and SMTP environment variables. Start the gateway from the
repository root:

```bash
uvicorn lab_agent.api.app:app --host 127.0.0.1 --port 8000
```

`AUTH_COOKIE_SECURE=true` is the safe default and **production HTTPS must use
Secure cookies**. For local or internal HTTP testing only, explicitly set
`AUTH_COOKIE_SECURE=false`; otherwise browsers will not send the cookie over
HTTP. FastAPI and Streamlit must use the same hostname when sharing the cookie:
`http://localhost:8000` with `http://localhost:8501` works, whereas mixing
`localhost` and `127.0.0.1` does not. The same host rule applies when using a
server IP for both ports. Exposing the gateway temporarily on another machine
requires `--host 0.0.0.0`; do not treat that as a production deployment.

## Intended production topology

```text
Browser --HTTPS--> Nginx -- /auth/* --> FastAPI 127.0.0.1:8000
                         \-- /* ------> Streamlit 127.0.0.1:8501
```

Nginx and HTTPS termination are future deployment work. Task 14 will connect
the Streamlit UI to this gateway and plans to read the cookie via
`st.context.cookies`; Streamlit currently has no login guard or cookie code.

The gateway has no wildcard CORS policy. `SameSite=Lax` and JSON requests are
the current protections for the small auth surface. Future authenticated
state-changing API endpoints must revisit CSRF protection.

Wrong OTP returns HTTP 401 **after** the request transaction commits its
failed-attempt increment. Successful verification commits OTP consumption,
User changes, and UserSession creation together before setting the response
cookie. SMTP failure rolls back request-code database changes. PostgreSQL and
SMTP cannot commit atomically; if mail sends but the database commit fails,
the user must request a new code.
