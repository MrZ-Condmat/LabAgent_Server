# PostgreSQL Integration Tests

> **DANGER — DESTRUCTIVE TESTS**
>
> `TEST_DATABASE_URL` 指向的数据库会被清空，并会执行 Alembic
> `upgrade head`、`downgrade base` 和再次 `upgrade head`。它绝不能指向生产数据库、
> 服务器正式数据库或任何包含重要数据的数据库。

## Safety rules

- 集成测试只读取 `TEST_DATABASE_URL`，不会回退到 `DATABASE_URL`。
- 数据库必须是 PostgreSQL。
- 数据库名称必须包含 `test` 或 `integration`。
- `postgres`、`template0`、`template1` 和正式名称 `labagent` 会被拒绝。
- 未设置 `TEST_DATABASE_URL` 时，集成测试会安全跳过。

## Optional Docker test database

仓库提供 [compose.integration.yml](../compose.integration.yml)，只绑定本机
`127.0.0.1:55432`，并使用临时内存存储，不创建持久化数据卷。

启动 Docker Desktop 后，在项目目录执行：

```powershell
docker compose -f compose.integration.yml up -d
$env:TEST_DATABASE_URL = "postgresql+psycopg://labagent_test:test_password@localhost:55432/labagent_integration_test"
python -m pytest -m integration -q
docker compose -f compose.integration.yml down
```

这些用户名和密码只属于本机 disposable test container。

## Test commands

运行集成测试：

```powershell
python -m pytest -m integration -q
```

运行普通离线测试：

```powershell
python -m pytest tests -m "not integration" -q
```

运行全部测试时，如果没有配置测试数据库，integration tests 会显示为 skipped：

```powershell
python -m pytest tests -q
```

不要将真实凭据写入 `.env.example` 或提交到 Git。
