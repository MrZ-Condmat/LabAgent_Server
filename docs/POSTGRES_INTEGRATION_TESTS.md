# PostgreSQL Integration Tests

> **DANGER — DESTRUCTIVE TESTS**
>
> `TEST_DATABASE_URL` 指向的数据库会被清空，并会执行 Alembic
> `upgrade head`、`downgrade base` 和再次 `upgrade head`。它绝不能指向生产数据库、
> 服务器正式数据库或任何包含重要数据的数据库。

## Safety rules

- 集成测试只读取 `TEST_DATABASE_URL`，不会回退到 `DATABASE_URL`。
- 必须显式设置 `LABAGENT_ALLOW_DESTRUCTIVE_INTEGRATION_TESTS=1`，否则拒绝。
- URL 必须精确匹配 Compose 专用连接：PostgreSQL+psycopg、`127.0.0.1:55432`、
  database=`labagent_integration_test`、user=`labagent_test`、测试密码=`test_password`，禁止 query 参数。
- 拒绝 localhost、非白名单或远程主机、生产端口 55433、labagent_beta 及任意仅带 test 的数据库名。
- 每次连接 pool checkout 在测试 SQL 前，核验实际 current_database/current_user、内部端口 5432、
  PostgreSQL 16 和 `cluster_name=labagent-disposable-integration`。任何一项不符立即阻止破坏性操作。
- opt-in 不绕过 URL 或实际服务器身份校验；无标记的旧实例也被拒绝。
- 未设置 `TEST_DATABASE_URL` 时，集成测试会安全跳过。

## Optional Docker test database

仓库提供 [compose.integration.yml](../compose.integration.yml)，只绑定本机
`127.0.0.1:55432`，并使用临时内存存储，不创建持久化数据卷。

启动 Docker Desktop 后，在项目目录执行：

```powershell
docker compose -f compose.integration.yml up -d
docker compose -f compose.integration.yml ps
$env:LABAGENT_ALLOW_DESTRUCTIVE_INTEGRATION_TESTS = "1"
$env:TEST_DATABASE_URL = "postgresql+psycopg://labagent_test:test_password@127.0.0.1:55432/labagent_integration_test"
python -m pytest -m integration -q
docker compose -f compose.integration.yml down
Remove-Item Env:TEST_DATABASE_URL
Remove-Item Env:LABAGENT_ALLOW_DESTRUCTIVE_INTEGRATION_TESTS
```

这些用户名和密码只属于本机 disposable test container。

Compose 启动 PostgreSQL 时设置独立 cluster_name。迁移、DROP SCHEMA、TRUNCATE 和 C3 fixture
全部使用 guarded engine；Alembic 复用已核验的连接，不创建未受保护连接。
该机制防止误连，不是针对恶意伪造本地测试实例的密码学认证。若端口占用，先调查，禁止改用生产端口。
如需调整端口，必须同时审查 Compose、guard、测试与本文，不能通过 opt-in 扩大白名单。

C3 fixture 使用独立临时 schema 和临时 migration 文件，复用实际 alembic/env.py，验证另一连接可见的
持久化 DDL/revision、失败事务状态、锁竞争及释放、审计结果。不会新增生产 0005。
Docker 不可用时执行离线测试，明确报告 PostgreSQL 测试 skipped；禁止连接 Ubuntu 生产服务器替代测试。

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
