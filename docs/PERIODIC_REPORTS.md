# Priority 3 周报与月报

## 周期与调度

- 周报每周五日报结束后生成，范围为上周六至本周五，包含周五。
  例如 `2026-11-13` 的周报覆盖 `2026-11-07` 至 `2026-11-13`。
- 月报每月 1 日日报结束后生成，范围为上个月完整自然月。
  例如 `2026-12-01` 生成 `2026-11` 月报；支持闰年及跨年。
- 周五恰好是 1 日时，会分别生成两个报告。
- 复用现有 Cron → run_daily_report.sh → generate_daily_report.py 入口和时区配置。
  不新增 Cron/systemd timer，也不改变现有日报触发时间（当前北京时间 10:00）。
  生成时间取决于日报执行完成时间，不保证恰好 10:00 完成。
- 日报抛出异常时，finally 中仍尝试汇总已经保存的日报；不吞掉原始日报异常。
  不完整的周期报告会明确标记，正常结束路径返回非零状态，便于日志排查。
- `--skip-periodic-reports` 可以跳过本次日报的周期汇总。错过计划运行的日期，
  应通过管理员页面或下方 CLI 补生成，当前不提供自动补跑/重新评分。

## 数据范围与去重

只读取 `reports/ArXiv_reports/YYYY-MM-DD.json` 和
`reports/Journal_Summary_reports/YYYY-MM-DD.json` 中 score=3 的文章。
不调用 LLM、不抓取 RSS/arXiv、不重新评分、不改写原日报，不写数据库。
报告保留原始文章摘要、评分理由和关键词，属于精选文章集合，不额外生成 AI 综述。

**周期按来源日报日期归属，不按文章最初发表日期。**期刊日报可能包含当日和前一日
的 RSS 更新，论文发表日期也可能更早；UI、HTML 和 JSON 均标明这一依据。

按 DOI、arXiv ID（忽略版本）、规范 URL、其他 ID/标题逐级去重，保留该篇出现过的
日报日期与来源。不同记录标识不一致时，不能保证识别所有跨平台重复文章，不使用
模糊标题匹配。跨来源相同 DOI 只展示一次；来源统计可以同时包含这篇，因此两个
来源计数之和可能大于唯一文章数。重复出现而评分曾变化的文章，只要本周期任一日报
将其评为 3，就会入选；重建时以当前保存的日报为准。

缺失、损坏、日期不符、部分期刊缺失或存在已知评分失败的来源会记录为 coverage
issue，页面明显提示计数可能不完整。缺失的周末 ArXiv 日报按既有跳过规则处理，
不因此标记缺失；周末文件如实际存在，仍会纳入。工作日没有文件（包括节假日未更新）
会提示缺失，当前没有额外的 arXiv 节假日历。空的有效来源报告可以表示当天零文章。

## 文件与前端

```text
reports/
├── Weekly_reports/YYYY-MM-DD.json   # 周五的日期
├── Weekly_reports/YYYY-MM-DD.html
├── Monthly_reports/YYYY-MM.json
└── Monthly_reports/YYYY-MM.html
```

这些目录位于现有 shared reports 内，跟随 release 切换保留。逐文件临时写入后原子替换，
JSON 是页面的数据源；页面下载的 HTML 从同一份 JSON 渲染，避免读取旧 HTML。
重建同一周期会更新该周期快照，不自动重建受补跑影响的其他周期。

侧栏新增 **Weekly Reports** / **Monthly Reports**；Overview 提供快捷入口。
登录用户共享查看，支持历史周期选择、来源筛选、搜索、25 篇分页、摘要展开和 HTML/JSON
下载。只有 admin 可在页面补生成/重建，普通用户不显示生成控件。不会新增聊天类型或
修改私人聊天历史。初次部署没有周期文件时页面显示说明，可先生成历史周期。

## 运维 CLI（不需要 LLM/API Key）

使用与日报相同的外部 runtime 文件，命令只读来源日报、写入对应周/月报：

```bash
cd /data/zmr/projects/labAgent_Server
export LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env

# 按当前日期生成到期报告；非周五且非 1 日时不生成
/data/zmr/conda_envs/labagent_server/bin/python scripts/generate_periodic_reports.py

# 指定某个周五补生成对应周报
/data/zmr/conda_envs/labagent_server/bin/python scripts/generate_periodic_reports.py \
  --period weekly --run-date 2026-11-06

# 按下月 1 日作为运行日期，补生成 2026-10 月报
/data/zmr/conda_envs/labagent_server/bin/python scripts/generate_periodic_reports.py \
  --period monthly --run-date 2026-11-01
```

`--reports-root` 可显式指定模拟/其他报告根目录。退出码：完整报告或非到期日为 0，
输入错误、写入失败或来源不完整为 1。缺失来源不冒充完整报告：仍保存可查看的部分
报告和 coverage 状态。不要为补周/月报去重跑全部 LLM 评分；先处理具体缺失来源。

## 本地测试

```powershell
python -m pytest tests/test_periodic_reports.py tests/test_scoring_notifications.py tests/test_report_backfill.py -q
```

测试只使用临时模拟日报与 Streamlit AppTest；不连接真实 API、SMTP 或生产数据库。
上线验收应检查：普通用户可查看但不能生成、admin 可补生成历史周期、日期与计数匹配、
缺失数据提示、筛选/下载及周五/1 日日报日志中的周期汇总记录。
