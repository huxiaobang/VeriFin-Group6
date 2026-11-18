# VeriFin 本地 embedding 选型研究与实测

日期：2026-11-06。状态：**已完成三种真实本地模型的开发诊断；尚未确定正式实验的最终模型。** 本文不修改已提交的 Proposal，也不把开发集结果当作正式 E3 或独立人工标注的结论。

## 结论与当前取舍

用户担心“小 embedding 会漏掉关键证据”有实际依据，但参数量本身不足以决定选型。此次相同财报、分块和查询上的实测中，Qwen3-Embedding-0.6B 的中文查询召回优于 E5-small；相近规模的 BGE-M3 并未全面胜过 E5-small。换成更大的模型，也没有消除期间混淆或让混合检索在每种查询上都优于 BM25。

当前建议把 **Qwen3-Embedding-0.6B 作为质量优先的下一轮候选，E5-small 保留为速度与资源基线，BGE-M3 保留为对照**。这是继续试验的优先次序，不是“Qwen 已被证明最优”。在 30 条相关查询表达中，Qwen 的 hybrid 比另外两者仅多覆盖 1 个标注片段，且这些查询实际只对应 10 个基础问题；不能声称差异显著。交互中的单次查询约 0.2 秒，本机资源可承担 Qwen 候选，但首次索引应在准备阶段预建或增量缓存。

embedding 负责找候选证据。它的相似度不是证据成立概率，也不替代财务期间、单位、数值与因果关系的核验。

## 候选为什么这样选

| 候选 | 本地实测参数量 / 向量维度 | 上下文上限 | 推理约定与在本项目中的用途 |
|---|---:|---:|---|
| multilingual-e5-small | 117,653,760 / 384 | 512 tokens | 查询加 `query: `，文本加 `passage: `；多语言低资源基线。短上下文使分块与截断检查尤其重要。 |
| BGE-M3 | 567,754,752 / 1,024 | 8,192 tokens | 不加查询 instruction；本次只用 dense 向量，检索混合采用项目自己的 BM25 + RRF。 |
| Qwen3-Embedding-0.6B | 595,776,512 / 1,024 | 32,768 tokens | 查询用英文任务 instruction，文本不加 instruction；质量优先候选，使用官方 last-token pooling。 |

E5 的输入前缀与 512-token 限制来自其[官方模型卡](https://huggingface.co/intfloat/multilingual-e5-small)。BGE-M3 官方支持 dense、learned sparse 与多向量表示；本实现**没有**启用后两者，不能把结果写成“完整 M3 混合检索”的表现。[BGE-M3 官方模型卡](https://huggingface.co/BAAI/bge-m3)

Qwen 的官方用法支持任务 instruction、长上下文与可调向量维度；本次保留 1,024 维，没有压缩维度，也没有量化。不同模型遵守各自训练时的输入格式，比强行使用相同前缀更合理。[Qwen 官方模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)

此前的小型英语模型适合验证加载和接口，不应因“能跑”就成为双语财务检索的最终选择。本次没有下载 4B/8B 模型：先比较本机可用的约 0.1B 与 0.6B 候选，避免在缺少任务收益证据时增加资源成本。这不等于认定更大模型无用。云端 embedding 也仍可作为将来的对照，本轮没有付费 API 调用。

## 实验设计与边界

- 语料是仓库中同一组 **4 份真实财报相关快照、73,728 个字符**，来自 MSFT 与 NVDA。这里是实验样本范围，不是 Agent 支持公司的范围。
- 统一使用 600 字符分块、80 字符重叠，共 158 段；corpus fingerprint 为 `corpus_59f5576b526292b642e7dde9`。所有候选都检查真实 tokenizer 长度，没有静默截断。
- 查询包括既有的 10 个英文正例、对应的 10 个中文表达、10 个英文改写，以及 3 个 metadata 空集控制项和 1 个语义无答案控制项，共 34 条。新增表达由 agent 编写，继承原开发标注，不是新增的独立人工 gold。
- 每个正例原本仅指定一个来源片段，可能存在未穷尽的其他有效证据；严格片段覆盖率不能等同于完整答案正确率。
- 三模型使用相同 entity、document report date、cutoff 过滤，top-k=5、上下文上限 3,000 字符。dense 使用归一化 cosine，hybrid 使用当前 equal-weight RRF，k=60，候选上限为 100。没有根据结果调整融合权重。
- 每个模型在独立进程中依次运行：CPU、float32、4 个 PyTorch intra-op threads、1 个 inter-op thread、batch size=8。模型加载后先做一次不计时的查询 warm-up。没有并行运行编码基准。

这组查询只能用于筛选方案与发现问题。它覆盖的公司、期间、问题类型和表格结构都少，30 个正例表达也不是 30 个独立研究任务。

## 实测检索结果

表中每格是 **严格标注片段 Recall@5 / MRR@5**；同一标注片段必须被一个返回段完整覆盖。

| 检索配置 | 原始英文，10 条 | 中文表达，10 条 | 英文改写，10 条 | 30 个表达合计 |
|---|---:|---:|---:|---:|
| BM25 | 1.000 / 0.875 | 0.000 / 0.000 | 0.500 / 0.400 | 0.500 / 0.425 |
| E5-small dense | 0.700 / 0.600 | 0.600 / 0.483 | 0.600 / 0.390 | 0.633 / 0.491 |
| E5-small hybrid | 0.900 / 0.687 | 0.600 / 0.483 | 0.800 / 0.565 | 0.767 / 0.578 |
| BGE-M3 dense | 0.600 / 0.533 | 0.700 / 0.600 | 0.600 / 0.550 | 0.633 / 0.561 |
| BGE-M3 hybrid | 0.800 / 0.667 | 0.700 / 0.600 | 0.800 / 0.528 | 0.767 / 0.598 |
| Qwen3-0.6B dense | 0.900 / 0.633 | 0.800 / 0.700 | 0.700 / 0.475 | 0.800 / 0.603 |
| Qwen3-0.6B hybrid | 0.900 / 0.800 | 0.800 / 0.700 | 0.700 / 0.428 | 0.800 / 0.643 |

几个直接影响选型的发现：

1. **不能用原来 10 个英文查询就判定检索已经足够好。** 它们与原文词面接近，BM25 已满分；同义改写、中文查询暴露了缺口。
2. 当前 BM25 的 tokenizer 没有中文分词或跨语言映射；这些中文查询没有产生可用的词面匹配，hybrid 因而退化为 dense。这里观察的是当前系统，不能推广成“所有 BM25 中文检索都为零”。
3. RRF 不是必然提升质量的步骤。例如 Qwen 的英文改写 MRR 从 dense 的 0.475 降到 hybrid 的 0.428；原始英文 BM25 的召回也高于三个 hybrid。后续需要保留 BM25 baseline，并单独验证融合与重排，不把组合名字当成收益证明。
4. 3 个 metadata 空集控制项在所有配置中均返回空结果。对“斑马鱼与珊瑚共生”这一语义无答案查询，BM25 返回空，dense/hybrid 返回候选。后者本来就是没有拒绝阈值的 top-k 排序，**不能把该返回行为算成 embedding 的综合准确率**；是否有支持性证据，应交给下游核验或经开发集校准的拒绝策略。

## 本机资源与交互代价

主机通过 `sysctl` 核验为 Apple M1 Pro、16 GiB RAM、8 个逻辑 CPU。运行库为 Python 3.12.14、PyTorch 2.15.0、Sentence Transformers 6.2.0、Transformers 5.22.1。

| 模型 | 本地模型文件合计 | 编码 158 段 | 单查询中位 / P95 | 整个进程峰值 RSS |
|---|---:|---:|---:|---:|
| E5-small | 0.49 GB | 2.69 s | 20.5 / 24.4 ms | 0.96 GiB |
| BGE-M3 | 2.29 GB | 21.13 s | 171.6 / 205.2 ms | 2.24 GiB |
| Qwen3-0.6B | 1.21 GB | 53.86 s | 197.6 / 233.1 ms | 2.39 GiB |

文件大小采用十进制 GB，包括 tokenizer/config，排除下载缓存；RSS 采用 GiB，包含 Python、库、tokenizer 与权重，不是纯模型内存。查询时间包含分词、前向计算、归一化，不含检索排序、模型加载或网络；每个不同查询计时一次。没有把单次运行当作稳定的吞吐 SLA，也没有声称这些是 MPS/GPU 数字。

Qwen 的发布权重存储较小，不代表 float32 运行时占用更低。一次初始尝试按配置在 CPU 加载为 bfloat16，未完成整轮后停止；最终三者统一 float32，且结果 JSON 同时记录加载与实际计算 dtype。该尝试没有被当作可比的性能结果。此发现也推动核心 CPU loader 明确选择 float32，避免默认精度造成无谓的性能问题。

## 从失败样例看到的架构问题

真实 E5 结果中，询问 NVIDIA 当季毛利率下滑原因时，排第一的是同一份公布材料中的**下一季度 outlook**，而标注证据讲的是当季向更复杂、成本更高的数据中心系统过渡。文档日期和公司过滤都正确，段落语义的期间仍然错了。

因此应继续强化以下部分：

- 将 actual / guidance、季度 / 全年与单位放进 claim 和 evidence 的核验，而不能只过滤文档日期。
- 财报分块保留表格标题、列头、期间和单位；当前字符分块没有证明能正确保留跨页表格语义。网页快照中的导航残留也会占用候选段。
- 把 reranker 作为独立可评估组件；它只能调整已经召回的候选，不能救回完全未召回的证据。
- 对同一问题允许返回可追溯的替代证据，在正式标注中明确哪些来源/片段算有效；不能把单个开发 span 当成所有正确证据的全集。

## 可复现材料

代码：[benchmark_embedding_candidates.py](../scripts/benchmark_embedding_candidates.py)。配置与结果：

- [固定候选与实验设置](../data/evaluation/embedding_selection_manifest.json)
- [34 条开发查询及标注来源声明](../data/evaluation/embedding_selection_development_queries.json)
- [汇总指标、资源、SHA256 与复核结果](../data/evaluation/embedding_selection_results/summary.json)
- [本次 Python 依赖版本](../data/evaluation/embedding_selection_results/runtime-requirements.txt)
- 同目录的 `*.results.json` 保存逐条查询排名；`*.embeddings.json` 保存本次真实编码的冻结向量，可在不重新运行模型的情况下重算检索指标。三份 artifact 均通过 SHA256、语料绑定、向量加载和指标复算检查。

固定的官方 revisions：

| 模型 | revision |
|---|---|
| E5-small | `614241f622f53c4eeff9890bdc4f31cfecc418b3` |
| BGE-M3 | `5617a9f61b028005a4858fdac845db406aefb181` |
| Qwen3-Embedding-0.6B | `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |

BGE-M3 的该官方版本只提供 `pytorch_model.bin`。实验将它保存在单独的 source 目录，使用 `torch.load(weights_only=True, map_location='cpu')` 读取纯 tensor state dictionary，再转换为 safetensors；没有放开生产 loader，也没有允许自定义 pickle globals 或远程代码。原始 SHA256 为 `b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38`，转换后为 `993b2248881724788dcab8c644a91dfd63584b6e5604ff2037cb5541e1e38e7e`。

从项目目录复现时，先在隔离 Python 环境安装上述依赖，再显式下载指定的公开模型：

```bash
python scripts/benchmark_embedding_candidates.py download --candidate e5-small --models .verifin/embedding-models
python scripts/benchmark_embedding_candidates.py download --candidate qwen3-0.6b --models .verifin/embedding-models
python scripts/benchmark_embedding_candidates.py download --candidate bge-m3 --models .verifin/embedding-models
python scripts/benchmark_embedding_candidates.py convert-bge --models .verifin/embedding-models

python scripts/benchmark_embedding_candidates.py run --candidate e5-small --models .verifin/embedding-models --output .verifin/embedding-study
python scripts/benchmark_embedding_candidates.py run --candidate qwen3-0.6b --models .verifin/embedding-models --output .verifin/embedding-study
python scripts/benchmark_embedding_candidates.py run --candidate bge-m3 --models .verifin/embedding-models --output .verifin/embedding-study
```

下载不发送用户 Hugging Face token；评估阶段设置 offline 模式，使用 `local_files_only=True`、`trust_remote_code=False` 与 safetensors。模型权重和虚拟环境留在本机实验目录，不应放进轻量交接包。

下一轮应增加多家公司、多期间、actual/guidance、同名近义指标、单位和表格查询，并在冻结模型、分块、融合参数后使用独立 held-out 材料作正式 E3。自动扩充的诊断集可以继续由 agent 准备，但不能据此声称已完成独立人工评估。
