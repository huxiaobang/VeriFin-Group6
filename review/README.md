# VeriFin Final Report 与实测材料 · 2026-11-24

先看[报告 PDF](Group_6_VeriFin_Final_Report.pdf)。需要逐段评论时，可以直接打开 [GitHub 网页版全文](report.md) 或下载 [Word 文档](Group_6_VeriFin_Final_Report.docx)。报告和评测来自实验提交 `21493e6`。这是给组员审阅的草稿。

| 实验 | 这轮测到了什么 | 从哪里核对 |
|---|---|---|
| E1 · 逐条核验能否修好答案 | 在 11 份开发首稿上，原稿和仅用规则都覆盖 35/64 个必答项，通用审稿覆盖 48/64，Structured Critic 覆盖 50/64。后两组各有 8/11 份全对。另有两份诊断首稿。四组共 52 份答卷，采用两阶段 AI 评分。 | [按首稿、条件查看得分与得失项](e1/case-grades.tsv) |
| E2 · 自主规划如何应对追问与缺数据 | 相同任务分别交给自适应 Planner 和固定策略，各有 8 轮。自适应组 5 轮完整财务答、1 轮有效澄清、2 轮未交付实质回答。固定组 2 轮完整财务答、5 轮数字正确但缺少“最新”依据、1 轮澄清未解决日期歧义。 | [逐轮评分、模型及工具调用](e2/turn-grades.md) · [当时的首稿和最终回答](e2/first-and-final-answers.zip) |
| E3 · 哪种检索能找到支持答案的段落 | 每种方法使用相同的 5 段、3,000 字符上限。20 条正例查询中，BM25 命中 15 条，稠密检索与混合检索各命中 16 条。另有 4 条控制查询，每种方法合计 24 条。 | [逐题结果与延迟](e3/query-results.md) · [含段落 ID 与过滤条件的 TSV](e3/query-results.tsv) |
| 12 个发行人事件 | 20 轮用户回答中，15 轮通过、2 轮部分通过、3 轮失败。可对照评分原句核实哪些内容真正交付。 | [逐轮结论](issuer-study/turn-grades.tsv) · [实际用户回答](issuer-study/user-answers.zip) |

E1 复评了版本 `503c149` 的历史答卷。E2、E3 和 12 事件使用 [4538c9c 的源码](https://github.com/huxiaobang/VeriFin-Group6/tree/4538c9ce0905976bec3ef5c1c1af37607f2720c2)。报告第 8 页和[逐卡配对得分](skills/card-grades.tsv)记录后续 Skill 实验。评测包中的人工核对仍待组员完成。

## 最新候选修复

[候选 38 的代码差异](candidate-38/release-fixes.patch)以 `4538c9c` 为基线。[测试记录](candidate-38/suite.log)显示当时通过 1,315 项测试，[验证清单](candidate-38/validation.json)给出基线和补丁摘要。已知故障修复后的结果需要与上面的原始得分分开解读。日常运行和演示继续使用[组友发行分支](https://github.com/huxiaobang/VeriFin-Group6/tree/release/team-runtime)。

## 交稿前请组员核对

读一份实际回答，对照原始披露记录同意或不同意 AI 评分的原因、原文位置和日期。请同时核对报告中的本人贡献及 AI 使用说明。课程提交仍待完成。
