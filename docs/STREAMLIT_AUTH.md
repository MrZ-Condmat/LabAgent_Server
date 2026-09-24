# Streamlit and FastAPI Email OTP sign-in

Task 14 connects the existing Streamlit pages to the FastAPI auth gateway. The
gateway owns login pages and browser `Set-Cookie` responses; Streamlit only
reads the cookie from `st.context.cookies` and validates it against PostgreSQL
through `SessionService` on every script rerun. `st.session_state` remains UI
state, not an authentication authority.

## Local manual test

Install the updated requirements and configure a disposable/development
PostgreSQL database with the existing Alembic migrations, plus the required
OTP, Session and SMTP secrets in your private environment. Do not use the
production database for testing.

For local **HTTP** testing in both terminals, use the same hostname:

```bash
export AUTH_COOKIE_SECURE=false
export LABAGENT_AUTH_GATEWAY_PUBLIC_URL=http://localhost:8000
export LABAGENT_STREAMLIT_PUBLIC_URL=http://localhost:8501
```

Terminal A:

```bash
uvicorn lab_agent.api.app:app --host 127.0.0.1 --port 8000
```

Terminal B:

```bash
streamlit run lab_agent/web/app.py --server.address 127.0.0.1 --server.port 8501
```

Then check the browser flow:

1. Open `http://localhost:8501`; existing LabAgent pages must be hidden.
2. Follow **Sign in** to `http://localhost:8000/auth/login` in the same tab.
3. Enter an allowed school email, request a code, then enter the six-digit
   code received by email.
4. After FastAPI sets the HttpOnly cookie, the browser fully navigates back to
   Streamlit. Overview, ArXiv Daily, Journal Daily, Database, Logs and existing
   chats should behave as before.
5. Refresh, open a new tab, and reconnect: a valid persistent UserSession
   should restore login without relying on Streamlit session state.
6. Follow **Logout**. The FastAPI logout page POSTs to `/auth/logout`, which
   revokes the database session and clears the browser cookie, then navigates
   back to Streamlit. Existing pages must again be hidden.

`st.context.cookies` is read-only and reflects cookies from the initial
Streamlit session request. A Streamlit rerun alone cannot pick up a cookie
newly set by FastAPI; the full browser navigation is required. The same
hostname is also required across ports: do not mix `localhost`, `127.0.0.1`
and a server IP. On an internal HTTP test server, use the same server IP for
both ports and explicitly set `AUTH_COOKIE_SECURE=false` only for that test.

For future HTTPS deployment behind a reverse proxy, both public URLs should
use the same HTTPS host, and `AUTH_COOKIE_SECURE=true` **must** be restored.
Task 14 does not configure Nginx or certificates. The Streamlit guard protects
the LabAgent interface; it does not secure the Dify iframe's own independent
URL. This task does not implement RBAC or persist chat history per user.
