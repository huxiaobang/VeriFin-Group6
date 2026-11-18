# VeriFin 主模型与 API 选型记录

日期：2026-11-06。**可以使用 OpenAI API；建议先以一个有能力的通用模型完成开发 pilot，再比较更省费用的候选。现在不应锁死 Qwen，也不应仅凭品牌宣布 OpenAI 更适合。** 本次没有真实 LLM 调用，因此下表是有依据的候选安排，不是 VeriFin 实测排名。

## 1. 原计划和当前决定

原 Proposal 的主 LLM 路线是云端 Qwen API，不是要求组员在笔记本上部署主模型；本地模型讨论主要涉及 embedding。`qwen3.7-plus` 与原快照 `qwen3.7-plus-2026-05-26` 确实出现在阿里云官方资料中。问题在于此前没有用本项目任务证明它应成为唯一选择，而不是该型号不存在。[阿里云模型列表](https://www.alibabacloud.com/help/en/model-studio/models)、[模型价格与快照](https://www.alibabacloud.com/help/en/model-studio/model-pricing)

实现保留 provider 可替换：兼容 Chat Completions 用于 Qwen 等供应商，OpenAI 默认使用 Responses。固定接口由本地控制器解释 Planner JSON 并执行工具。多轮状态、证据和报告仍由本地 SQLite 管理。[OpenAI Responses 迁移说明](https://developers.openai.com/api/docs/guides/migrate-to-responses)

**暂定选择：OpenAI 作为可用接入路线，主模型 ID 留空，等待实际账号可用型号与小规模 pilot；Qwen 保留为备选。** 接口接通、JSON 合法、财务语义正确是三个不同完成条件。

## 2. 候选和为何比较

| 候选 | 在 VeriFin 中的试验角色 | 选择依据与需要验证的风险 |
|---|---|---|
| GPT-6.1 Sol | 优先作为 OpenAI 开发 pilot 的均衡候选 | 官方定位为复杂专业工作中兼顾能力与成本；需要实测规划、财务口径、Critic 与多轮表现，不能拿官方通用定位代替效果证据 |
| GPT-6 Luna | 通过流程后进行低费用对照 | 官方面向聚焦、高吞吐任务；先看是否漏核验、误结束、频繁修复，不能只比较单 token 价格 |
| GPT-6 Astra | 少量困难案例的高能力参考 | 官方最强能力候选，价格较高；不默认把所有日常调用升级，也不把它的判定当人工真值 |
| Qwen3.7 Plus 固定快照 | 原方案及跨供应商候选 | 保留原研究路线，核实区域、JSON 模式和输出限制；在同一任务与来源下比较，避免没有实验就淘汰 |

前三项的角色依据来自当前官方 [模型家族说明](https://developers.openai.com/api/docs/guides/latest-model)、[Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)、[Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)。这里不使用官网营销描述来声称其在金融任务上优于另一个模型。账号权限及可调用 ID 尚未验证。

截至本次查阅，Standard 全球文本价格（USD / 百万 token，输入不超过 272K）为：

| 模型 | 普通输入 | 缓存读取 | 输出 |
|---|---:|---:|---:|
| GPT-6.1 Sol | 2.00 | 0.10 | 10.00 |
| GPT-6 Luna | 0.10 | 0.01 | 0.50 |
| GPT-6 Astra | 10.00 | 1.00 | 50.00 |

缓存写入另有费率，长上下文和其他服务档位也会改变费用；完整快照在 `data/pricing/openai-standard-2026-11-06.json`，只能显式用于匹配的运行。Qwen 价格依区域/部署模式与上下文区间变化，本记录不拿一个区域的报价代表所有账号。[OpenAI 当前价格](https://developers.openai.com/api/docs/pricing)

## 3. 比较需要观察什么

第一批使用现有开发任务与 replay，先在一个模型上检查以下行为，费用预算确认后才实际调用：

1. 财季、公司、指标、单位及截止时间是否解析正确；歧义时是否提出有用澄清。
2. Planner 是否会根据工具失败和 Critic 反馈调整工作，而不是重复相同检索或直接结束。
3. Researcher 是否仅使用已有来源和计算 ID；跨年、YTD、合并/分部与因果表达是否出错。
4. Critic 是否识别不支持的陈述和缺项；结构化检查不能只输出格式漂亮的空 issue 列表。
5. 追问、改期间、改公司、重开会话是否保持有效状态；旧结论失效后不能继续沿用其核验状态。
6. 每个任务的调用数、实际 input/output/cache/reasoning token、延迟、失败与最终缺口。

Schema 合法率、预算遵守、崩溃和工具记录可以自动检查；语义正确率与来源是否充分，不能由该模型自评自动变为正式 gold。先做自动开发诊断可以节省人工时间，之后仅将真正需要判断的材料交给组员。

选模型时比较**每个任务的实际总成本及失败率**：便宜模型如果需要反复修复，未必更省；贵模型如果没有可观察收益也不值得默认采用。选择之后冻结模型 ID、API 模式、reasoning effort、提示词、来源与预算，正式 E1 的两种 reviewer 使用相同模型设置。跨模型比较作为单独实验，不能混进 Critic 方案的因果结论。

## 4. 本次技术取舍

- 使用 Responses 的结构化文本输出入口，当前为 JSON-object mode＋本地 schema/ID 检查。未启用 strict JSON Schema；后者可降低部分格式错误，却不证明事实正确，也需要处理 Planner 各 action 的字段约束。[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- 未引入 provider 托管的自主搜索、函数调用或远端会话状态，以保留实验中相同的来源和工具接口；这是一项实验控制选择。主模型依然选择行动，由控制器执行并记录。
- 默认不自动重试。每次调用先预留预算；截断、拒绝、异常和 token 用量均保留。后续若增加重试，应作为预算内显式行动并报告成本。
- `max_output_tokens` 包含 reasoning tokens。暂定 4096 是可配置初值；真实 pilot 若因推理占满而截断，应报告并重新冻结设置，不能把空 JSON 当成功。
- 本地 tokenizer 的 context 控制尚为字符上限，不是供应商 token 精确估算；超限保存 partial，不静默丢原始证据。以后可基于实际模型加入准确 token 预算。
- `store=false` 用来控制 Responses 应用状态存储；不将它描述为供应商零保留。密钥只从本机环境读取，旧 provider endpoint 不会自动获得 OpenAI key。

Embedding 的选择是独立问题：主 LLM 负责规划/综合/语义复核，embedding 负责找到候选证据。二者不必同厂商，也不需要同时本地或同时云端。现阶段优先实测多语言本地候选；云端 embedding 可作为未来对照，但本轮没有为此增加付费调用。
