# 调用费用记录

`python3 -m verifin.costs` 从已经保存的 `model_call` 日志和显式价格表生成估算，不联网。它不改变 API 账户预算，也不提前授权任何支出。

```bash
python3 -m verifin.costs \
  --events pilot/case-001/turn-01/events.json \
  --prices data/pricing/openai-standard-2026-11-06.json \
  --expected-attempts 8 \
  --output cost-ledger.json
```

将 events 路径改为实际导出的 JSON 数组，`--expected-attempts` 改为这些 events 覆盖范围内的 episode 预留调用总数。它可发现崩溃后未记录结果的尝试；不提供时完整性保持未知。输出文件必须不存在。Pilot 的具体目录见 [运行说明](PILOT_AND_REVIEW_TOOLS.md)。

价格表记录查阅日期、币种、精确模型、endpoint、服务档位、短/长上下文费率和来源。本轮提供的三个 OpenAI 候选价格来自 [官方价格页](https://developers.openai.com/api/docs/pricing)，查阅日期 2026-11-06。没有自动应用价格，也不为未列模型猜价。它不是账号账单，排除税费、区域附加费、折扣、托管工具、存储、数据供应商和本机运算。

核算规则：

- 普通输入 = 总输入 − 缓存读取 − 缓存写入；写入按 30 分钟/12 小时的实际报告分别计费。
- 推理 token 已含在输出 token 中，绝不额外加一次。
- 失败调用若返回 usage，仍纳入估算；网络失败或缺报保持 unknown，不能等同免费。
- 缓存写入数大于零却没有生命周期分项时，费用 unknown，不能默认为更便宜的档位。
- 超过指定输入阈值时，对整个调用使用对应长上下文费率。
- endpoint、服务档位或返回模型不能匹配价格表时拒绝估算；脚本 fixture 从真实调用费用中排除。
- `priced_calls_subtotal` 仅为已能估算部分；`estimated_total` 只有完整时才有值。已知 token 本身不足以保证金额已知。

缓存分项公式依据 [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching#monitor-cache-performance)。E1 `summarize_resources` 也接受该 catalog 并使用保存的逐次调用事件。旧两费率表默认仅输出算术参考；只有显式 `pricing_scheme: flat_rate` 且不涉及 Responses/tier/cache 时才计为估算，不能称为实际账单。
