# Task 16: persistent private chat

## Data boundary

- ArXiv and Journal reports remain shared files. They are the source of paper data.
- `conversations` and `messages` are private PostgreSQL data. Every conversation belongs to one `User.id`; an admin has no access to another user's private history.
- Streamlit keeps only the active conversation ID, report selectors, and temporary Agent/Chat objects. On every rerun, the active ID is checked against `CurrentUser.id` in SQL.
- The existing `conversation_type` values `overview`, `arxiv`, and `journal` distinguish the three workspaces. `context_metadata` stores only the report date and, for Journal, summary or journal slug/name. It never copies report papers or stores credentials.
- System and paper context prompts are built at runtime. Only visible user and assistant turns are saved. A New Conversation begins an empty draft and leaves older database records intact.
- Sending commits the user turn first, closes that database transaction, calls the model, and then commits the assistant turn in a second transaction. A model error leaves the user turn in history with no fabricated assistant turn.
- No schema change was needed. Alembic head remains `0004_email_otp_auth_foundation`.

## Manual server acceptance

Use a test account and avoid private research content in test prompts. Confirm the production database has been migrated to the existing head before running the updated Streamlit app.

1. Log in, open **Overview**, send two or three turns, and press F5. The turns should remain. Click **New Conversation**, send another turn, then switch between both entries under **Recent Conversations**.
2. Log out and log in with a new OTP. Both Overview conversations should still be listed. Restarting Streamlit and logging in again should give the same result.
3. In **ArXiv Daily**, choose an available dated report, start a chat, refresh, and reopen it. Change the calendar date and send a new message: the old conversation must retain its original report date. Reopen the old entry and verify its displayed date and answers use the original papers.
4. In **Journal Daily**, test both a **Summary** and a specific journal with an available dated report. Refresh and log in again, then reopen each entry. Its journal slug/date or summary/date must remain fixed even after changing the page selector.
5. Temporarily make a test report unavailable. Its old chat should remain readable, show a context warning, and disable sending until the report is available again. Do not remove production reports for this test.
6. Log in as another test user. The first user's entries must be absent. Repeat with an admin account: admin can see only its own private chats.
7. If a test LLM call fails, refresh: the user message should remain, with no assistant error text saved as a reply. A later message can continue the conversation.

The **Recent Conversations** list shows at most 20 entries per workspace, ordered by last message time. A fresh browser session opens the most recent conversation; any older entry can be opened from the list.

Automated PostgreSQL tests use only the disposable `compose.integration.yml` database and `TEST_DATABASE_URL`; see [POSTGRES_INTEGRATION_TESTS.md](POSTGRES_INTEGRATION_TESTS.md). They never contact DeepSeek or SMTP.
