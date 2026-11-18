# E2 人工质量与误调用评分

`verifin.planning_scoring` 对 `run_planning_pair` 保存的真实输出生成待填材料、校验独立标注及裁决，再整理描述性指标。它不调用模型，不生成真值，不把程序测试当成实验结果。双人独立工作及查证仍需实际完成；软件只能检查保存记录的一致性。

## 生成独立评审材料

```python
import json
from pathlib import Path
from verifin.planning_evaluation import FrozenPlanningCase
from verifin.planning_scoring import (
    build_planning_packet, write_planning_annotation_bundle,
    validate_planning_annotation, adjudicate_planning_annotations,
    score_planning_outcome, summarize_planning_scores,
)

# 使用已有真实运行文件；以下操作完全离线。
case = FrozenPlanningCase.from_dict(json.loads(Path("run/case.json").read_text()))
run = json.loads(Path("run/result.json").read_text())
packet = build_planning_packet(case, run, "adaptive", turn=1, trial_id="trial-1")
write_planning_annotation_bundle(
    packet, "review-packet-001", ["ACTUAL_REVIEWER_A", "ACTUAL_REVIEWER_B"]
)
```

每个条件、每轮分别生成材料，输出目录使用不透露条件的中性编号。`ACTUAL_REVIEWER_A/B` 须替换为真实参与人标识。默认拒绝覆盖已有文件，保护人已填写的记录。

- `administrative/packet.json` 绑定完整 frozen case、paired run、条件、回合、前后答案、事件、来源和模型设置哈希。此目录留给组织者。
- 每份 `reviewer-N/input.json` 去掉显式条件标签和模型调用数，保留本轮原请求、前文、前轮请求和答案、前后 claims/证据/计算、实际工具尝试及即时观察。
- `reviewer-N/annotation.json` 为独立待填表：所有真值为空，`independent=false`，无完成时间。分别分发，完成前不交流。
- `administrative/resolution-template.json` 为未裁决模板。

这种处理是隐藏显式组别标签；工具路径或回答内容仍可能让人推测组别，不保证统计意义上的完全盲法。组织者应将资料目录重命名为中性编号，不给评审查看原始组别目录、角色调用数或另一评审的答案。文件哈希用于避免误混版本，不是数字签名；应保留只读原始输出和实际查证记录。

## 共用必答单元和全部断言

两组同一轮使用原始公共 `TaskSpec` 的 checklist；尚未解析的请求保留原文，不能用模型自己缩减后的 checklist 作评分标准。人工在 `assessment.units` 将每项要求拆为可独立判定的必答内容，说明 `answerable` 或 `legitimate_gap` 及理由。两组同一轮必须复用相同单元和标注指南。E2 两组各自生成了不同原答案，因此不能强求两组的 before 判定相同。

`assessment.before` 和 `assessment.after` 使用 [E1 标注说明](ANNOTATION.md) 的单元、断言和证据字段；分别覆盖每一条展示的 claim 和每一个必答单元。复合 claim 要拆成多条 assertion，额外断言也要标，不能删掉不利内容。E2 不要求 `baseline_assertion_ids`，因为此模块报告两阶段质量，不推断逐命题纠正归因。

`correct` / `legitimate_gap` 必须链接人工判为 supported 的断言。一个空答案仍有全部原必答单元，必须标为 missing；删除原来正确的必答内容会降低覆盖和完整性，不能靠断言分母变小获得“整体改善”。报告只计算结构化 claims 的质量；界面摘要不是额外经过核验的答案。

阶段为 `null` 时对应判断必须为 `null`，不能补造答案或记满分。阶段存在但运行失败时，仍可判断实际留下的内容；控制器 complete/失败、Supported 标签及规则通过均不是真值。人工判断的合理证据缺口与模型不会取数、预算耗尽、网络失败不同。

## 逐次工具调用判断

`assessment.tool_calls` 必须覆盖每一次实际调用，包括失败、重复和最终没有采用的调用。每条需保留 `call_id`，填写以下 verdict 和非空理由：

| Verdict | 判定依据 |
|---|---|
| `justified` | 在当时已知状态和公共任务下，有合理研究或修复用途；允许等价有效路径 |
| `mistaken` | 人工认定调用目标、参数、时点或必要性有实质错误，并解释可核查依据 |
| `unassessable` | 保存的信息不足或无法可靠判断，明确缺少什么 |

**重复或失败本身不是误调用。** 合理重试、范围变化后的重新查询和验证来源可以是 justified；成功返回的调用也可能答非所问。不得使用一条“标准动作序列”当作唯一真值。判断依据是调用当时可知的信息，不能利用后续结果倒推一个当时合理的行动一定有错。

新日志带 `observation={data,evidence,calculations,error}` 和原始 observation hash，材料保留并校验。旧日志没有即时观察时保持 `null`；若参数、摘要及来源仍不足以判定，填写 unassessable，不能自动补 justified 或 mistaken。

## 完成、裁决和汇总

真正完成后，各自填写 `independent=true`、带时区的 `completed_at`。两份记录须来自不同标注者、使用同一指南并绑定本 packet。裁决明确列出实际 adjudicators、完成时间、最终 assessment；任一 units/before/after/tool_calls 部分不同，`resolution_notes` 必须说明如何解决。软件不多数投票选真值。

```python
# a、b、resolution 从实际完成并保存的 JSON 读取。
validate_planning_annotation(packet, a)
validate_planning_annotation(packet, b)
adjudication = adjudicate_planning_annotations(packet, [a, b], resolution)
score = score_planning_outcome(packet, adjudication)

# 同一 paired run 的其余条件/回合完成后一起汇总；缺的仍显示 pending。
summary = summarize_planning_scores(run, [score])
Path("human-summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
)
```

每阶段输出正确必答内容覆盖率、无依据断言率（包含 contradicted）、合理缺口处理率及 `human_complete_supported_answer`。完整答案要求全部必答单元正确回答或准确处理合理缺口，且没有无依据断言；有无法判断的内容时完整性为 null。

误调用数仅由人填的 mistaken 计数。分母为零则率为 null；有 unassessable 时精确率为 null，并给已知下界和可能上界。行级结果保留运行状态、停止原因、错误类型、前后阶段是否可用和标注状态。组均值附实际观察回合数；`paired` 仅对双方同一轮都有可判定指标的样本计算 adaptive−fixed 差值，列出纳入和排除数量。不能比较来自不同缺失子集的组均值后直接宣称改善。

这是一份运行的描述性整理，不是显著性检验或已完成的 held-out 研究。自然案例、注入诊断与 scripted fixture 应分别保存和报告。耗时、模型/工具尝试和 usage 使用原 paired run 的 diagnostics；公共 TaskSpec 的上游解析成本未包含，不能由本评分工具补造或隐去。当前测试的合成注释只验证协议，不是两名真人的实验数据。
