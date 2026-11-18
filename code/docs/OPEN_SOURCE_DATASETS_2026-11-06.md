# VeriFin：公开数据与可直接复用的参考标注

核查日期：2026-11-06。该文档记录独立的公开数据实验，不修改已提交 Proposal、原正式评测集或既有发布包。原始下载保存在工作目录 `work/open-source-audit`；仓库只保存接口、测试与审计摘要。

**可以明显减少新增人工工作。** 已接入 ConvFinQA、TAT-QA 和 FinQA 的官方参考答案、证据位置与来源信息，能直接评分答案和证据检索。本模块优先采用 **ConvFinQA + TAT-QA**：前者补多轮依赖，后者补表格与文本、单位和多种答案类型。与 E1 直接相关的公开 claim verification 标签由本轮另一条工作线的 `claim_benchmarks.py` / FinDVer 实验补充，整合时应一起看，不能继续笼统地说“全部 E1 都需要从零人工标注”。

这些 QA 参考答案能评价某项指定答案是否正确，但不是新生成长报告中每条 claim、每次 Critic 反馈或每条 Planner 路径的完整真值。可以用公开标注做可复现的主要客观实验，再把研究结论限定在实际测量的范围。

## 1. 优先级与实际可用范围

| 数据集 | 官方规模与 split | 本次实际读取 | 已有参考标注 | 建议用途 |
|---|---|---|---|---|
| [ConvFinQA](https://github.com/czyssrs/ConvFinQA) | 3,892 个对话、14,115 轮；train/dev/test 为 3,037/421/434 个对话，11,104/1,490/1,521 轮；公开 test 没有答案标注 | 完整 dev 的 421 个对话和 1,490 个 turn，218 个原始 page context | 逐轮数值答案、程序、证据行/句、用户问题历史 | 首先用于多轮问答结果与上下文证据检索；本轮已完成全 dev 的 BM25 对照 |
| [TAT-QA](https://github.com/NExTplusplus/TAT-QA) | 论文/README 口径为 16,552 问、2,757 个 hybrid context；2024 年已公开 test gold | dev 1,668 问/278 context；当前 test gold 实际为 **1,663 问/277 context** | 答案、类型、scale、derivation、相关段落、来源为 table/text/table-text | 首先用于表格与文本联合问答、数值和单位客观评分；不把整张表标签误当精确单元格标签 |
| [FinQA](https://github.com/czyssrs/FinQA) | 论文 8,281 问；有公开 train/dev/test，另有不带参考答案的 private test | 完整 dev 883 问、299 个 page context | 数值执行结果、程序、证据行/句 | 单轮数值推理的兼容基线；与 ConvFinQA 同源，不能当完全独立数据 |
| [FinanceBench](https://github.com/patronus-ai/financebench) | 论文整体 10,231 问，公开部分只有 **150 问**；HF 显示单个 `train` 分区，不等于官方训练/验证/测试划分 | 全部 150 问、32 家公司、84 份被问到的文档；另读 361 份文档 metadata | 自由文本答案、189 条证据，含文档名、零起点页码与整页文本；100 问有非空 justification | 完整报告的证据检索有价值；必须下载全部候选报告并核对页码/文本，不能把 gold 摘录池叫全报告检索 |
| [FinDER](https://huggingface.co/datasets/Linq-AI-Research/FinDER) | 官方卡 5,703 条 query–evidence–answer；HF 单个 `train` 分区 | 官方 first-rows 的前 50 条仅作 schema 样本 | 文本答案、证据字符串、问题类型、reasoning 标记；样本行没有结构化 doc/page ID | 后续更贴近实际含缩写、含歧义的金融检索；官方有 `10-k.zip`，仍须构建完整候选库与证据对齐 |

规模来源分别为 [FinQA 论文](https://aclanthology.org/2021.emnlp-main.300/)、[ConvFinQA 论文](https://aclanthology.org/2022.emnlp-main.421/)、[TAT-QA 论文](https://aclanthology.org/2021.acl-long.254/)、[FinanceBench 论文](https://arxiv.org/abs/2311.11944)及 [FinDER 官方卡](https://huggingface.co/datasets/Linq-AI-Research/FinDER)。上表“本次实际读取”的数目由下载文件计算，详见 [机器可读审计](OPEN_SOURCE_DATASETS_2026-11-06.validation.json)。TAT-QA 当前 test 文件比论文口径少 6 问/1 context；本实验使用固定文件的真实分母，不补造缺失样本。

## 2. 许可必须分别判断

| 数据集 | 代码/仓库许可 | 数据集明确条款与处理 |
|---|---|---|
| FinQA | 官方仓库 [MIT LICENSE](https://github.com/czyssrs/FinQA/blob/main/LICENSE) | 数据和代码在同一官方仓库发布；本次未发现单列的数据许可。记录仓库 MIT、保留版权和许可通知，并引用论文；不扩张成对来源报告的权利声明 |
| ConvFinQA | 官方仓库 [MIT LICENSE](https://github.com/czyssrs/ConvFinQA/blob/main/LICENSE) | 同上；保留来源、原 ID、版本与论文引用 |
| TAT-QA | 代码仓库 [MIT LICENSE](https://github.com/NExTplusplus/TAT-QA/blob/master/LICENSE) | README 明确数据集是 [CC BY 4.0](https://github.com/NExTplusplus/TAT-QA#license)，不能把数据写成 MIT |
| FinanceBench | 本次官方 GitHub tree 中未见 LICENSE 文件，不能自行假设其全部代码都是 MIT | 官方 [Hugging Face 卡](https://huggingface.co/datasets/PatronusAI/financebench) 明示 **CC BY-NC 4.0**；公开可下载不等于无条件商业复用 |
| FinDER | 本次核查的是数据发布，并未核定所有配套代码的许可 | 官方 [数据卡](https://huggingface.co/datasets/Linq-AI-Research/FinDER/raw/main/README.md) 明示 **CC BY-NC 4.0**，要求署名引用；论文的 CC BY 不能替代数据集 NC 条款 |

金融报告本身是另一层来源材料。公司报告可以公开访问，不因此自动成为政府作品或进入公有领域。课程研究中保留作者、数据版本、原报告来源；对外分发原文和商业用途应按相应材料条款另行处理。这里不将资料“可下载”解释为已取得所有原始报告的再授权。

## 3. 字段核查与防止答案泄漏

**ConvFinQA。** 实际 turn 文件的 `cur_dial`、`turn_ind`、`cur_program`、`exe_ans` 和 `gold_ind` 位于 `annotation` 内。每条 turn 记录还带有完整对话、未来问题、未来程序和答案等字段，不能把原 JSON 整条喂给模型。接口只向模型提供当前问句、此前用户问题、原始 table 和所有 pre/post 文本；过去答案应来自被测系统自己的运行，不从参考答案回填。所有 1,490 个 dev turn 的证据 locator 均能对应到原始来源块。

**FinQA。** `qa.exe_ans/program/gold_inds` 只进入 scorer；`qa.model_input`、`tfidftopn`、`table_retrieved`、`text_retrieved` 不进入模型输入。官方 README 记录过不同正负表格行序列化带来 label leakage 的问题；本接口对所有表格行采用同一套只依赖原始表格的序列化规则，不根据 gold 决定内容或格式。883 个 dev 问题均有可映射证据。

**TAT-QA。** 使用 `dataset_raw` 中的 `table`、`paragraphs` 和 `questions`。`answer/derivation/answer_type/answer_from/scale/rel_paragraphs` 留在 scorer。`answer_from` 与 `rel_paragraphs` 支持“整张表/相关段落”粒度的来源检查；不能声称它们是逐单元格、逐字符证据。官方说明 `dataset_tagop` 的 `facts/mapping` 是启发式生成，本轮没有把这些字段转成人工精确 gold。

**FinanceBench。** 实际证据字段是 `doc_name`，而 README 说明列的是 `evidence_doc_name`，适配时要以固定版本真实数据为准。189 条证据全部有非空摘录、整页文本、合法非负整数页码，且文档名可在 metadata 查到；共有 168 个不同 doc-page。这里仅验证了字段覆盖，尚未验证 PDF 提取文本与页码对应，不能把“189/189 metadata 可连上”叫成检索或解析成功。

**FinDER。** 50 条 schema 样本均有非空答案和 references，共 54 条证据字符串；不存在直接可用的 doc/page 字段。原 corpus 可下载，但本轮没有下载它，也没有把参考证据字符串合并成一个 gold-only 检索库。对齐覆盖率明确保留为未知。

上述原始字段结构、样本官方 ID、各类覆盖率和缺口均保存在 [validation JSON](OPEN_SOURCE_DATASETS_2026-11-06.validation.json)；不通过模型推测缺失标注。

## 4. 已可运行的适配与评分

入口为 `verifin/public_benchmarks.py`，也可通过 `scripts/public_benchmark_audit.py` 调用。只使用标准库与既有 BM25，不联网、不调用生成模型、不创建人工 gold。

```python
cases = load_public_dataset(
    "convfinqa", ".../dev_turn.json", split="dev",
    source_url=".../固定 commit/data.zip", revision="完整 40 位 commit",
)
model_input = cases[0].input_record()
scorer_reference = cases[0].reference_record()
```

每例保留 `dataset/split/original_id/group_id/dialogue_id/turn_index`，来源保留上游 URL、commit、原文件 SHA-256 和字节数。ConvFinQA 的来源 URL 指向 ZIP，记录的文件 SHA-256 是其中解出的 `dev_turn.json`，压缩包 hash 另见下载 manifest。不能把这两个 hash 混用。

`input_record()` 是唯一预定进入模型的对象，其中 `input_sha256` 绑定原始问题、历史问题和来源内容。`reference_record()` 另存，`source` 明示 `published_benchmark_reference`。导出时生成 `inputs.json` 和 `references.DO_NOT_SEND_TO_MODEL.json` 两份文件；目录必须新建或为空。

预测格式为列表中的每项 `case_id/input_sha256/answer/scale`。不认识的 ID、重复 ID 或 input hash 不一致会拒绝评分；未给出的预测按错误保留在分母中，不能静默删掉失败样本；没有公开参考答案时标为不可评分，不猜答案。

| 答案类别 | 本轮已实现的客观评分 | 明确边界 |
|---|---|---|
| FinQA/ConvFinQA 数值 | 与公开执行结果对比，四舍五入到小数点后 5 位；显式百分数/scale 做单位换算 | 没有执行预测程序，也没实现官方 symbolic program equivalence；不是排行榜完整指标 |
| FinQA/ConvFinQA yes/no | 规范大小写后的精确匹配 | 不评自由文本解释 |
| TAT-QA arithmetic/count | 显式 scale 换算后的 Decimal 精确匹配 | 不从回答 prose 中猜数字，不自动猜美元/百万/百分比单位 |
| TAT-QA span/multi-span | 忽略大小写与重复空白的 multiset 精确匹配，并核对 scale | 未实现完整官方 token F1、标点/数词等正规化；报告 `official_leaderboard_metric: false` |

正式与原论文比榜单时，应适配作者的 [FinQA evaluator](https://github.com/czyssrs/FinQA/tree/main/code/evaluate) 和 [TAT-QA evaluator](https://github.com/NExTplusplus/TAT-QA/blob/master/tatqa_eval.py)，记录其依赖、版本、异常规则与与本地评分的差异。当前窄口径评分用于我们的可审计实验，不把它包装成作者官方指标。

本次实际检查的 FinQA dev 883、ConvFinQA dev 1,490、TAT-QA dev 1,668 与 test 1,663 个公开答案均满足现有客观评分的输入要求。这是 **参考字段可评分覆盖率**，不是模型准确率；本轮没有把 gold 当预测再宣称 100% 成绩。

## 5. 真实公开 dev 上的无模型检索对照

运行完整 ConvFinQA dev，每题候选为原始给定 context 的 **全部** 表格行和文本句，引用标签来自 `annotation.gold_ind`。候选集合不按 gold 筛选。比较只使用当前问题，和按时间顺序以换行连接此前用户问题及当前问题；不增加编号、标签词或参考答案。

统一使用 v2 协议直接对 source block 排名，避免再分块生成不同 ID 导致同分排序变化。报告 mean evidence Recall@5、全部证据命中率@5、MRR@5，并另保留 MRR 的未截断值；最终上下文只有 top-5 来源单位，未施加字符预算。该口径衡量提供 context 后的证据选择，不是完整 PDF 检索，不是固定字符预算的 E3。

| 范围与输入 | 问题数 | mean Recall@5 | 全部证据命中率@5 | MRR@5 |
|---|---:|---:|---:|---:|
| 完整 dev：仅当前问题 | 1,490 | 0.579690 | 0.465772 | 0.509452 |
| 完整 dev：此前用户问题 + 当前问题 | 1,490 | 0.836290 | 0.712081 | 0.749452 |
| 预先固定子集：仅当前问题 | 100 | 0.551429 | 0.410000 | 0.474667 |
| 预先固定子集：此前用户问题 + 当前问题 | 100 | 0.840595 | 0.680000 | 0.767500 |

上述为最终 v2 结果；逐例结果在 `work/open-source-audit/convfinqa/bm25-{current,history}-full-dev-v2.json`，hash 和摘要在 validation JSON。早先同目录没有 `-v2` 的文件保留为 superseded，使用不同 chunk ID 同分排序，不能混在同一对照表。核心 BM25 也已按词排序后累加浮点分数，排除 Python set 遍历顺序对极近同分值的影响。冻结子集共 550 个不同 source block，最长 907 个字符。

另在看到结果前冻结了 100 个 turn、18 个原始 page group 的诊断子集，供相同问题、相同 source block 候选下比较 dense、hybrid 和 reranker。按 `SHA256(seed|group_id)` 排序，整组纳入，在不超过 100 turn 的条件下最多 20 组；真实选中 18 组。seed 为 `verifin-public-reranker-2026-11-06-v1`，manifest 保存原始 ID、未纳入组、来源 hash、创建时间和自身 hash。文件名中的 `20groups` 指组数上限，不代表实际有 20 组。所有相关对话和轮次一起保留。

本实验展示了不保留会话历史会丢失检索所需语义。它不能单独证明某种 Planner、Structured Critic 或全部 Agent 的有效性：仍缺少真实生成答案、相同条件的核验比较和相应研究问题的结果。小子集上的方法选择是开发诊断，不能再把同一子集称为未见测试集。

## 6. 对 Proposal 的 E1/E2/E3 能回答什么

| 研究问题 | 这些公开 QA 标注直接支持 | 仍不能据此推出 |
|---|---|---|
| E1：核验及修订 | 在同一个公开 QA 问题上，比较核验前后最终数值/指定答案正确率、证据命中；自然模型输出可以保留后做相同输入的条件比较 | 答案相同不代表解释中每条 claim 都正确，也不直接提供错误类型、遗漏要求、误报和 Critic 定位的完整标签；需要与 FinDVer 等公开 claim 标签分层处理 |
| E2：规划 | ConvFinQA 可客观衡量 follow-up 的最终答案、多轮累计错误；可以继续统计真实运行的工具次数、失败和成本 | 公开 gold 程序是数值推理程序，不是唯一合法工具路径；不能用“与 gold 程序不同”判 Planner 错误；现成数据也不覆盖来源故障、澄清、动态查询等所有 E2 情境 |
| E3：检索 | FinQA/ConvFinQA 行句标签可直接评分 source recall；FinanceBench 页证据与 FinDER references 可支持完整 corpus 的检索评价，前提是先做可追溯对齐 | 提供好的原 context 不等于完成全报告/多公司检索；gold-only corpus 不是合格负例池；TAT-QA 整表来源标签不能评价细粒度 table-cell recall |

合理的主实验设计是将 **公开参考数据的 objective QA/retrieval**、**公开 claim verification** 与 **真实交互运行的日志/案例分析** 分别报告。它们可在没有团队新增逐条标注的情况下提供实质结果。若没有评到原 Proposal 的某个更广指标，就缩小结论，而不是用 LLM judge、启发式标签或人工改坏文本冒充独立自然错误 gold。

## 7. Split、泄漏与数据使用边界

1. 保留官方 split，不能把官方 dev 调过的参数结果称为 held-out test。ConvFinQA test gold 当前未公开，本地完整评分用 dev；正式 test 依官方提交方式，不能填造参考答案。
2. ConvFinQA 从 FinQA 派生。本次 ConvFinQA dev 的 218 个 page context **全部**出现在 FinQA dev 的 299 个 page context 中；这只是实际测到的 dev-dev 重叠。若混合训练、示例或调参，必须继续核查所有跨数据集 split、原报告页和原题关系。
3. 同页的多题、同对话的多轮不是独立样本。抽样、训练/验证划分、误差分析和置信区间应以报告或至少原 context group 分组；本轮 100 个 turn 不能写成 100 份独立报告。
4. TAT-QA 当前 test gold 已公开、旧数据也可能在预训练中出现。冻结实验和推理输入能避免本项目直接泄漏，不能保证模型从未见过基准；报告应保留这个适用边界。
5. FinanceBench、FinDER 的 HF `train` 只是现有分发 partition。新建验证/测试集时，应先固定报告/公司组与采样规则，不能根据跑分删难题；先解决原报告 corpus 和 reference 对齐，再报告检索结果。
6. 公开标注也可能有错。保留原始 gold，不在看过模型答案后悄悄修正；有明确上游勘误时记录版本、影响案例和新旧指标。

## 8. 复现与下一步接线

在解压后的仓库根目录，`python` 指已安装本项目的 Python 环境；`AUDIT` 可以是任意新建工作目录。**不需要原作者电脑上的 work 临时文件**：随代码提供的 `scripts/fetch_public_benchmark_data.py` 固定官方 commit、字节数和 SHA-256，下载三个已实现适配的数据集并保留许可。默认只打印计划，显式 `--download` 才联网；`--verify-only` 可只读验证现有文件。三个数据集下载和选定解压合计 53,913,518 字节，不解压训练集。所有评估输出路径需要新建。

```bash
AUDIT=work/public-benchmarks
python scripts/fetch_public_benchmark_data.py --dataset all --output-dir "$AUDIT" --download
python scripts/fetch_public_benchmark_data.py --dataset all --output-dir "$AUDIT" --verify-only

python -m verifin.public_benchmarks \
  --dataset convfinqa --file "$AUDIT/convfinqa/dev_turn.json" --split dev \
  --source-url https://github.com/czyssrs/ConvFinQA/blob/cf3eed2d5984960bf06bb8145bcea5e80b0222a6/data.zip \
  --revision cf3eed2d5984960bf06bb8145bcea5e80b0222a6 \
  --retrieval-probe --history all_user_questions --top-k 5 \
  --output "$AUDIT/convfinqa/new-history-result.json"

python -m verifin.public_benchmarks \
  --dataset tatqa --file "$AUDIT/tatqa/dataset_raw/tatqa_dataset_dev.json" --split dev \
  --source-url https://github.com/NExTplusplus/TAT-QA/blob/870accc41953dcde885aabeb963d94aabdc0fbc3/dataset_raw/tatqa_dataset_dev.json \
  --revision 870accc41953dcde885aabeb963d94aabdc0fbc3 \
  --export-dir "$AUDIT/tatqa/new-model-input-export" \
  --output "$AUDIT/tatqa/new-coverage.json"

python -m unittest tests.test_public_benchmarks -v
```

数据获取脚本 `work/open-source-audit/fetch_sources.py` 只访问官方公开端点，记录 URL、下载时间、字节数、SHA-256，单文件与总量有限额；本数据审计未下载完整 FinanceBench PDFs、FinDER corpus 或模型权重。公开数据下载共 33,692,062 字节，加上选定 ZIP 成员的解压副本共 **61,055,508 字节**，低于 100 MB。`summarize_public_sources.py` 可离线重建本审计 JSON。并行工作的本地 embedding 向量结果、parser/model 文件另计，不混入数据获取量。

接口接入实际 Agent 时，建议新增独立 runner：按 `dialogue_id/turn_index` 顺序运行 ConvFinQA，给被测系统自身历史回复，逐轮记录候选、检索结果、最终结构化 `answer/scale`、events 与成本；评分只读独立 references。TAT-QA 先固定 dev 用于格式/单位调试，冻结规则后再运行当前公开 test gold 对应的输入。不要把这些外部数据的来源标签自动转换成现有人工 E1 annotation 文件里的已完成人工判断。

本轮已通过 10 项接口测试，覆盖未来轮/答案隔离、单位/百分数、来源映射缺失、输入 hash 绑定、失败分母、只读参考导出和 BM25 候选范围。真实 dev 检索结果另存逐例 ID，方便与后续本地 embedding/reranker 的同一固定子集对照。

## 9. 可直接转发的组件回放

`examples/open-source-study.html` 是无需联网的单文件回放，展示固定 100 题中的每一道问题、此前用户问题、BM25 / Qwen dense / hybrid 返回的真实 top-5 来源，以及相应公开参考位置的命中。参考证据只在结果展示和评分使用。下半部分读取实际 parser probe，比较 Apple R2 纯文本片段与保留 3/6 Months、2025/2024 多层表头的结构化表格。它明确标为组件回放，不提供虚构的 Agent 对话，也不伪装成现场模型调用。

通过指定最终结果文件可重新构建；不自动挑选最高分实验，原实验结果保持只读：

```bash
python scripts/build_open_source_demo.py \
  --results data/evaluation/open_source_results/convfinqa-qwen0.6-canonical-baselines.json \
  --data work/public-benchmarks/convfinqa/dev_turn.json \
  --parser-dir PATH_TO_PARSER_PROBE_RESULTS \
  --output examples/open-source-study.html --replace
```

已经生成的 HTML 无需原数据或 Python；重建时需通过上述下载脚本获取 ConvFinQA，以及 `compare_document_parsers.py` 产生的真实 parser probe 结果。`--data`、`--parser-dir` 都可显式指定，不依赖固定绝对路径。公开源的抓取/字段审计还包括 FinanceBench、FinDER；它们的固定版本 URL 与本次原始下载 hash 记录在 validation JSON，这两个数据集本轮没有 runnable scorer，未包含在上述三个适配器的一键数据下载中。

builder 校验来源 hash、固定选题集合、全部候选数量、返回的 source IDs 与 qrel 命中比例，并将结果文件 hash 写进页面。默认展示官方顺序中的第一条追问，所有 100 题均可选择。页面不加载外部资源；数据作为转义后的 inert JSON 嵌入，通过 `textContent` 显示，并使用只允许当前脚本 hash 的 CSP。仓库附 [NOTICE](OPEN_SOURCE_DATASETS_2026-11-06.NOTICE.txt)，包括完整 ConvFinQA MIT 通知、其他数据集的不同条款和原始报告版权说明；完整 NOTICE 同时嵌入 HTML，单独转发 HTML 也会保留。

开发阶段 DOM 验证已检查 100 题 × 3 方法共 300 次切换、600 个结果栏的命中标记、前后题按钮、Apple 多层表头与四个原始 Net sales 数值；未发现 JS console error。浏览器视觉验收由主 agent 在内置浏览器进行，不使用 Chrome。
