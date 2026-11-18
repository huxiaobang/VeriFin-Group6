# VeriFin：更强检索模型与开源复用方案（2026-11-06）

本次结论：**上一轮 E5-small / BGE-M3 / Qwen3-Embedding-0.6B 的实验有诊断价值，但不足以支撑最终技术选型。** 它只比较了三种单向量文本编码器，样本也过小；没有覆盖 2026 年的新模型、较大模型的本地量化路径、重排、多向量或上下文检索。不能把“当前 CPU FP32 加载器只能方便地装下较小模型”写成“项目最适合较小模型”。

本轮已真实验证两个具体增量：**Qwen3-Reranker-0.6B 的两阶段检索**，以及 **PPLX v2 late 0.6B 的多向量检索**。目前没有足够证据将其替换为默认方案：重排未提高覆盖，PPLX 的召回点值改善仍伴随较宽区间和更高计算/索引成本。先修复共同表格表示问题，再将 **官方 Qwen3-Embedding-4B Q4_K_M** 纳入下一轮强 dense 基线；它有官方 GGUF，不能因 16 GB 内存直接排除。这里的“值得验证”不等于“已证明更好”。

## 1. 证据口径与本轮边界

- 检索时间为 2026-11-06；模型固定 revision、许可、权重字节数见同目录 `OPEN_SOURCE_MODELS_2026-11-06.sources.json`。
- 官网的历史榜单、不同 benchmark 的厂商自评、我们的本机实测分开记录。Qwen 文档中的 MTEB 第一明确对应 **2025-06-05**；不能引用为 2026 年 11 月的现时排名。[Qwen 官方仓库](https://github.com/QwenLM/Qwen3-Embedding)
- `small`、参数数目、向量维度和权重文件大小都不是准确率。MRL 降维、INT8 输出向量、模型权重量化是三种不同操作。
- 本机是 M1 Pro、16 GiB 内存。本轮新实验采用 CPU、FP32、4 个计算线程。没有把未运行的 Metal、MPS、MLX、GGUF 或付费 API 写成本机测试结果。
- 下载只访问公开固定版本，不使用用户 token。实际编码使用离线本地 safetensors；本子任务仅新增研究和实验文件。**已发布 v0.4 归档保留**，本轮主分支的源码增量另行验证并发布新版本。

## 2. 候选与采纳判断

| 候选 | 它解决什么问题 | 许可与接入事实 | 本项目判断 |
|---|---|---|---|
| Qwen3-Embedding-4B | 更强的多语言单向量基线，较少改变检索语义 | Apache-2.0；标准 Transformers / ST，query instruction、last-token pooling；官方 BF16 权重约 8.04 GB | **必须补测的强 dense 基线**。本机优先官方 Q4_K_M + llama.cpp / Metal；另记量化和运行时，不能与 FP32 时间混算 |
| Qwen3-Embedding-8B | 检验更高容量是否还有收益 | Apache-2.0；标准模型，官方 BF16 权重约 15.13 GB | 不是本机全精度默认项；待 4B 确有增益后再测试量化或外部机器，不能假定“8B 必然值得” |
| Jina embeddings v5 text small | 2026 年的新蒸馏训练方案，在亚十亿参数预算内挑战旧 dense 模型 | CC-BY-NC-4.0；677M；retrieval LoRA、`Query: ` / `Document: `、last-token pooling；官方 HF 路径需要自定义代码 | **值得下一轮 dense 对照**。先做本地代码审阅适配，保留 LoRA 身份；不能直接放宽生产环境的 `trust_remote_code`，也不能称为无商业限制的开源 |
| PPLX embed v1 4B / context v1 | 双向注意力、扩散式继续训练；context 版本利用同文档邻近内容 | MIT；自定义模型；mean pooling；原生 INT8 输出向量，需 cosine 或归一化；4B 官方 FP32 权重约 16.09 GB | 有技术参考价值，但本机优先级低于官方 Qwen 4B 量化路径；INT8 向量不会自动令 16 GB 权重变小 |
| PPLX embed v2 late 0.6B | 每 token 多向量 + MaxSim，减少将整段压入一个向量的损失；后续可探索视觉财报 | MIT；ST 6 原生 `MultiVectorEncoder`，无需下载 Python；128 维/token，`[Q] ` / `[D] `；实际权重约 2.38 GB | **本轮真实实验项**。它是新的索引与评分方案，不能只换现有 model ID。先测文本，暂不宣称视觉能力在 VeriFin 生效 |
| PPLX embed v2 context 0.6B | 整份文档上下文内生成分块表示，支持文本与图像 | 精确仓库未声明 license，需进一步确认；自定义代码；不重叠 chunk，query / document 编码接口不同 | 保留为上下文检索研究项。须改全文缓存身份与 chunk 协议，不能套入现有 600/80 overlap 流程后声称已完成支持 |
| EmbeddingGemma 2 | 本机可运行的文本/图像等统一向量，面向视觉材料的低成本路线 | Apache-2.0；740M 总参数、可选择加载模态；768 维；标准库支持 | 作为多模态资源对照；官方文本成绩不构成比 Qwen 更适合财务文本的证据。当前不为增加模型数量而重复下载 |

资料：[Qwen 4B](https://huggingface.co/Qwen/Qwen3-Embedding-4B)、[Qwen 8B](https://huggingface.co/Qwen/Qwen3-Embedding-8B)、[Jina v5 模型卡](https://huggingface.co/jinaai/jina-embeddings-v5-text-small)、[Jina 技术报告](https://arxiv.org/abs/2602.15547)、[PPLX v1](https://huggingface.co/perplexity-ai/pplx-embed-v1-4b)、[PPLX v1 技术报告](https://arxiv.org/abs/2602.11151)、[PPLX v2 late](https://huggingface.co/perplexity-ai/pplx-embed-v2-late-0.6b)、[PPLX v2 context 固定版本](https://huggingface.co/perplexity-ai/pplx-embed-v2-context-0.6b/tree/6d1fddab0a18478acc0a51edd1e81333fe441095)、[EmbeddingGemma 2 发布说明](https://blog.google/innovation-and-ai/technology/developers-tools/embeddinggemma-2/)。文件大小是公开文件清单，不是实测峰值 RAM。

### 为什么 4B 本地路线合理，但本轮不冒充已经测过

Qwen 官方提供 `Qwen3-Embedding-4B-Q4_K_M.gguf`，固定版本文件为 **2,496,703,776 bytes**。这是本机可行性值得实测的依据；权重以外还需要运行时、工作缓冲和系统内存，不能据此直接承诺峰值 RAM。模型逻辑仍应采用官方 instruction 和 pooling，并校验后端输出、无意截断、批次独立性与缓存身份。[官方 GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF)

建议保持一个小型 backend adapter 接口，而非迫使所有模型使用 `SentenceTransformer(..., dtype=float32)`：明确模型 revision、权重 hash、量化格式、后端版本、device、prefix、pooling、维度。先用相同公开样本比召回，再报告本机延迟和内存。若 Q4 有增益再考虑 Q5 / Q8；不能将量化差异归因于参数量本身。本轮没有下载或执行该 4B 模型。

### 为什么 Jina 值得研究，而不是直接切换

Jina v5 在 2026-02-18 发布，使用 Qwen3-Embedding-4B 蒸馏和任务适配。它提供了比“只增大参数”更合适的受限硬件对照。官方模型卡与较早发布文章的 benchmark 数字并不完全相同，不能把不同时间或口径混在一起构造排名。[官方发布文章](https://jina.ai/en-US/news/jina-embeddings-v5-text-distilling-4b-quality-into-sub-1b-multilingual-embeddings/)

本轮只阅读了固定版本的约 8 KB 模型封装，没有执行远程代码。封装选择任务 LoRA，并存在非本地路径触发下载的分支。后续适配需固定 retrieval adapter、检查截断、将模型和 adapter 一同计入缓存指纹，保证离线可复现。`trust_remote_code=True` 是当前官方加载路径的事实，不能因此宣布模型不可用，也不能因此全局取消限制。[固定版本源代码](https://huggingface.co/jinaai/jina-embeddings-v5-text-small/tree/dd76d535f5447ca3897a9c893fb1e612ead98192)

## 3. 重排不是再换一个 embedding

| 候选 | 使用位置与成本 | 本次决定 |
|---|---|---|
| Qwen3-Reranker-0.6B | 固定候选 top-20 → top-5；query / document 成对前向计算，以官方 yes/no logits 评分；Apache-2.0，多语言 | 本轮优先真实验证；同时报告候选池 Recall@20、top-5 收益和新增延迟。分数不是已经校准的事实真实性概率 |
| Jina reranker v3.5 | 2026-07 发布，0.6B listwise 排序；CC-BY-NC-4.0、自定义代码 | 值得后续比较，但 listwise 与 pairwise 的候选规模和 token 预算需对齐；厂商表格并未在所有数据集胜过较大 Qwen |
| Ettin reranker 400m v1 | 2026-05 发布，Apache-2.0、标准 ModernBERT CrossEncoder，含官方 ARM64 ONNX INT8 文件 | 英文查询的速度候选；其英语 benchmark 不证明中文适用，不能据此直接替换双语默认项 |

资料：[Qwen reranker](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)、[Jina v3.5](https://huggingface.co/jinaai/jina-reranker-v3.5)、[Jina 技术报告](https://arxiv.org/abs/2607.18152)、[Ettin 模型卡](https://huggingface.co/cross-encoder/ettin-reranker-400m-v1)、[Ettin 发布与实验](https://huggingface.co/blog/ettin-reranker)。

排序器无法找回未进入候选池的证据。因此先看 candidate recall，再判断重排收益。如果 dense / BM25 本身漏掉某个财务表格，单独提高 reranker 分数无济于事；也不能用 reranker score 代替 Critic 的单位、期间、实体与引用检查。

本轮 Qwen reranker 已跑完同一 100 轮：Recall@5 为 **80.82%**，低于重排前的 81.37%，每题额外 warm scorer 中位耗时 **4.62 s**，因此不默认采用。完整固定协议、独立复核及案例见 [Qwen 重排实测报告](OPEN_SOURCE_RERANKER_2026-11-06.md)。这也不证明该模型家族普遍无效；当前表示和输入方式是实验条件的一部分。

## 4. 多向量和上下文模型真正需要改什么

PPLX v2 late 于 2026 年 10 月公开发布，是基于 Qwen3.5 的双向多向量模型；官方小模型的“340M active”与约 0.6B 总参数不是同一概念。其标准输出是一串 128 维 token 向量，评分采用对每个 query token 取文档最大相似度后求和。这个契约不能用现有单向量 cosine 假装兼容。[官方发布文章](https://www.perplexity.ai/hub/blog/multimodal-embeddings-beyond-a-single-vector)、[技术报告](https://arxiv.org/abs/2610.29523)

本轮实验遵从官方 `[Q] ` / `[D] ` 和原生 token mask，不自行改造成 PyLate 默认格式。逐 token 归一化、独立 MaxSim 公式对照、导出后精确回读构成必要校验。记录 token 数与真实向量字节数；128 维不表示索引一定比 1024 维单向量更小。

Sum-MaxSim 的原始分数随 query token 数增加，不能跨问题直接套用同一个拒答阈值。按 query 长度取平均可以改变分数尺度，但不改变同一 query 的排序，也不能自动获得可靠的无答案判断。

对 VeriFin 的设计推断是：逐 token 匹配仍不等于财务关系校验。例如一个表格同时包含 2023、2024 和毛利率，模型可以分别命中这些词，却不能因此证明某个数值属于正确年份。表格单元格、期间与单位关联仍应保留给证据表示、计算工具与 Critic 检查。

如果后续采纳，工程上至少要有：多向量存储与检索接口、成本受控的候选筛选、不同长度的公平评估、token mask 配置和模型 fingerprint。视觉财报还需要 page / region 级溯源、渲染或解析稳定性和视觉检索的独立标签。当前文本实验不覆盖这些视觉要求。

PPLX v2 context 的顺序分块需要**不重叠**，且块向量依赖整份父文档。已有“单块文本 hash → embedding”缓存会在邻近段落变化后错误复用，必须将父文档和块顺序计入身份。官方 query 编码接口也与 document 编码不同。这些都是适配成本，并非简单增加模型名称。[官方固定版本说明](https://huggingface.co/perplexity-ai/pplx-embed-v2-context-0.6b/blob/6d1fddab0a18478acc0a51edd1e81333fe441095/README.md)

## 5. 公开样本与本轮真实实验

### 冻结协议

新增公开 ConvFinQA 开发集子集，按来源页分组冻结 **100 个对话轮次、18 个来源页、550 个不同的原始文本块**。组选择在本轮结果产生前固定。每题候选为原始 supplied context 的全部表格行和文本句，不按答案筛选。仅在 scorer 中使用官方 evidence locator；这不是人工新增标注。

对照 current-question 与 previous-questions + current-question 两种输入，共 162 个不同 query；不输入 reference answers 或未来问题。主指标为 source-ID macro evidence recall@5、all-evidence fraction、MRR@5，同时报告上下文字数。它测量已提供财报片段内的证据检索，不测来源发现、完整财报搜索或最终 Agent 回答正确率。100 个轮次是相关样本，不能当作 100 份独立财报。[ConvFinQA 官方仓库](https://github.com/czyssrs/ConvFinQA)

同一冻结数据、query 生成函数与 scorer 被 Qwen baseline、reranker 与本轮 MaxSim 复用。MaxSim 对每题所有 eligible 原始块精确评分；附加 BM25 + MaxSim 使用 equal-weight RRF（k=60），它仍不是 BGE-M3 官方 learned sparse 或 multivector 模式。

这是**整套检索方案的对照**。PPLX 与 Qwen 的训练数据、骨干架构、输入约定和表示方式同时不同；即使出现增益，也不能把增益单独归因于“多向量”这一因素。

**已发现共享表格表示问题，必须先修表示再作最终选型。** 对照官方 MA / FRT 源表发现，当前 `public_benchmarks._blocks` 无条件将 `table[0]` 作为表头，但部分首行其实是余额数据，产生了类似 `$476915: 404436` 的数值列名。这会将首行金额混入后续行的表示。为保持本轮冻结协议，此时不改源块；下列所有模型共享这份表示，数值仅能作为该表示下的开发诊断，不能将错误全部归因于模型。下一轮应先正确区分表头与数据行、保留列的期间关系，再统一重跑；该修正优先于继续增加模型规模。

### 已完成：同一公开样本上的三种 dense 模型

已新增 E5-small 和 BGE-M3 的真实离线 CPU FP32 编码，直接复用同一个 source loader、纯 newline 历史问题、候选池与 scorer。下表均为带历史问题、top-5；每项的分母为相同 100 个轮次。

| 模型 | Dense Recall@5 | Dense MRR@5 | BM25 + Dense Recall@5 | BM25 + Dense MRR@5 | 融合后全部证据覆盖 |
|---|---:|---:|---:|---:|---:|
| E5-small | 79.46% | 0.7847 | 81.49% | 0.8073 | 62% |
| BGE-M3（dense-only） | 74.85% | 0.7193 | 82.26% | 0.7587 | 63% |
| Qwen3-Embedding-0.6B | 80.70% | 0.8425 | 81.37% | 0.8200 | 62% |

共同 BM25 基线为 Recall@5 **84.06%**、MRR@5 **0.7675**、全部证据覆盖 **68%**。这份英语 supplied-context 数据有很强的词面线索；它不能替代中文跨语言或全财报检索评估。纯 top-5 的 oracle 上限是 macro recall 99.714%、全部证据覆盖 99%：其中一题有 7 个官方证据块，预算为 5 时不可能全部返回。

| 模型 | 550 块首次编码 | 162 query 批量编码（batch=8） | 实际 encoder 进程峰值 RSS |
|---|---:|---:|---:|
| E5-small | 3.02 s | 0.74 s | 1.01 GiB |
| BGE-M3 | 24.90 s | 5.98 s | 1.82 GiB |
| Qwen3-Embedding-0.6B | 48.99 s | 13.97 s | 2.24 GiB |

Qwen 文档时间取首次真实编码记录，纯 newline 版本复用了经 hash 验证的相同文档向量；query 时间来自新版本的真实编码。上述 query 时间是批量吞吐，不能解释成单次用户交互延迟。RSS 取实际模型进程，而非仅加载 JSON 的 rescore 进程。

结论是**三者没有在本样本中形成一致的质量胜者**：BGE 融合 recall 略高但 MRR 较低；Qwen 的 dense MRR 较高；E5 的成本显著较低，但本英语样本不能证明它足以支持双语目标。相对 Qwen，E5/BGE 融合 recall 的点差分别只有 +0.119 / +0.893 个百分点；按 18 个来源页整组 bootstrap（5,000 次）的 95% 区间分别为 [-8.45, +6.96] / [-5.56, +5.97] 个百分点，均跨 0。这是开发诊断区间，不是未见数据上的显著性证明。

实际向量、每题排序、资源与检查存于 `data/evaluation/open_source_dense_public/`；`comparison.json` 核对了来源、冻结选择、query 格式、case ID、候选数、逐题 BM25 输出，以及新向量文件 hash、数量、有限值、单位范数与原文 hash。`scripts/open_source_dense_public.py` 和 `scripts/open_source_dense_summary.py` 可复跑此补充实验。

### 已完成：PPLX 多向量实测与独立复算

2026-11-06 完成同一 100 轮实验。2,377,321,184-byte 主权重与官方 LFS SHA256 `107eaae48d026ea20458b30dd755a0f1475b34fcf5a1895123ad67a2385b9d6b` 完全匹配；使用原生 ST `MultiVectorEncoder`、CPU FP32，不执行下载的 Python，不输入图片，不调用模型 API。

| 带历史问题的方法 | Recall@5 | 全部证据覆盖 | MRR@5 |
|---|---:|---:|---:|
| PPLX Sum-MaxSim | 83.68% | 64% | 0.8215 |
| BM25 + PPLX MaxSim RRF | 83.51% | 65% | 0.8177 |

相对 Qwen dense，MaxSim recall 增加 2.98 个百分点，18 组 bootstrap 95% 区间为 **[-0.52, +9.30]** 个百分点；相对 Qwen hybrid，PPLX 融合增加 2.14 个百分点，区间为 **[-0.64, +6.02]**。两者区间均跨 0，且未超过同协议 BM25 的 84.06% recall；因此保留研究价值，不据此替换默认实现。MRR 也没有形成一致改善。

真实成本为：550 块编码 **146.73 s**，162 query 的 batch-8 编码 **44.32 s**；单 query 编码 median **923 ms**、p95 **1,038 ms**。本机 `causal_conv1d` 与 `chunk_gated_delta_rule` 使用正确但较慢的 reference PyTorch fallback，不能把这些数值当作优化 CUDA 或 Metal 的性能。峰值 RSS **1.98 GiB**。

文档共有 **19,064** 个 token 向量，FP32 原始索引 **9,760,768 bytes**；同 550 块、1024 维单向量的 FP32 参照为 **2,252,800 bytes**，约 **4.33 倍**。每块 token 向量 median 30、max 277。完整 query + document NPZ 为 11,623,044 bytes，不能拿含 query 的压缩文件大小冒充文档原始索引。

MaxSim 排序 median **0.714 ms**，但这里每题只评分 **12–52** 个 supplied-context 候选（median 29），不能外推为全库检索延迟。单 query 与 batch-8 向量最大绝对差 5.36e-7；native MaxSim 与独立公式一致，NPZ 回读逐值一致。随后不加载模型，用 NumPy 从导出向量独立重算 **400 个 top-5 排序**，全部 passage IDs、逐题指标与汇总完全相同；验证文件为 `data/evaluation/open_source_late_public/independent-rescore.json`。

旧版四份财报的 34-query 脚本也已准备，但它的 30 个正例只对应 10 个独立信息需求，中文/同义改写相互相关；即使后续跑完，也只作错误类型诊断。负例中 metadata-empty 控制与语义无答案分开：未设置拒答阈值的 top-k 必然返回候选，这不能作为 embedding “准确率”扣分。

### 可复现交付

- `scripts/open_source_late_public.py`：同一公开 ConvFinQA 子集，真实权重、CPU FP32、原生 MultiVectorEncoder、MaxSim 与 BM25 融合。
- `scripts/open_source_late_interaction.py`：旧版 34-query 开发诊断；保留相同 600 字符 / 80 overlap 语料。
- `scripts/open_source_late_rescore.py`：从导出向量独立复算全部 top-5，检查 corpus / artifact hash；仅与明确采用纯 newline history 的基线做配对，按来源页整组重采样报告开发样本区间。
- `data/evaluation/open_source_late_public/pplx-v2-late-0.6b-convfinqa-results.json`：完成后记录固定来源、模型文件 manifest、指标、索引成本、延迟、峰值 RSS。
- 配套 `.npz`：保存实际 ragged token vectors，以 `allow_pickle=False` 回读；允许离线重新评分，不需要反复推理模型。
- 权重传输采用固定 revision + HTTP Range 校验，每一片检查 `Content-Range`，组装后比对官方 LFS SHA-256。临时 range 文件与下载元数据不参与模型 fingerprint。

独立实验环境已安装 Python 3.12.14、PyTorch 2.15.0、Sentence Transformers 6.2.0、Transformers 5.22.1、NumPy 2.5.4；PPLX 的 processor 另需 Pillow 13.0.0 和 torchvision 0.29.2，后两项安装在独立实验目录，未将视觉依赖强加给原 demo。

续传脚本已保存到仓库 `scripts/open_source_download_public.py`。它不使用用户 token；失败分片会报告类型，重启复用已完整写入的分片，最终仍以官方整文件 SHA 验收。先按 [可复跑说明](REPRODUCE_OPEN_SOURCE_STUDY.md) 获取数据和安装实验依赖，将上述固定 revision 的完整原生模型配置/权重放入 `work/models/pplx-embed-v2-late-0.6b`。以下命令在仓库根目录执行；第一条重新推理，第二条仅审计发布包中已有的结果，均写入新的 work 路径：

```bash
python scripts/open_source_late_public.py \
  --data work/public/convfinqa/dev_turn.json \
  --selection data/evaluation/public_convfinqa_selection.json \
  --model work/models/pplx-embed-v2-late-0.6b \
  --output work/pplx-late

python scripts/open_source_late_rescore.py \
  --data work/public/convfinqa/dev_turn.json \
  --selection data/evaluation/public_convfinqa_selection.json \
  --result data/evaluation/open_source_late_public/pplx-v2-late-0.6b-convfinqa-results.json \
  --baseline data/evaluation/open_source_results/convfinqa-qwen0.6-canonical-baselines.json \
  --output work/pplx-late-rescore.json
```

## 6. 哪些现成资源应复用

1. **Sentence Transformers 原生 Encoder / MultiVectorEncoder / CrossEncoder**：分别承载 dense、MaxSim 与重排，避免自己拼错 pooling 或官方输入格式。它们的输出契约仍需在本项目边界上明确。
2. **官方 Qwen GGUF + llama.cpp**：作为 Apple Silicon 上更大 dense 模型的独立后端；固定后端版本和量化格式，保留与标准权重基线可区分的结果。
3. **公开金融数据集的官方证据定位**：先用 ConvFinQA 等来源现成的 evidence labels 扩大覆盖，保留其 provided-context / development 属性，不能把自动转换叫作新的人类验证 gold。
4. **官方模型卡与固定代码 revision**：模型名不足以复现；任务 adapter、prefix、pooling、量化、截断策略与索引版本均应进入实验身份。

可选 API 参照为 Voyage 4 家族。厂商文档明确其家族内共享空间，可用较大 document 模型与较小 query 模型搭配；这不意味着任意两个 embedding 模型可以混用。其 2026-01 的排名宣称只作当时的外部依据；本轮没有调用 API，也没有取得其在 VeriFin 上的效果。[Voyage 4 官方发布](https://blog.voyageai.com/2026/01/15/voyage-4/)

## 7. 选型验收标准

最终默认模型应由固定样本上的配对结果决定：候选召回、最终 top-5 证据覆盖、中文/英文及多轮错误类型，再结合实际本机延迟、索引大小、RAM、许可和维护成本。应独立报告排序后仍存在的期间/单位/表格关联错误。

若新模型改善明显且成本可接受，进入默认候选；若只改善特定类问题，作为条件路线；若仅官网总分高或小样本多命中一题，继续保留为候选。**本轮扩展了研究范围和可复现实验，不提前宣布最终获胜模型。**
