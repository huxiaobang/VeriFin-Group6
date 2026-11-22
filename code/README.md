# VeriFin · 开发阶段实现

本目录是基于 2026-10-09 最终 Proposal 的开发版本 **0.5.0（2026-11-06）**。已有研究控制器、多轮持久化、来源与计算工具、核验、本地界面及 E1/E2/E3 管线。本轮新增公开金融基准适配与评分、可追溯 HTML 表格提取、完整财报/多轮检索实测与可分享回放，并修复 SQLite 首次并发连接和 BM25 跨进程确定性问题。**截至 11 月 17 日，已有真实主模型多轮开发运行；正式 E1/E2 比较仍未完成。交接以包顶层说明、实测和限制为准。** 已有真实本地检索实验，不能把它们改称完整 Agent 的回答准确率。

先看 [本轮采用决定与验收](docs/OPEN_SOURCE_ADOPTION_2026-11-06.md) 和 [真实公开评测回放](examples/open-source-study.html)。后者是可转发的离线单文件，含100个真实问题的检索证据和真实Apple表格。早期 [技术复核](docs/TECHNOLOGY_REVIEW_2026-11-06.md)、[主模型/API](docs/MODEL_SELECTION_2026-11-06.md)、[三模型开发诊断](docs/EMBEDDING_STUDY_2026-11-06.md) 继续保留。旧标注表演示是空白表单，不是已有人工标签。

新的组件入口：安装 `pip install '.[html]'` 后可用 `verifin-inspect-html filing.html --source-url 原始URL --output-dir 新目录`；公开QA适配/客观评分可用 `verifin-public-benchmark --help`。结构提取结果目前为独立版本和CLI，未悄悄替换 Agent 默认旧文本的引用偏移。模型权重、完整公共数据和临时环境不放进源码发布包；固定版本下载与复跑命令在上述报告中。

## 启动

正式目录已收录 22 张方法卡。分部合并、增量利润率与一次性税项的计算、逐问交付、误选和重复取证情况见[首批新增方法的离线验收](docs/首批新增Skill离线验收.md)。

CLI 和网页会按当前提问自动选择研究 Skill，并分别向 Planner、Researcher 和 Critic 加载对应指引。显式 `--method-pack` 可复现历史固定版本。对已明确公司和期间的库存、开店批次追问，控制器可从本地披露中找可比较的数值，并调用正式计算工具。在已有会话执行 `python3 -m verifin --db 会话.sqlite3 --data-dir 财报目录 ask 会话ID "追问" --offline` 可仅运行这些本地步骤，无需模型凭据。离线结果列出处和算式，整轮保持部分完成，等待语义核验。

需要 Python 3.11 或以上。核心仅使用标准库；当前在 macOS 上验证，跨进程会话锁使用 POSIX `flock`。可选本地 embedding 需要独立安装 SentenceTransformers 及本地 safetensors 权重，核心启动不依赖它们。安装与配置见 [RUNTIME_EMBEDDINGS.md](docs/RUNTIME_EMBEDDINGS.md)；模型权重不放进源码包。

在本目录执行：

```bash
python3 -m verifin serve
```

打开 <http://127.0.0.1:8765>。可新建会话、查看保存的报告和来源；配置模型后可提问、追问和停止。默认只使用随项目附带的历史 replay 来源。

### 配置真实模型

设置以下环境变量后启动。`.env.example` 是字段说明，程序**不会自动读取** `.env` 文件。

使用 OpenAI 时：

```bash
export VERIFIN_PROVIDER='openai'
export OPENAI_API_KEY='你的本机密钥'
export VERIFIN_MODEL='账号实际可用、准备测试的模型 ID'
export VERIFIN_REASONING_EFFORT='medium'
python3 -m verifin config
python3 -m verifin serve
```

它使用 Responses 和 Standard 档位；模型未自动选定。切换 provider 前清除旧 `VERIFIN_API_KEY`、`VERIFIN_BASE_URL`、`VERIFIN_ENABLE_THINKING`，避免继承旧配置。参考 `.env.openai.example`。不要把真实 key 放进共享文件。

使用 Qwen 等兼容接口时：

```bash
export VERIFIN_API_KEY='你的密钥'
export VERIFIN_PROVIDER='compatible'
export VERIFIN_API_MODE='chat_completions'
export VERIFIN_BASE_URL='你的 workspace 对应的 HTTPS 兼容接口 base URL'
export VERIFIN_MODEL='账号实际可用的模型或快照 ID'
python3 -m verifin config
python3 -m verifin serve
```

也接受 `DASHSCOPE_API_KEY`。base URL 不要带 `/chat/completions`；endpoint 与密钥所在区域需要匹配。`config` 只验证本地配置，不代表已连接成功。不会在错误日志中打印密钥或供应商原始响应，也不会在接口失败后偷偷切换成 mock。

使用 OpenRouter 的 Chat Completions 时，将 `VERIFIN_BASE_URL` 设为 `https://openrouter.ai/api/v1` 或其美欧区域端点。默认将已注册的工具定义和可复用的上下文字段提前，并使用派生的会话标识启用粘性路由。设 `VERIFIN_OPENROUTER_PROMPT_CACHE=0` 可恢复原有请求布局以便对照。实际缓存读写量和费用以 OpenRouter 回执为准。[OpenRouter 提示缓存文档](https://openrouter.ai/docs/guides/best-practices/prompt-caching)

可选配置：`VERIFIN_TIMEOUT`、`VERIFIN_MAX_OUTPUT_TOKENS`、`VERIFIN_MAX_CONTEXT_CHARS`；Qwen 的 `VERIFIN_ENABLE_THINKING` 与 OpenAI 的 `VERIFIN_REASONING_EFFORT` 不混用。详细说明及官方依据见 [MODEL.md](docs/MODEL.md)。输出使用 JSON-object mode 和本地校验，尚非 provider strict JSON Schema。

### 联网取 SEC 数据

```bash
export SEC_USER_AGENT='你的应用名称 你的真实联系邮箱'
python3 -m verifin serve --live
```

默认不联网。启用后允许受限的 SEC 请求；不会把组员邮箱自动拿来当网络身份。缓存保存在数据库旁的 `snapshots/`，原始 replay 文件不被改写。没有任意网址抓取或执行任意 Python 的工具。

已发现的公司、filing 元数据与原始快照可跨进程复用，保留实际抓取时间。`source=live` 仍会明确刷新；缓存复用不会冒充刚刚联网取得的数据。

## 已实现的主流程

1. Planner 读取当前问题、会话范围、证据、反馈和工具参数，选择取数、综合、澄清或结束。工具选择来自模型 JSON 决策，控制器负责校验和执行。
2. SEC 财务事实使用明确期间、单位、发布时间和来源定位；计算工具检查操作数及口径。文档片段保留原文定位与哈希。
3. Researcher 生成带证据、计算与必答项 ID 的候选结论。
4. 规则检查与单独的 Structured Critic 调用检查结论及遗漏。Critic 提建议，Planner 决定下一步；不存在固定“一轮、两个动作”的补救限制。
5. 控制器检查覆盖、核验是否有效、预算和取消状态。无法完成时保存 `partial` 报告，说明未解决项；规则通过不会直接变成语义验证通过。

会话和研究 episode 分开：新研究请求获得新的有限预算，澄清回复继续原预算，等待澄清不消耗活动执行时间。会话重开后保留证据、公式和报告版本。范围或来源修正会使依赖结果失效，不能沿用旧报告的 Supported 状态。内部重新规划不能删除已经确定的必答项来伪造完成。

本轮采用 Proposal 允许的 **Python 状态机 fallback**。本地界面使用标准库 HTTP 服务，先打通交互；尚未迁移为 LangGraph/Streamlit。

## 命令行

```bash
python3 -m verifin new --title 'Company research'
python3 -m verifin sessions
python3 -m verifin ask SESSION_ID 'Show Microsoft revenue and operating margin for FY2025 Q2.'
python3 -m verifin ask SESSION_ID 'Explain that calculation using the saved sources.'
python3 -m verifin trace SESSION_ID
python3 -m verifin cancel SESSION_ID
python3 -m verifin export SESSION_ID ./exports/my-report
python3 -m verifin export SESSION_ID ./exports/first-report --version 1
python3 -m verifin scope SESSION_ID
python3 -m verifin scope SESSION_ID --file scope.json --expected-version 2
python3 -m verifin charts SESSION_ID ./exports/charts --version 1
python3 -m verifin audit SESSION_ID --output ./exports/log-audit.json
```

全局 `--db` 与 `--data-dir` 放在子命令前。默认数据库为当前目录下 `.verifin/sessions.sqlite3`。例如：

```bash
python3 -m verifin --db ./my-work/research.sqlite3 ask SESSION_ID 'Compare with another covered issuer.' --live --model-calls 20 --tool-calls 20 --seconds 300
```

导出包括 Markdown、完整 JSON、证据 CSV 和计算 CSV。报告保存来源、公式、未解决项、停止原因及实际调用/Token 记录。新增 [离线费用账本](docs/COSTS.md) 可用显式价格表估算；缺报、不同档位或无法匹配的模型保持未知，界面不会自动猜金额。

界面可选择历史报告版本，查看对应范围、来源、公式和图表，并导出该版本。当前范围可直接编辑，过期表单会被拒绝；受影响依赖失效而旧报告保持原样。会话轮询读取报告摘要，正文按需载入；历史报告的核验标签表示保存时的状态。累计用量区分已知 Token 与缺报数据，未提供价格时金额保持未知。

## 捕获候选答案并运行 E1

配置真实模型后，可以保存本次研究的第一份候选答案。保存发生在 Critic 复查之前，之后的修订不会覆盖这份输入：

```bash
python3 -m verifin ask SESSION_ID 'Your research request' --capture-case ./cases/dev-001.json
python3 -m verifin evaluate --case ./cases/dev-001.json --output ./evaluation/detection-001
python3 -m verifin evaluate --case ./cases/dev-001.json --output ./evaluation/revision-001 --revise --model-calls 12 --tool-calls 8 --seconds 180
```

`evaluate` 的检测阶段最多发起 2 次模型调用：普通复查和 Structured Critic 各一次，另外保存不复查和规则检查条件。`--revise` 再让两个模型复查条件分别进入同一个修订执行器；以上预算是**每个条件的修订预算**，不包含先前的检测调用。反馈解释、规划、综合和失败均计数。

修订使用独立工作会话，冻结原始范围和必答项，不影响研究会话。输出仍是 `Pending Verification` 的候选，不能视作已经改善。模型接口失败和预算耗尽保留在 JSON 记录中。`--output` 必须是新目录或空目录。

默认使用 replay，`--live` 可用于探索性取数，新取得的原始快照保存在评价输出目录的独立子目录。正式比较还应固定可获取的来源快照，避免不同运行时刻的数据变化成为混杂因素。已提供双人独立标注、裁决、修订质量/退化和资源计分接口；真实标注与研究结果仍待完成。详见 [EVALUATION.md](docs/EVALUATION.md) 和 [ANNOTATION.md](docs/ANNOTATION.md)。

## 无模型的工作入口

`planning preview` 执行固定取数与计算，停在综合前；`retrieval` 导出语料、执行 BM25 或使用已提供的真实向量执行 dense/hybrid；`study` 生成待人工完成的材料并校验实际标注。完整命令与输入格式见 [OFFLINE_WORKFLOW.md](docs/OFFLINE_WORKFLOW.md)。

`ask`、`evaluate`、`serve` 和 `planning preview/run` 可加 `--market-data prices.json`、`--retrieval-vectors vectors.json`。交互研究还可用 `--retrieval-encoder config.json` 编码未见查询；paired E2 暂不接受 runtime encoder。前三个支持 `--market-config provider.json`；只有明确 `--live` 才允许市场联网，密钥从 `ALPHAVANTAGE_API_KEY` 读取。行情导入和 listing 身份不要求 SEC CIK，格式、授权、交易日期、币种与复权口径见 [MARKET.md](docs/MARKET.md)。

E2 使用相同引擎、模型、来源和预算的配对比较；`planning run` 需要真实模型配置，`planning preview` 不需要。两个入口均冻结来源；E2 公共 TaskSpec 的上游解析应单独计成本与评估。详见 [PLANNING.md](docs/PLANNING.md)。

### 自动运行与材料整理

```bash
python3 -m verifin.pilot --suite examples/pilot-development.json --output .verifin/preflight
python3 -m verifin.annotation_form --reviewer-dir path/to/reviewer-1 --output reviewer-1.html
```

Pilot 默认仅本地检查；显式 `--execute` 才调用配置的 LLM，每轮重开数据库并保存候选、报告、事件和审计。HTML 标注表可以离线编辑、拆分断言和导出草稿；不会预填真实判断或冒充独立标注。详见 [PILOT_AND_REVIEW_TOOLS.md](docs/PILOT_AND_REVIEW_TOOLS.md)。

## 不用密钥的可复现集成演示

```bash
python3 -m scripts.demo_replay --output .verifin/demo
python3 -m verifin --db .verifin/demo/sessions.sqlite3 serve
```

演示使用原交接包保存的真实 MSFT 财务来源：收入 USD 69,632,000,000，营业利润 USD 31,653,000,000，计算利润率约 45.4575%。关闭并重开数据库后，第二轮复用来源和公式，工具调用数为 0。**规划与 Critic 回复是固定测试脚本，真实模型调用数为 0**，页面和导出都标明这一点。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

测试涵盖来源哈希、财季/累计口径、数值边界、版本失效、多轮修正、跨公司扩展、澄清预算、失败计数、取消竞态、崩溃后的部分结果、报告导出和本地 HTTP 行为。HTTP 测试需要能够监听 `127.0.0.1` 的临时端口，不访问公网。

最新结果和剩余工作见 [开发状态](docs/STATUS.md)。模块说明见 [存储](docs/STORAGE.md)、[数据与计算工具](docs/TOOLS.md)、[模型与核验](docs/MODEL.md)、[评测脚手架](docs/EVALUATION.md)。设计依据为 [最终 Proposal](docs/PROPOSAL.md)。

## 当前边界

- 产品逻辑没有两家公司 allowlist；随包 replay 只覆盖 MSFT/NVDA。通用 SEC 路径通过第三发行人的模拟响应测试，实际网络可用性仍待配置联系信息后检查。
- 财务指标映射、发行人覆盖和文档支持有明确边界；缺数据时返回缺口，不编造结果。SEC 历史分页、BM25/dense/hybrid 和通用行情适配已实现；三个真实本地 embedding 已做开发诊断，供应商访问及正式有效性实验未验证。
- 原文披露中的数值可以交给 Critic 检查，但只有结构化事实和计算记录能做确定性数值核对。模型语义错误、提示注入抵抗和多轮真实表现仍需实测。
- 模型上下文至多选择 24 条近期/相关历史消息和 3 个相关报告，明确省略数量，保留当前范围和原始证据。选择是抽取式，不是模型语义摘要。超过总上下文上限会终止为部分结果，没有无限长期记忆。
- 这是本地单用户开发版；没有登录、多用户部署、自动后台研究、交易或实时监控功能。停止会阻止后续调度，不能撤回已经发送的 HTTP 请求。

原 Sandisk 交接包和已提交用途的 Proposal 文件均未修改。

财报原表数值提取可用 `pip install '.[disclosures]'` 安装。读或搜本地回放文档时，受支持的 HTML 收入变动表和带原生季度标签、图框及单位的 PDF 库存图会同时返回可计算的单元格 ID。安装了对应解析库即可生效。Planner 按行、期间和口径选取两个数，调用 `calculate`。段落引用、图表舍入精度和原始坐标仍保留。
