# Pilot 与独立标注辅助工具

这两个工具把重复的运行、整理、格式检查工作自动化。它们不生成金融事实真值，也不把脚本测试、规则通过或模型自评计作人工标注。没有 API 配置时可以完成 preflight、已有材料的标注表生成及全部离线测试。

## 1. 运行前检查与多轮 pilot

准备一个明确列出对话、数据划分和预算的 JSON 文件。下面只是开发用示例请求，不是已建立的 heldout 测试集：

```json
{
  "protocol": "verifin-pilot-v1",
  "label": "Development multi-turn smoke pilot",
  "split": "development",
  "budget": {
    "max_model_calls": 12,
    "max_tool_calls": 8,
    "max_seconds": 180
  },
  "cases": [
    {
      "id": "msft-revenue-margin",
      "title": "Development dialogue: revenue and margin",
      "turns": [
        "Report MSFT revenue and operating margin for 2024-10-01 through 2024-12-31 using information available by 2025-01-31. Show the operands and sources.",
        "Explain that margin calculation using the saved sources."
      ]
    }
  ]
}
```

先执行不消耗模型额度的检查：

```bash
python -m verifin.pilot \
  --suite path/to/pilot-suite.json \
  --output .verifin/pilot-preflight
```

默认只写 `suite.json`、`preflight.json` 和 `summary.json`。检查包括显式预算、case ID、模型本地配置、凭据是否存在，以及 replay 文件的清单哈希和逐文件完整性。不会发请求来验证 key、模型权限或余额，缺少模型配置也可以留下可分享的 preflight。输出不含凭据值。

确认 API 账户、确切模型及预算之后，才执行：

```bash
python -m verifin.pilot \
  --suite path/to/pilot-suite.json \
  --output .verifin/pilot-run-001 \
  --execute
```

`--execute` 允许访问已配置的模型 API。默认金融工具仍只读 replay 和本地缓存；需要 SEC / 市场数据网络访问时另加 `--live`，并配置 SEC 联系 User-Agent。源配置沿用现有 `--market-data`、`--market-config`、`--retrieval-vectors`、`--retrieval-encoder` 参数；两种 embedding 输入互斥。没有隐式模型回退或无限重试。

预算按 episode 执行。等待用户澄清后，同一个 episode 继续使用原预算；常规后续轮次可以开启新 episode。preflight 另外列出“轮数 × 单 episode 预算”的保守总上限。时间预算是协作式限制，正在执行的调用须先返回；调用数、token 或时间限制都不等同于货币额度硬限制。请在供应商账户另行管理实际支出。

每个 case 都是独立的新 session，每一轮开始与结束都会关闭并重开同一个 SQLite 文件。输出结构如下：

```text
pilot-run-001/
  suite.json
  preflight.json
  summary.json
  sessions.sqlite3
  <case-id>/
    session-audit.json
    turn-01/
      result.json
      candidate-before-review.json   # 只有真正生成首份候选才会出现
      events.json                    # 本轮新增事件，保留模型 usage 字段
      session-audit.json              # 截至该轮的整个 session 审计
      report.*                       # 该轮存在报告时导出
    turn-02/...
```

澄清轮可能没有报告、没有候选；失败轮保留 partial 和错误类型，不能被当成成功样本。报告中的 Supported 仍是系统判断。`semantic_acceptance: not_assessed` 和 `truth_scored: false` 始终保留，自动日志检查通过只说明协议/结构检查通过。保存的 `candidate-before-review.json` 可以交给现有 E1 入口继续做条件比较；pilot 本身不等于 E1 评估。

输出目录必须是新目录或空目录，防止混入旧试验或覆盖已有结果。不要在同一个 pilot 文件中混合开发与 heldout 案例；`split` 是明确声明，独立划分和泄漏约束仍由现有 study plan 流程管理。

## 2. 生成可以直接打开的独立标注表

先使用已有的 `verifin study annotations` 生成至少两位独立标注者的材料，流程详见 [ANNOTATION.md](ANNOTATION.md)。随后，分别从一个 `reviewer-N` 目录构建 HTML：

```bash
python -m verifin.annotation_form \
  --reviewer-dir path/to/bundle/reviewer-1 \
  --output path/to/reviewer-1.html
```

只将对应 HTML 和标注指南发给相应标注者。不要把 `administrative/` 目录交给标注者。生成器只接收 blinded `input.json` 与同一 packet 的 `annotation.json`，拒绝直接嵌入 administrative packet，也拒绝系统支持标签。页面包含必要的请求、范围、claims、证据与计算，而不包含条件名、模型设置或实验 run 绑定。

HTML 是单个本地文件，不依赖服务器、第三方脚本、在线模型或网络请求。可在现代浏览器直接打开。表内提供：

- 定义和拆分必需内容单元，填写可回答性与理由。
- 将每个 claim 拆成多个独立断言，关联证据、计算、内容单元和原始回答断言。
- 分别评价断言支持情况与必需内容的完成情况。
- 导入/导出未完成草稿；不自动保存、不预填人工判断。
- 检查遗漏、ID 关联、支持情况与完成情况的结构一致性。
- 逐份确认断言拆分完整，并由标注者明确签署后导出已完成 JSON。

导入草稿或修改内容会清除全局完成签署。导出草稿始终设置 `independent: false`、`completed_at: null`，因此不能意外进入已完成结果。点击完成按钮也不会生成判断：所有字段必须先填写，且必须由本人勾选完成声明。浏览器检查只能验证格式和关联，不能证明判断正确或参与者确实独立。

管理员收到 `annotation-completed.json` 后，仍须使用原有 sealed packet 做权威校验：

```bash
python -m verifin study validate-annotation \
  --packet path/to/bundle/administrative/packet.json \
  --annotation path/to/annotation-completed.json
```

随后沿用两份独立标注与明确裁决流程。HTML 不自动裁决分歧、不自动填人名、不生成完成标注，也不绕过已有评分协议。此版本为 **E1 outcome annotation** 编辑器；E2 规划标注仍使用原有专用格式，不能混用。

## 3. 验证范围

新增测试覆盖默认 dry run 不调用模型、凭据隐藏、目录防覆盖、错误轮留存，以及真实 controller + scripted fixture 的两轮持久化、候选冻结、报告版本和完整事件导出。标注测试覆盖与现有 Python validator 的兼容、盲审绑定、空白模板不可完成计分、引用文本的 HTML 注入隔离，以及 JavaScript 完整性规则的实际执行。

这些是工具与协议测试，未使用真实 LLM、未产生真实人工标签、未建立模型性能结果。模型/API 可达性、真实对话质量与最终人工判断仍需在团队决定运行时完成。

已提供可直接打开的 [DEMO 标注表](../examples/annotation-demo.html)，使用单独存放的虚构材料和未填写模板。主 agent 已在内置浏览器完成本地页面的布局与基础交互检查；另用 jsdom 31.0.0 通过十项 DOM 交互检查，包括草稿导入/导出及空白禁止完成。详见 [DEMO 说明与复现方式](../examples/ANNOTATION_DEMO.md)。
