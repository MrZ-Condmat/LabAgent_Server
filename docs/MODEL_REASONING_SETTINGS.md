# 模型思考程度与 token 预算

`lab_agent/config/models.json` 中分别配置：

| 用途 | reasoningEffort | maxTokens |
| --- | --- | --- |
| 文章评分 | high | 16384 |
| Overview / ArXiv / Journal 聊天 | max | 无此配置项，请求中也省略 |

三处聊天和评分复用 `thinking_request_options`，显式传入
`reasoning_effort` 和 `extra_body={"thinking":{"type":"enabled"}}`。
聊天即使读到旧配置中的 maxTokens，也不会发送这个限制或回退到 1500。
参数错误会明确失败，不会静默移除思考档位重试。

## 官方资料与 temperature

查阅日期：2026-11-10。

- [DeepSeek V4.1 官方模型卡](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash)
  的权重推理 sampling 推荐 temperature=1.0，top_p=0.95 或 1.0。
  其大输出预算建议服务于通用推理/评测，不直接作为每日论文评分预算。
- [官方思考模式 API](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)
  明确说明 temperature 在思考模式不生效，因此本项目省略 temperature。
- [官方 Chat Completions](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)
  支持 low/high/max；不传输出限制时，思考模式默认 64K，max 默认 128K。

以上默认值属于 DeepSeek 官方 API。项目实际使用的并行科技平台可能有不同
转发、输出预算或采样规则。离线 SDK 请求测试只能证明客户端确实发送参数，
不能证明平台已按该档位执行。上线前应另做小规模、受控的真实接口验证，
比较参数是否接受、返回 usage、finish_reason 和延迟；单凭 HTTP 200 或一次
回答长短不能证明思考档位生效。本次实现未调用真实 LLM 或修改服务器。

## 评分为什么保留上限

原来的 5000 预算在 high 思考时可能不足以同时容纳思考与最终结构化评分。
本次提高到 16384，作为批量评分的工程起点；这不是官方针对论文评分的推荐，
也不是已经测得的最佳值。后续应按真实 token 用量和截断率调整 JSON 中的值。
高预算是允许的上限，不会强制每篇都生成这么多 token。

若 `finish_reason=length`，本次评分被判定失败并沿用已有 fallback/通知逻辑，
不接受截断的部分评分，也不自动重复同一长度预算。空最终答案也会报错。
其他错误保留已有最多两次尝试；OpenAI SDK 自动重试仍为 0。

## 取消聊天限制意味着什么

省略 max_tokens 会使用平台默认预算，并非真正无限生成。
本项目一次聊天提交只调用一次 completion，没有无限 agent/tool 调用循环。
但模型仍可能在单次请求内重复或过度思考，耗到平台预算，增加费用和延迟。
评分批量运行时若也省略限制，会扩大每篇的潜在成本，因此评分保留硬预算。

目前评分、聊天的客户端 timeout 继续为 60 秒。它不是 token/费用上限：
客户端超时不能证明服务端已停止生成或停止计费，手动/自动重试可能重复消耗。
max 聊天可能更容易超过现有等待时间；应通过受控真实测试后单独决定是否调整
timeout 和平台级消费额度。本次未扩大超时或增加自动重试。

## 本地验证

```powershell
python -m pytest tests/test_model_thinking_settings.py tests/test_scoring_notifications.py tests/test_report_backfill.py -q
```

使用 httpx MockTransport 检查实际 OpenAI SDK 序列化字段，无网络请求、无真实密钥，
覆盖两个论文聊天类、Overview 共用参数、长度截断/空回复、既有告警和补跑回归。
