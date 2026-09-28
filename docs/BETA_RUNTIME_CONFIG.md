# Long-term Beta Runtime Configuration

LabAgent application processes use one server-only runtime file. This removes
their dependency on temporary variables exported in an interactive SSH shell.

## Directory and responsibility boundary

```text
/data/zmr/projects/
├── labAgent_Server/
├── labagent_runtime/
│   ├── postgres.env
│   └── labagent.env
└── labagent_backups/
    └── postgres/
```

- `postgres.env` configures the PostgreSQL container and A1/A2 lifecycle,
  backup, and restore scripts. Application code never loads this file.
- `labagent.env` configures FastAPI, Streamlit, daily reports, authentication,
  SMTP, database access, and LLM clients.

The canonical application path is:

```text
/data/zmr/projects/labagent_runtime/labagent.env
```

Create it manually on the server from `config/labagent.env.example`, replace
every placeholder, and restrict access:

```bash
install -d -m 700 /data/zmr/projects/labagent_runtime
install -m 600 config/labagent.env.example \
  /data/zmr/projects/labagent_runtime/labagent.env
chmod 600 /data/zmr/projects/labagent_runtime/labagent.env
```

Never commit, upload, print, or paste the populated file into logs or support
messages.

## Selection and precedence

Select the external file for a process with:

```bash
export LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env
```

The dotenv file selection order is:

```text
explicit config_path argument
    > LABAGENT_ENV_FILE
    > <project root>/.env
```

An explicitly configured `LABAGENT_ENV_FILE` that is missing causes startup to
fail with its path. It never silently falls back to the project `.env`.
Without `LABAGENT_ENV_FILE`, local development continues to use the project
`.env`, including on Windows.

Within the selected file, python-dotenv uses `override=False`:

```text
existing process environment value > selected dotenv value
```

This permits a deliberate process-level override without allowing dotenv to
replace an already supplied environment value.

## Validation

Run the same dotenv selection logic used by the application:

```bash
LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env \
  /home/zmr/miniforge3/bin/conda run -n labagent_server \
  python scripts/validate_runtime_config.py
```

The validator prints only the selected path and `SET`, `MISSING`, or `INVALID`
for each field. It never prints a database URL, password, HMAC secret, email
credential, or API key. It performs no database, SMTP, or LLM connection.

Long-term Beta validation requires:

- PostgreSQL `DATABASE_URL` with a PostgreSQL-compatible scheme.
- Allowed email domains, OTP and session HMAC secrets, cookie name and secure
  setting.
- FastAPI and Streamlit public HTTP(S) URLs.
- SMTP host, port, username, password, sender, and SSL setting.
- Separate DeepSeek chat and scoring keys plus base URL.
- Timezone and Streamlit bind host/port.

It rejects known placeholders, HMAC secrets shorter than 32 characters,
invalid ports, unsupported boolean values, non-HTTP(S) public URLs, invalid
timezone names, and non-PostgreSQL database URLs.

## Shared-process requirement

FastAPI and Streamlit must receive the same `LABAGENT_ENV_FILE`. In particular,
they must share:

```text
AUTH_SESSION_HMAC_SECRET
DATABASE_URL
AUTH_COOKIE_NAME
```

Otherwise FastAPI can create a session that Streamlit cannot validate. Daily
report processes should use the same file so database, LLM, timezone, and path
configuration remain consistent.

`scripts/run_web_app.sh` resolves `STREAMLIT_HOST` and `STREAMLIT_PORT` through
the same Python `Config` loader before starting Streamlit. It does not source
the env file as shell code. FastAPI request dependencies and the daily report
entry point already construct `Config` or call `load_project_dotenv()`, so they
inherit the same selection behavior.

## Future systemd integration

Beta Task B2 can set the canonical path for both services, for example through
a systemd `Environment=LABAGENT_ENV_FILE=...` entry. B1 does not install or
modify service units. The application reads the selected file itself, so an
interactive SSH shell and temporary `export` commands are not required during
normal service operation.
