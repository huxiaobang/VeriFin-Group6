# E1 人工标注、修订评分与实验材料

这些工具不调用模型，也不替人判断金融事实。生成的表单始终标为 **pending**；两个姓名、一个勾选项或系统的 Supported 状态，都不能替代实际独立标注。代码检查记录的完整性、一致性和输入绑定，无法证明填写人真的独立工作。团队应保留原始标注文件、查阅来源和裁决记录。

## 工作顺序

1. 开发结束后选择新事件和新问题，建立 case manifest。自然输出和人为注入错误分别标记；同一事件、短对话各轮和扰动版本留在同一 split。原有开发问题不能重新命名为 held-out。
2. 冻结模型快照、提示、来源、预算、质量/成本容忍值以及运行顺序。计划默认为 12 个不同的自然 held-out 案例，并要求短对话及一个开发/验证中未出现的发行人。注入诊断不能凑够自然案例数量。
3. 用第一份语义复查前候选运行 E1 检测；需要修订比较时，按计划对四个条件使用同一执行器。保留失败及耗尽预算的结果。模型运行不属于本材料生成工具的职责。
4. 人工先确定原请求的必答内容单元、原答案判定和检测 gold，再评估各条件。**同一 case/trial 的各条件必须复用同一套必答单元和 baseline 判断**。不得看到某组结果后为该组改变评分标准。
5. 分别发给至少两名标注者，各自完成后才交流。人工处理分歧并填写裁决；软件不会多数投票选“真值”。最后校验、评分、汇总。

## 生成材料

从项目根目录可先运行以下命令；它只生成空白计划及验收表，不会构造事件、答案或已完成的人工判断：

```bash
python3 -m examples.study.prepare_materials --output .verifin/study-materials
```

生成 `heldout-slots.json`、`draft-plan.json` 和 `application-acceptance.json`。12 个 slot 的事件、发行人、问题和标注人均为空；draft plan 不能冻结或被视为已完成实验。

有真实的保存案例和运行输出后，可离线生成注释包：

```bash
python3 -m examples.study.prepare_materials \
  --output .verifin/annotation-case-001 \
  --case path/to/case.json --detection path/to/detection.json \
  --revisions path/to/revisions.json --condition generic_review \
  --annotator ACTUAL_REVIEWER_A --annotator ACTUAL_REVIEWER_B
```

`ACTUAL_REVIEWER_A/B` 必须换成真实参与人的标识。此命令也不会替他们完成标注。

- `administrative/packet.json` 保存 case、完整检测 run、完整修订 run 和条件输出的哈希绑定，以及展示给人的原答案/修订答案。
- 每个 `reviewer-N/` 单独分发。其中的 `input.json` 不带条件名或 run 绑定信息；`annotation.json` 只带该标注人的身份和不透明 packet hash。不要把 administrative 目录发给标注者。
- `administrative/resolution-template.json` 是尚未裁决的空模板。

默认拒绝覆盖已有文件，避免抹掉人填过的注释。文件哈希用于防止误混版本、漏更新和修改后误评分，不是数字签名，也不能防止有意伪造整套材料。应另外保留只读原件及团队审查证据。

## 必答内容单元

`assessment.units` 是人独立确定的原请求内容，不是模型声称的完成度。每个单元包含：

| 字段 | 意义 |
|---|---|
| `id` | 跨条件固定的单元 ID，例如 `unit:r1:revenue` |
| `requirement_id` | 原 checklist 的现存 ID，不能增删原请求来迁就结果 |
| `description` | 一个可独立判断的必答内容；宽泛要求应拆成多个单元 |
| `answerability` | `answerable` 或经人工确认的 `legitimate_gap` |
| `rationale` | 必答性、可回答性及所查来源的依据 |

每个原始 requirement 至少对应一个单元。合理缺口是所需证据确实不可得或不能比较；一次查询失败、模型不会取数、预算耗尽都不自动让原本可回答的问题变成合理缺口。

可使用外部原始资料核实 gold，但应记录 URL、版本和查阅依据，并与模型可见证据区别保存。对答案“由所引证据支持”的评分仍基于该答案实际可见/引用的来源。核对实体、财季或累计期、单位、币种、会计口径、业务分部、发布时间、计算操作数，以及管理层归因与因果结论的区别。

## 每个答案的完整判定

`assessment.baseline` 和 `assessment.candidate` 各包含 `assertions` 与 `units`。候选完全不可用时 candidate 必须为 `null`，不能补一份假答案。

先把每个展示的 claim 拆成独立命题。每个 claim 至少有一条 assertion，复合 claim 可以多条；确认 `claims_segmentation_complete=true`。保留所有没有对应必答单元的额外断言，它们也可能制造错误。

Assertion 字段包括 `id`、`claim_id`、原意忠实的 `text`、`requirement_unit_ids`、`evidence_refs`、`verdict`、`rationale`。判断使用 `supported`、`unsupported`、`contradicted`、`unassessable`。证据 ID 必须存在于对应答案的证据/计算记录中；引用存在不意味着支持。修订后的每条 assertion 另外填 `baseline_assertion_ids`，表示它替换或延续的原命题；新命题填空数组。不要仅靠相同 claim ID 判断语义对应。

每个必答单元在前后答案中都要填一条 judgment：

| `verdict` | 含义 |
|---|---|
| `correct` | 内容完整正确，并由人工链接的 supported 命题回答 |
| `partial` | 只回答了所需内容的一部分；不按完整正确计分 |
| `incorrect` | 有回答，但实质错误或缺乏必要支持 |
| `missing` | 没有回答；`assertion_ids` 必须为空 |
| `legitimate_gap` | 对预先确认的合理证据缺口做了准确说明 |
| `unassessable` | 标注者无法确定，必须解释原因，不能冒充无错误 |

`correct`、`legitimate_gap` 必须链接人工判为 supported 的命题；可回答单元不能以弃答获得合理缺口分。每条 judgment 都需要理由。空答案仍要对每个原必答单元填写 missing，不能删掉单元。

## 独立标注和裁决

标注完成后才填写 `independent=true` 与带时区的 `completed_at`。至少两份原始注释须有不同 `annotator_id`，同一 `guideline_version`，且都绑定当前 packet。

裁决记录必须显式包含 `source=independent_human`、`adjudicated=true`、当前 `packet_sha256`、实际 `adjudicators`、完成时间和最终 `assessment`。两人记录或最终裁决在 units、baseline、candidate 任一部分不同时，`resolution_notes` 必须对该部分解释处理依据。裁决可由两名成员共同完成，不要求虚构第三名裁决人。

```python
from verifin.outcome_scoring import validate_annotation, adjudicate_annotations, score_outcome

# packet、annotation_a/b、resolution 从保存的 JSON 读取；所有人工作业需先真实完成。
validate_annotation(packet, annotation_a)
validate_annotation(packet, annotation_b)
adjudication = adjudicate_annotations(packet, [annotation_a, annotation_b], resolution)
score = score_outcome(packet, adjudication)
```

裁决产物保存两份原始记录及各自哈希。修改答案、packet、原标注或裁决后，旧评分不能继续混用。

## 指标口径与防止“删答案获益”

- 正确必答内容覆盖率：`correct / answerable units`。合理缺口单列，不冒充实质内容正确。部分回答不按完整正确计。
- 无依据断言率：`(unsupported + contradicted) / all assertions`，同时单列 contradicted 数量。
- 任一分母为零时返回 `null`；有 unassessable 时相关精确率为 `null`，另提供已知下界及可能上界。不能将未知样本悄悄当零错误。
- 分别报告必答单元纠正和命题纠正，不把两者相加当成一个收益。删掉无依据的**可选**命题可以是改善；删掉必答答案并不自动算纠正。
- 原来正确或准确处理缺口的必答单元，修订后变差会记录 regression；变为 missing 另记 `deleted_correct_required_unit_ids`。新无依据命题或无依据命题数量增加也阻止 `safe_improvement`。
- 一边纠正、一边损坏时 `quality_change=mixed`，`safe_improvement=false`；不能对外称总体改善。出现无法判定的关键内容时 `safe_improvement=null`。

这些规则建立在人工提供的单元划分、命题对齐和真值判断上；软件做逻辑校验与计数，不能代替人的金融核实。跨条件汇总会拒绝不一致的必答单元、baseline 判断或检测 gold。

## 分组计划与汇总

```python
from verifin.study import case_manifest_entry, create_study_plan, freeze_study_plan

entry = case_manifest_entry(case, group_id="ACTUAL_EVENT_GROUP", kind="natural",
                            event_id="ACTUAL_EVENT_ID", issuer_ids=["ACTUAL_ISSUER_ID"])
# 多个相关轮次/扰动保留同一 group；injected 条目必须填 parent_case_id。
plan = create_study_plan(entries, study_id="team6-e1", heldout_group_ids=heldout_groups,
                         model_settings=exact_detection_settings, budgets=declared_budgets,
                         tolerances=declared_quality_and_cost_tolerances)
plan = freeze_study_plan(plan)  # 缺案例/新发行人/对话/配置等会拒绝
```

标识由团队依据真实资料填写；大小写和别名由整理人统一。自动检查 group、event、dialogue、parent、重复 context hash 的关联，不能识别语义上相同但被故意换名的事件。冻结检查只确认声明的材料完整，不证明模型已接通，也不替团队选择合理阈值。模型设置须与运行输出的 settings 对象完全一致。至少给预算中的 `max_model_calls`、`max_tool_calls`、`max_seconds` 正有限值；实际执行还应固定 no-progress 与 planner-step 限制。

```python
from verifin.outcome_scoring import summarize_resources
from verifin.study import aggregate_results, export_study_summary

resources = summarize_resources(detection_run, revision_run, prices=explicit_prices, trial_id="trial-1")
summary = aggregate_results(plan, outcome_scores, resources=resource_records,
                            detection_scores=human_detection_scores)
export_study_summary(summary, output_directory)
```

导出 summary JSON、每案例/条件 CSV、分组 CSV 和配对 CSV。所有计划中的组合都会出现；尚未标注显示 pending，失败候选保留 unavailable。`require_complete=True` 可强制拒绝漏项汇总。自然/注入、不同执行模式分别汇总；generic 与 structured 的配对差值只使用同一 case/trial 且两边可判定的结果，明确列出排除项。样本少，当前只做描述性汇总，不输出未经设计的显著性或普遍化结论。CSV 的空格代表未知/未评分，不代表零。

## Token、耗时和费用

每阶段模型尝试数与已知 Token 均计入，包括失败调用。`*_reported_calls` 与实际 `model_calls` 一致且对应 Token 有值时，才视为完整记录；旧日志只有总数时保守视作已知下界。反馈解释、重新综合和工具工作的耗时包括在修订阶段。

推荐显式提供 `schema_version: 1` 的分项价格 catalog，例如 `data/pricing/openai-standard-2026-11-06.json`。资源汇总按每次调用的 endpoint、实际返回模型、服务档位、普通输入、缓存读写、输出和长上下文区间计算；缺少记录或 usage 时总费用保持 `null`，仅列能够估算的部分。详见 [COSTS.md](COSTS.md)。工具不联网查价、猜价或换汇，不同币种不相加。

旧 `model/currency/input_per_million/output_per_million/source/effective_date` 格式仍可读取，但默认只给未调整的算术参考，不计为金额下界。只有明确 `pricing_scheme: flat_rate` 且不涉及 Responses、服务档位或缓存分项时，才按声明的统一费率估算。缓存折扣和写入不能被普通输入单价替代。所有结果均是配置费率下的 token 估算，不是供应商账单或支出硬限制。

阶段耗时之和是执行时间口径，不是用户等待墙钟时间或人工注释耗时。缓存预热、在线来源变化、机型负载与调用顺序仍需实验控制。固定 corpus/replay 和提前声明顺序，才能解释比较。

应用验收的自动日志检查与人工语义检查边界见 [APPLICATION_ACCEPTANCE.md](APPLICATION_ACCEPTANCE.md)。
