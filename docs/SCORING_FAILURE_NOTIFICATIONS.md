# 每日文章评分失败邮件通知

Cron 调用 `scripts/generate_daily_report.py` 时，会收集本次 ArXiv / Journal
评分的最终失败，在运行结束时发送一封汇总邮件；即使后续报告步骤抛出异常，
也会尝试通知已记录的评分失败。

发件与收件均使用服务器私有配置的 `SMTP_FROM`，认证使用现有 `SMTP_USERNAME`
和 `SMTP_PASSWORD`。传输复用已有 SMTP_SSL、证书验证、超时和异常映射，
不增加邮箱凭据，不自动发送测试邮件。

## 配置

现有 SMTP 配置完整时默认启用，可在外部 runtime file 中显式设置：

```dotenv
SCORING_FAILURE_NOTIFY_ENABLED=true
```

设置为 `false` 可以关闭。凭据继续保存在服务器外部
`/data/zmr/projects/labagent_runtime/labagent.env`，不能写进仓库。
下次定时日报使用新 release 的代码时生效。

## 计数与内容

- 沿用现有评分重试次数；一次失败但重试成功不会通知。
- 重试耗尽进入原有 fallback 的文章会记录为失败。
- 解析结果仍为 `Unable to parse response` 也会计入失败，保留既有评分结果。
- 正常的 Priority 1 不是评分失败。
- 邮件包含报告日期、失败总数、ArXiv/Journal 分类数量和最多 20 篇文章的
  公开标题/标识及错误类别，不包含原始异常、API 响应、密码或密钥。
- 没有失败就不发邮件。缓存读取没有重新评分时，不追溯旧报告中的失败。

## 去重与故障

`logs/scoring_notifications/<report-date>.sent` 表示该日报日期已经发过邮件。
同一报告日期再次运行不会重发；手动回填不同报告日期可各发送一封。
logs 是现有 release 共享目录，因此正常切换 release 不会丢失通知状态。

发送前用排他创建的 `<report-date>.sending` 文件防止并发重复发送。
发送失败只记录安全提示，不改变日报结果；之后再次运行可以重试通知。
SMTP 已成功但状态文件更新失败时保留 `.sending`，避免自动重发。
崩溃后留下 `.sending` 时，先核对收件箱和日志，再由操作员决定是否删除该日期
的 reservation 文件。普通 SMTP 无法提供严格 exactly-once：服务端收信后连接
中断等不确定结果，仍可能造成漏报或后续重试重复。

通知默认在一次日报运行完成后发出，因此邮件只汇总触发发送的那次运行。
同日报日期后续运行中新出现的失败不会再发送第二封。该功能不替代进程退出、
机器停机或评分器初始化失败的外部监控。

## 本地验证

```powershell
python -m pytest tests/test_scoring_notifications.py tests/test_smtp_email_sender.py -q
```

全部使用 fake/mocks，不连接 SMTP 或 LLM，不生成真实日报。
