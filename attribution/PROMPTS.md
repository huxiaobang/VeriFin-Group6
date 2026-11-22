# Project-related prompts

**Group 6 · VeriFin: Evidence-Grounded Financial Research with Structured Verification**

## Codex development assistance

The following are verbatim excerpts from project development instructions. Surrounding personal and environment-specific details are omitted. The excerpts retain the original Chinese wording.

> 保留原实现作为对照。让同一个用户入口可以选择实验后端，尽量复用现有财报工具、证据与计算 ID、Skill、SQLite 数据和输出格式。Planner 的自主选择、Structured Critic 的逐条判定，以及 E1/E2/E3 的公平对照都要保留。

> 两套实现在相同的初始状态、输入、证据和离线模型回执下运行。测试脚本不要预填需要 Agent 作出的决定。

> 请区分模型作出的研究判断和引擎强制执行的状态转换。

> 修改只在 SanDisk 实验副本进行，先用离线回执和真实本地工具验证。

## VeriFin research roles and settings

- [Static application role prompts](runtime-role-prompts.json) reproduces `COMMON_PROMPT` and all five entries in `ROLE_PROMPTS` from `code/verifin/model.py` at the submitted Python release `503c149808aeaf225f4b35be89c89dd50a827fbc`. The JSON stores the exact string values after decoding. The shared prompt accompanies Planner, Researcher, Structured Critic, E1 generic reviewer, and E1 revision Planner.
- The source snapshot also contains the prompt assembly in `ModelClient._research_role`, `ModelClient._cached_tool_input`, and `ModelClient.complete` in `code/verifin/model.py`. The final payload depends on provider, selected role, active research methods, registered tool definitions, and the active episode. The versioned methods are in `code/verifin/skills/*/SKILL.md`. Source context and tool outputs enter as quoted data.
- Run-time model configuration and provider instructions are in `code/docs/MODEL.md` and `code/.env.openai.example`. The relevant experiment records specify their source revisions, model IDs, and budgets. The role strings above come from the submitted version. Historical requests can use a different revision or different context.

## Public experiment inputs

- [18 public development task prompts and context](development18-public-prompts.jsonl) is the exact JSONL from `evaluation/development18/pilot_agent_inputs.jsonl`. It contains the 18 questions and the public information cutoff and market conventions. The scoring references were kept outside agent inputs.
- E1 McDonald's follow-up, as recorded in `VeriFin_MCD_E1_20261117/结果.md`.

  > 回到最开始，你说利润比收入涨得快。那这季到底更多是加盟业务撑起来的，还是另有原因？

- E2 ordinary task and injected source-outage task, as recorded in `evaluation/实验证据/e2-cases/01-direct.json` and `03-source-outage.json`. Both cases use the same user text. The simulated outage is an experimental condition.

  > 截至 2025 年 1 月 31 日，微软截至 2024 年 12 月 31 日的单季收入是多少？

- E2 opening turn and changed period or issuer, as recorded in `evaluation/实验证据/e2-cases/02-previous.json` and `04-switch.json`. The two quoted follow-ups belong to different cases with the same opening turn. These development fixtures also provide public task specifications to both arms.

  > 以 2025 年 1 月 31 日为截止日，微软最近一季收入是多少？

  > 那上一季呢？

  > 换成英伟达，它最近一季收入是多少？

The development pilot judging criteria and per-case results are documented in `evaluation/development18/results-2026-11-22/protocol.md` and the corresponding results. The protocol is a retained rubric, and the original standalone Codex grading prompts are unavailable here.

## Missing records

The archive is incomplete for earlier Codex coding, report or proposal editing, evaluation, and team-only ticket-intake interactions. The specific models used in historical Codex sessions and some original provider-specific role inputs are also unavailable in this record. The application role prompts are recovered from versioned source. Team members should add any separately retained project prompts they can share and record any further gaps before submission. No credentials, private conversation exports, raw authorization records, student numbers, or contact details are included in this prompt record.
