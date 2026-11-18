# FinDVer 完整报告检索实测：BM25、Qwen dense 与 hybrid

**这轮真实实测没有支持“加入 dense 或等权融合就一定更好”。** 在预先冻结的 9 份完整报告、24 条公开标注 claim 上，BM25 的 evidence-block Recall@5/@10 高于 Qwen3-Embedding-0.6B dense 和等权 RRF hybrid。应保留 BM25 作为强基线；Qwen 继续作为候选，而不能仅凭先前 10 个自选基础问题把 dense/hybrid 定成默认优胜方案。

这不是宣布该模型在所有财务任务上较差。样本只有 24 条、来自 9 个 report group，使用的是公开 evidence IDs 的匹配指标；部分非 gold 段落也可能提供有效证据。本轮没有重新标注这些替代证据，没有调参后重跑，也没有作统计显著性或端到端 Agent 正确率声明。

## 实际运行范围

- 数据：[FinDVer](https://github.com/yilunzhao/FinDVer/tree/e8bb237def4ce555a606a45edba22666e31df248)，固定 revision `e8bb237def4ce555a606a45edba22666e31df248`。使用已冻结的 report 分层选择，保留所选报告在 testmini 的全部 claim：IE 15、numeric 5、knowledge 4。
- 共 2,971 个原始 paragraph/table blocks，2,544,238 个字符；全部编码，没有先按 gold 选择候选，没有切成较短的自选证据片段。
- 这些是上游已预处理报告里的原 context blocks，包含 Markdown 表格；不是本轮 HTML adapter 的输出。因此本实验不验证 HTML 摄取到检索的端到端链路。
- 每条 claim 的检索范围是数据集指定报告的**所有 blocks**，候选数为 127–463。9 份报告全部参与编码；这不是跨所有公司做报告发现的实验。
- 本地模型 `Qwen/Qwen3-Embedding-0.6B`，revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`；Sentence Transformers 真实推理，CPU FP32、Torch intra-op 4 threads、inter-op 1。没有生成模型 API 调用。
- 模型可见内容只有固定英文检索 instruction、statement 和原 block 文本；不编码官方 claim ID、label、gold evidence IDs 或解释。模型输入用内容派生 opaque ID；原 ID 只出现在 scorer 结果。
- dense 为单位向量的完整 cosine exact scan；hybrid 为 `1/(60+rank)` 等权 RRF，融合完整正分数 BM25 list 与完整 dense list，没有 candidate cap。
- 指标单位是上游公开 evidence block，报告 @5 和 @10。没有统一的字符预算，因此同时报告 context 字符数。不能直接与此前 600-character chunk 的 E3 开发结果比较绝对值。

使用真实换行的固定 query prefix 为：

```text
Instruct: Given a financial research question, retrieve relevant passages from company financial reports.
Query: <statement>
```

## 结果

| 方法 | Recall@5 | MRR@5 | 全部 gold 找齐@5 | Recall@10 | MRR@10 | 全部 gold 找齐@10 |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | **0.5410** | 0.7847 | 4/24 | **0.6743** | **0.7907** | **8/24** |
| Qwen dense | 0.4076 | 0.5153 | 4/24 | 0.5479 | 0.5426 | 4/24 |
| Equal RRF hybrid | 0.5132 | 0.7861 | 4/24 | 0.6590 | 0.7861 | 7/24 |

Recall 是先计算每条 claim 找回的公开 gold blocks 比例，再按 claim 平均；MRR 是第一个公开 gold block 的倒数排名。24 条 claim 的 gold 数为 1–5，因此 @5 和 @10 的 Recall、all-evidence 理论上限均为 1，低结果不是由 `gold_count > k` 造成。

按公开任务子集拆分的 Recall@5：

| 子集 | 条数 | BM25 | Dense | Hybrid |
|---|---:|---:|---:|---:|
| Information extraction | 15 | **0.5656** | 0.4022 | 0.4878 |
| Numerical reasoning | 5 | **0.7000** | 0.6000 | **0.7000** |
| Knowledge-intensive | 4 | 0.2500 | 0.1875 | **0.3750** |

knowledge 子集只有 4 条，hybrid 的局部改善不能作为整体显著提升的证据。三种方法在 @5 都只为 4 条 claim 找齐全部公开 gold，说明单次 top-5 检索不足以替代多事实任务的证据补全。

## 上下文和 CPU 成本

| 方法 | top-5 平均字符 | top-5 最大字符 | top-10 平均字符 | top-10 最大字符 |
|---|---:|---:|---:|---:|
| BM25 | 5,426 | 12,568 | 10,604 | 23,879 |
| Dense | 4,961 | 8,331 | 10,190 | 20,251 |
| Hybrid | 5,046 | 10,209 | 10,434 | 21,971 |

所有 block 的实际 tokenizer 长度合计 621,818，median 147、P90 359、最大 4,396，均在模型的 32,768 限制内。使用长度递增分桶，短块 batch 8，随后按长度降到 4/2/1；完整输入无静默截断。最长原 block 为 8,640 字符。

- Passage 编码耗时 **882.1 秒（14.70 分钟）**；24 个 query 合计 **3.10 秒**。
- 本次全进程 wall time **894.5 秒（14.91 分钟）**，峰值 RSS **2.42 GiB**。这是单次本机观察，包含模型与运行库，不是跨硬件吞吐基准。
- 实际持久化矩阵为 `2971 × 1024` float32，约 **11.61 MiB**；query 矩阵约 96 KiB。保存的是实际模型向量，不是随机、手设或 hash embedding。

工程含义是：**应在摄取阶段预建并保存索引**。不能让普通用户的第一次检索在请求内等待约 15 分钟。当前规模的向量矩阵很小，ANN/向量数据库不是这次慢的主要原因；冷启动全文编码才是主要成本。日后增量索引应按模型/前处理/文本 hash 复用已编码 block。

## 具体例子及解释边界

1. **TriNet 余额比较（`ie-val-10`）。** claim 比较两个时点的 cash/equivalents/investments。BM25 top-1 是相应余额表 `context-27`；dense top-1 是三个月现金流量表 `context-3`，@5 没有找回公开指定的两个证据块。这是相关金融主题与正确指标/期间的区别，不能靠公司或文档级日期过滤解决。
2. **HealthEquity 风险（`knowledge-val-198`）。** claim 涉及 HSA 托管/保险伙伴及不利事件。BM25 top-1 是相应风险段落 `context-70`；dense top-1 是现金流量表 `context-295`。本次输入没有被截断，因此不能把该错位归因于截断。
3. **存在未被 gold 覆盖的相关内容。** Blade 的 `ie-val-111` 在 dense @5 上公开 gold recall 为 0，但 dense top-1 `context-183` 确实讨论第三方运营商及其承担的保险成本，可能支持 claim 的部分核验。这里不能把“非 gold ID”直接改写为“完全无关”。仍按原 benchmark 计分，保留此定性观察，不事后扩充标签来抬高分数。
4. **Dense 也有帮助的情况。** `ie-val-73` 的多个税务/诉讼事实，dense @5 公开 gold recall 为 2/3，BM25 为 1/3；equal RRF 又回到 1/3。融合本身不是单调增益操作。

这些是观察到的行为，不是已证明的根因。长 block 的单向量表示、任务 instruction、金融术语与数字匹配、表头/段落上下文都可能影响结果；需要预先定义后续消融并在其他 report group 上验证，不能在这 24 条上反复调整后称为独立测试成绩。

## 对当前技术选型的修正

1. **保留 lexical 检索的地位。** BM25 是必须保留的强基线；BM25S 的已验证替换原型可以改实现效率，不改变这项结论。
2. **Qwen 暂时作为候选。** 先前小语料中较好的多语言结果仍有参考价值；这轮不同公司、完整报告、公开 gold 的结果显示了迁移风险。不能把单一小基准排名等同于已完成最终选型。
3. **检索和核验分别设计。** 期间、单位、actual/outlook、balance/flow 等需由来源结构与 claim 核验明确处理。小模型换成大模型不能作为这些约束的替代。
4. **优先检验候选召回与证据补全。** reranker 只能重排已召回候选；Planner 对复合 claim 分解查询、Critic 对缺失支持发起补充检索，才与 Proposal 的 Agent 闭环相关。本文没有验证该闭环的实际收益。

## 复现与验收

代码：`scripts/benchmark_findver_retrieval.py`。完整结果：`data/evaluation/open_source_results/findver-retrieval.json`；独立输出核验：`findver-retrieval-validation.json`。

核验确认：BM25@5 完全复现先前 `export-v2/audit.json`；所有结果 ID 均属于该 claim 的完整报告候选；@5 是 @10 的前缀；字符计数与原 block 一致；无重复候选；两套矩阵全部 finite、单位范数最大误差约 `6e-8`；向量文件和执行脚本 hash 与结果一致。没有为这些验收再次调用模型。

```bash
python scripts/benchmark_findver_retrieval.py \
  --data-dir ../../work/open-source-audit/findver \
  --selection ../../work/open-source-audit/findver/frozen-mini-stratified.json \
  --model-path ../../work/embedding-candidates/qwen3-0.6b \
  --cache-dir ../../work/open-source-audit/findver/findver-embedding-cache \
  --output data/evaluation/open_source_results/findver-retrieval.json
```

脚本在截止时间前可停止并保留 checkpoint；未完成全量编码时明确输出 `partial_encoding_no_dense_or_hybrid_metrics`。本次运行状态为 **completed**，全部 2,971 blocks 与 24 queries 已处理。使用缓存重跑时 passage 编码时长来自已完成批次，不能把新的短 wall time 当成首次建索引成本。
