# Persistent PostgreSQL for the Long-term Beta

This infrastructure is separate from the disposable integration-test database.

| Purpose | Compose file | Storage | Image |
| --- | --- | --- | --- |
| Offline integration tests | `compose.integration.yml` | `tmpfs`, disposable | `postgres:16` |
| Long-term Beta | `compose.beta-postgres.yml` | named volume `labagent_postgres_data` | `postgres:15-alpine` |

The Beta service is named `postgres-beta`, its stable container name is
`labagent-postgres`, and it binds PostgreSQL only to
`127.0.0.1:${LABAGENT_POSTGRES_PORT:-55432}`. It uses
`restart: unless-stopped` and never pulls an image automatically.

## Server-only configuration

Keep the real PostgreSQL environment file outside the deployed source tree:

```bash
sudo install -d -m 700 -o zmr -g zmr /data/zmr/labagent_runtime
install -m 600 config/postgres.env.example /data/zmr/labagent_runtime/postgres.env
```

Edit `/data/zmr/labagent_runtime/postgres.env` and replace `CHANGE_ME` with a
strong, unique database password:

```dotenv
POSTGRES_DB=labagent
POSTGRES_USER=labagent
POSTGRES_PASSWORD=CHANGE_ME
LABAGENT_POSTGRES_PORT=55432
```

Confirm its permissions without displaying its contents:

```bash
stat -c '%A %a %U:%G %n' /data/zmr/labagent_runtime/postgres.env
```

The file must remain server-only and should have mode `600`. Do not copy it
into the Git checkout, deployment archive, shell history, or logs. The
management script uses this path by default. To use another path for a single
command:

```bash
LABAGENT_POSTGRES_ENV_FILE=/secure/path/postgres.env \
  bash scripts/manage_beta_postgres.sh status
```

The application `.env` needs a matching `DATABASE_URL`. Its password component
must be URL encoded when it contains reserved URL characters:

```dotenv
DATABASE_URL=postgresql+psycopg://labagent:<URL_ENCODED_PASSWORD>@127.0.0.1:55432/labagent
```

This task does not create or modify either server file automatically.

## Lifecycle commands

First confirm that the required image already exists locally. The server does
not need Docker Hub access:

```bash
docker image inspect postgres:15-alpine >/dev/null
```

Then manage the database from the deployed project directory:

```bash
bash scripts/manage_beta_postgres.sh start
bash scripts/manage_beta_postgres.sh health
bash scripts/manage_beta_postgres.sh status
bash scripts/manage_beta_postgres.sh logs
bash scripts/manage_beta_postgres.sh restart
bash scripts/manage_beta_postgres.sh stop
```

`start` validates Docker, Compose, the local image, and required configuration,
then waits for the `pg_isready` health check. `stop` stops the service without
deleting the container or volume. The script intentionally has no destroy or
reset command.

Do not run this against the Beta database:

```text
docker compose down -v
docker volume rm labagent_postgres_data
```

Those commands can delete the long-term database files. A normal Compose
`down` does not remove this named volume unless `-v` is explicitly supplied,
but the lifecycle script uses `stop` to make the safe operation unambiguous.

## Initial Alembic provisioning

The first database is empty. After PostgreSQL is healthy, configure
`DATABASE_URL` for the Beta database and run the existing migrations:

```bash
export DATABASE_URL='postgresql+psycopg://labagent:<URL_ENCODED_PASSWORD>@127.0.0.1:55432/labagent'
/home/zmr/miniforge3/bin/conda run -n labagent_server alembic upgrade head
/home/zmr/miniforge3/bin/conda run -n labagent_server alembic current
unset DATABASE_URL
```

The expected head is `0004_email_otp_auth_foundation`. Use Alembic for schema
creation; do not replace it with `Base.metadata.create_all()`.

## Persistence verification on the server

Perform this acceptance check using only disposable Beta test records:

1. Start the Beta database and confirm health.
2. Run `alembic upgrade head` as shown above.
3. Through LabAgent, create a test user, one conversation, and messages. Avoid
   reusing production identities if this is a prelaunch exercise.
4. Record the row counts without including message content:

   ```bash
   docker exec labagent-postgres psql -U labagent -d labagent -c \
     'SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM conversations) AS conversations, (SELECT count(*) FROM messages) AS messages;'
   ```

5. Confirm the stable volume exists:

   ```bash
   docker volume inspect labagent_postgres_data
   ```

6. Stop and start PostgreSQL, then wait for health:

   ```bash
   bash scripts/manage_beta_postgres.sh stop
   bash scripts/manage_beta_postgres.sh start
   ```

7. Run the same count query and verify the counts and selected test records are
   unchanged. Confirm the conversation still belongs to the same user and its
   messages remain accessible through the application.

The `unless-stopped` policy causes the container to return when the Docker
daemon returns after a normal server reboot, unless an operator explicitly
stopped it. The actual reboot test belongs to the final Beta lifecycle E2E.

## Data and backup boundary

The named volume stores PostgreSQL physical data under Docker's persistent root
(`/data/tools/docker` on the target server). Future backups must use logical
PostgreSQL tools such as `pg_dump` and `pg_restore`; Beta Task A2 defines and
tests that workflow. This task does not migrate data from the disposable Task
16 test database and does not implement backup automation.
