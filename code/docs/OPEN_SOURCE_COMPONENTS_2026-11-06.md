# VeriFin 开源组件采用与实测记录（2026-11-06）

结论：应当复用成熟组件，但复用的位置要具体。本轮已经用 **lxml + pandas** 实现了可运行的 HTML 结构提取模块，并在真实 SEC 财报和复杂表格上验证；另有 **BM25S** 的可运行替换原型，在现有语料上复现当前 BM25 的排名。**EdgarTools** 也已真实离线运行，适合作为后续 SEC 结构识别的备选，需要针对来源定位、隐藏内容和数值转换做适配。无需为这些变化整体重写 Agent 编排。

本文的结果是源文件保真和实现对照，**不是独立标注的财务回答准确率，也不是 E1/E2 已成立的证明**。没有调用付费 API，没有下载 OCR/VLM 权重，没有改动已发布 v0.4 或已提交 Proposal。

随后补做了完整报告的真实模型检索实验：见 [FinDVer 实测补充](OPEN_SOURCE_COMPONENTS_2026-11-06_FINDVER.md)。预先冻结的 24 条公开 gold claim、9 份报告、2,971 个原始 blocks 上，BM25 Recall@5 为 0.541，高于 Qwen dense 的 0.408 和等权 hybrid 的 0.513。这项结果要求继续保留 BM25 强基线，不能把先前小语料排名直接当最终模型选型。另见 [标签隔离复核](OPEN_SOURCE_COMPONENTS_2026-11-06_REVIEW.md)：已发现并修复 FinDVer 原 ID 编码真假标签的问题。

## 1. 本轮已经交付的代码与结果

| 交付 | 状态 | 做了什么 |
|---|---|---|
| `verifin/structured_documents.py` | 已实现，供主 Agent 整合 | lxml 建 DOM；pandas 展开行列合并；保留源字符串、表头层次、原始单元格 XPath、快照 SHA；错误明确保留 |
| `tests/test_structured_documents.py` | 10 项通过 | 检查重复/空表头、rowspan/colspan、负数和百分比字符串、隐藏内容、原始定位、长表保护、无网络请求 |
| `scripts/compare_document_parsers.py` | 已在真实来源运行 | 同一份 HTML 比较原 `_FilingText`、新模块与 EdgarTools；保存完整输出和可核对差异 |
| `work/open-source-audit/parser_probe_bm25s.py` | 可运行替换原型，尚未改核心检索 | 复用 BM25S 打分，保持原 tokenizer、过滤范围、公式、query 去重、正分数筛选和 ID 排序 |
| `work/open-source-audit/parser_probe_results/` | 实测结果 | 两份真实 SEC HTML + 一个复杂 fixture，各 parser 的输出、table chunks、汇总 JSON |
| `docs/OPEN_SOURCE_COMPONENTS_2026-11-06.json` | 机器可读汇总 | 来源版本与哈希、组件决定、解析实测和 BM25S 对照 |

核心 API：

```python
from verifin.structured_documents import parse_structured_html, table_chunks

document = parse_structured_html(
    raw_html_bytes,
    source_url=verified_download_url,
    snapshot_path=raw_snapshot_path,
)
chunks = table_chunks(document, max_rows=12)
```

该模块接收已取得的字节，不自行联网。依赖仅在使用时导入；基础项目不安装解析依赖也可继续使用原功能。探针版本为 `lxml==6.1.3`、`pandas==2.2.3`，Python 3.12.14，macOS arm64；解析比较进程峰值 RSS 约 190 MiB，包含两套解析库，不是单个 parser 的精确内存测量。

## 2. 真实 SEC 样本证明了什么

样本来自 EdgarTools 上游测试目录的固定提交 `107d182146ccf5d22a4e71f82cde8b363b212cf1`，不是随时间变化的 main 下载。原字节下载自 GitHub 镜像并记录 SHA-256；随后独立打开 [SEC 的 Apple 2025 Q2 R2 原页面](https://www.sec.gov/Archives/edgar/data/320193/000032019325000057/R2.htm)，核对了表头及 Net sales 四个数值。不能把镜像下载写成直接 SEC 下载。

| 输入 | 大小 | 新模块实际结果 | 相比旧 `_FilingText` 的实质改善 |
|---|---:|---|---|
| Apple `R2.htm`，利润表附件 | 81,015 B | 1 个可见表，24 行数据，双层期间/日期表头；Net sales 四个字符串均保留 | 原实现将全部内容压成一行，无法机器定位某一行某一列；新结果区分 3 个月/6 个月、2025/2024 |
| Apple `aapl-20250329.htm`，10-Q 主文档 | 889,976 B | 37 个表，29 个解析，8 个空布局表明确标记 `empty_layout_table`；四个收入值在同一源行保留 | 源表格结构没有丢失，也没有把空布局表当成已提取的财务事实 |
| 自建复杂 fixture | 755 B | 两层表头、rowspan/colspan、actual/outlook、前导零、括号负数、百分比、N/A 与破折号均保留 | 旧实现还会保留 `hidden` 和 `display:none` 元素；新模块去除它们且保留可见 tail 文本 |

R2 对照行是 `Net sales → $ 95,359 / $ 90,753 / $ 219,659 / $ 210,328`，分别关联 `3 Months Ended / Mar. 29, 2025`、`3 Months Ended / Mar. 30, 2024`、`6 Months Ended / Mar. 29, 2025`、`6 Months Ended / Mar. 30, 2024`。旧输出也包含这些数字；改进在于保留数字和对应行列的关系，不能把“数字出现过”冒充表格理解。

所有比较期间屏蔽 socket connect，记录为 **0 次连接尝试、0 次 LLM 调用**。这是已看过样本的开发检查，样本规模不支持“适用于所有 SEC 财报”的结论。

## 3. 为什么 lxml + pandas 现在值得采用

[lxml](https://lxml.de/) 提供成熟 HTML DOM、XPath 和恢复解析；[pandas.read_html](https://pandas.pydata.org/docs/reference/api/pandas.read_html.html) 提供行列及 colspan/rowspan 展开。本轮没有重新手写 HTML parser 或表格网格算法。VeriFin 自有的部分是来源绑定、显式失败、字符串保护和 Agent 所需的输出契约。

薄适配层仍然必要，实测已经发现并处理了这些问题：

- **防止数值被重写。** 不把 `001,203`、`N/A`、`(52.3)`、`10.5%` 先变成数字；源证据保留字符串，计算仍交给现有受约束的 facts/calculate 链路。
- **防止表头被库改名。** pandas 自动处理重复列名时可能生成 `2025.1`。模块让 pandas 展开网格，再依据原始 `thead`/leading `th` 行恢复表头，避免伪造源标签。
- **定位的是原始来源。** 每个表和源单元格保存清理前 DOM XPath、source line、原字节 SHA；另存 lxml 序列化 DOM 片段哈希。片段哈希不是原始字节区间哈希，不声称拥有 HTML byte offset。
- **提取文本有独立坐标。** `text_snapshot_hash`、block 的 `text_start/end` 指向新提取文本的 Unicode codepoint；旧 `_FilingText` 的偏移不能直接沿用。
- **不从格式推断财务口径。** 主 10-Q 的很多期间表头使用 `td`。当前模块保留全部行列，却不假称已经识别了期间/单位。无声明表头的表保持整表，并标记超过行数目标；不能静默截断。
- **错误保留可复查。** 嵌套表、超限跨度、行映射改变等保留 `unresolved`、原因、来源和文本预览。空布局表也不会消失在成功计数中。

表头重复的 chunk 是派生证据，不一定是文档的一段连续文本；`text_is_exact_document_slice` 已作区分。CSS class、浏览器渲染可见性、扫描 PDF、跨页表格、复杂财务语义不在本模块的已验证能力内。

许可：[lxml 为 BSD 系列许可，底层 libxml2/libxslt 为 MIT](https://lxml.de/index.html#license)；[pandas 为 BSD-3-Clause](https://github.com/pandas-dev/pandas/blob/main/LICENSE)。保留版本及许可归属即可作为普通可选依赖管理，不需要 GPU。

## 4. EdgarTools：有价值，不能不经适配直接接入

本轮隔离安装并实际使用 `edgartools==5.62.1` 的 `edgar.documents.parse_html`，未使用其联网抓取方法。该版本已能从 Apple 主文档的 `td` 表格识别部分期间表头，这是它相比当前轻量模块的优势。它还有 SEC 专用语义树、section/XBRL 处理等可复用能力。[官方仓库](https://github.com/dgunning/edgartools)、[固定版本依赖声明](https://github.com/dgunning/edgartools/blob/v5.62.1/pyproject.toml)。

实测阻止了“安装后直接替换”的决定：

| 实测 | 对 VeriFin 的影响 | 正确接入方式 |
|---|---|---|
| R2 可见表只有 1 个，默认结果有 21 个，包含隐藏的 XBRL definition 弹窗表 | 把辅助定义混入财报检索 | 显式可见性处理，保留被过滤内容的来源记录 |
| `to_dataframe()` 把 `001,203` 变成 `1203.0`、`10.5%` 变成 `10.5`、`N/A` 和 `—` 变成 `0.0` | 缺失值和真实零可能混淆，比例单位消失 | 用 raw `Cell.text()` 作证据；数值语义转换单独验证 |
| 原始 `Cell.text()` 保留 Net sales 行 | 原始结构本身可用 | 保留 raw cells/rowspan/colspan，而非只取 convenience DataFrame |
| 未直接提供本项目需要的快照 SHA + 原 DOM XPath 引用契约 | 无法直接复用当前证据审计 | 用来源 sidecar 对齐，不把节点 ID 当原 HTML 坐标 |
| 导入时默认创建 `~/.edgar` 缓存 | 离线也有文件系统副作用 | 指定任务局部 `EDGAR_LOCAL_DATA_DIR` / `EDGAR_CACHE_DIR` |

M1 Pro 16GB 上本次两份 HTML 均可快速完成，不需要模型权重。主要顾虑是语义与依赖范围，而不是本次内存不足。上游处于活跃维护，PyPI 5.62.1 上传时间为 2026-10-23；仍应固定版本及回归样本，不能用维护活跃替代正确性检查。

顶层软件为 MIT，但本次解析的 PyPI 依赖包含 **Unidecode 1.4.0（GPLv2+）**，以及 PyArrow、HTTP/cache 等超出纯解析的依赖。上游 main 已出现 anyascii 替换，而固定 5.62.1 的依赖声明仍是 Unidecode；不能混用两个版本的许可结论。这里是依赖清单事实，不是在推断整个项目必须如何授权。若后续采用，记录实际安装版本并随分发保留相应许可；本轮主路径不依赖 EdgarTools。[Unidecode 项目说明](https://pypi.org/project/Unidecode/)、[EdgarTools MIT 许可](https://github.com/dgunning/edgartools/blob/v5.62.1/LICENSE.txt)。

## 5. BM25S：可实际替换手写评分，接口需要保持一致

已安装并运行 [BM25S 0.3.16](https://github.com/xhluca/bm25s) 的 NumPy backend；wheel 约 76 KB，本探针不需要 SciPy、Java 或模型权重。许可为 [MIT](https://github.com/xhluca/bm25s/blob/main/LICENSE)。

实际复用 4 份财报、158 个 chunk，14 个现有开发 query 加 3 个边界 query，共 17 例。**全部 top-5 排名、全部正分数候选集合与当前实现匹配**；最大分数绝对差约 `1.8e-15`。这说明替换可保持现有行为，不说明检索语义质量提高。

不能只写 `BM25()` 后就宣称等价：

```python
bm25s.BM25(
    k1=1.2, b=0.75,
    method="atire", idf_method="lucene",
    dtype="float64", backend="numpy", csc_backend="numpy",
)
```

本项目原公式包含 `(k1+1)` TF 系数；上述组合匹配该公式。使用原有 tokenizer，不新增默认 stemming/stopwords；query token 去重；只返回正分数，并以 chunk ID 作稳定并列排序。库的空 query/空 corpus 边界由 adapter 处理。

**过滤必须在统计前完成。** 当前 Corpus 是先按公司、报表日期、cutoff 等过滤，再计算 IDF。若改成全语料单一 index 后过滤，评分会被别家公司或未来文档影响，违反已有口径。原型为每个过滤后 chunk-ID 集合缓存一个 index；正式接入需要以 corpus fingerprint + normalized filters 作缓存键并限制缓存规模。

本机单次探针中，有候选 query 的耗时中位数约为旧实现 2.43 ms、已建索引后 BM25S 0.046 ms；首次建索引包含导入时最大约 115 ms。这只是小语料工程观察，不能宣传为线上吞吐提升倍数。采用价值是复用成熟的评分/稀疏索引与减少重复分词统计，当前小语料并没有必须上向量数据库的性能压力。

## 6. 暂缓的候选与 reranker 接口

| 候选 | 决定及理由 | 后续采用位置 |
|---|---|---|
| sec-parser | 不作为新主依赖：官方文档明确写着 “This repository is no longer maintained.”；PyPI 最新仍为 0.58.1（2024-06-09），依赖约束停留在 lxml 5 系列 | 只作为设计参考；没有为一个停止维护的库迁就当前依赖环境。[官方说明](https://sec-parser.readthedocs.io/en/latest/) |
| Docling / docling-core | 保留为 PDF、结构化文档模型与 token-aware chunker 的下一候选；本轮没有运行它，不能称已验证。单纯解析现有 HTML 不需要额外 OCR/VLM 栈 | `DoclingDocument` 可以统一 table/provenance；HybridChunker 可使用真实 tokenizer 并重复表头，但映射原始来源和过长单行仍需验收。[官方 chunking](https://docling-project.github.io/docling/concepts/chunking/)、[core 依赖](https://github.com/docling-project/docling-core/blob/main/pyproject.toml)、[MIT](https://github.com/docling-project/docling-core/blob/main/LICENSE) |
| Sentence Transformers CrossEncoder | 建议复用现成 query-document rerank 接口，不手写假 reranker；具体权重和效果由专门的模型/检索实验决定，本文不重复模型榜单 | 对过滤后的 BM25/dense 融合候选 top-N 重排，再应用最终 context budget；保留原 candidate IDs、score、model revision、截断记录与 latency。[官方 retrieve/rerank](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html) |

reranker 不能恢复第一阶段未召回的证据，也不能把文档级 report date 当成每句话的期间证明。应当固定候选集合比较 rerank 前后，记录 Recall@N、MRR/nDCG、claim 期间错误与延迟；没有独立标签的开发样本继续明确标记，不改名为 gold。模型代码许可和模型权重许可应分别记录。

## 7. 主工程的具体接入顺序

1. **当前可整合：** 在 `ToolRegistry.read_filing` 已验证原始快照后调用结构模块，输出可追溯 sidecar/显式 structured 模式。保留 parser version、依赖版本和新文本 hash，不混用旧引用偏移。失败记录需能出现在工具结果里。
2. **检索接入前必须处理：** Corpus 目前只接受普通文本并按字符切分。不能把 `table_chunks()` 的重复表头文本伪装成原文连续 span，也不能再次把无声明表头的长表强行字符切开。需要支持结构单元及来源映射，或暂时让工具返回整表、对过长输入明确拒绝/分页。
3. **BM25S 可小范围替换：** 保持现有外部工具接口，用已给原型替换评分函数并加入过滤后 index cache；原 ranking parity 检查保留。不要把替换库等同于语义质量提升。
4. **之后做 source fidelity 扩展：** 加入不同发行人、不同生成器的 10-K/10-Q、脚注、跨期同值/异值、隐藏标签和分页表。先扩大自动化保真案例，不要求组员现在完成大规模人工标注。
5. **保留项目自己的研究部分：** Planner/Critic 的职责、预算、证据状态、period/unit 检查、冻结评估协议属于 VeriFin 的贡献。成熟解析和检索组件应承接底层通用能力，不能替代研究问题或真实模型实验。

SEC 的 [官方 FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions) 说明政府创建内容及 EDGAR 公开申报内容可免费访问和复用；[开发者页](https://www.sec.gov/about/developer-resources) 仍要求声明客户端并遵守公平访问。软件许可证与数据来源分别记录。本轮使用小规模固定公开快照，不把开源代码的 MIT 许可证误写成所有数据与模型权重的许可证。

## 8. 复跑

以下命令在解压后的源码仓库根目录运行，不依赖作者机器上的 `work` 缓存。固定样本清单随源码放在 `data/evaluation/parser_fixture_manifest.json`；总下载量 970,991 B，下载地址锁定在指定上游提交，逐文件核对大小和 SHA-256。

```bash
python -m pip install -e '.[html]'
python scripts/fetch_parser_fixtures.py --output-dir artifacts/parser-fixtures
python scripts/fetch_parser_fixtures.py --output-dir artifacts/parser-fixtures --download
python -m unittest tests.test_structured_documents -v
python scripts/compare_document_parsers.py \
  --input artifacts/parser-fixtures/parser_probe_R2.htm \
  --input artifacts/parser-fixtures/parser_probe_aapl-20250329.htm \
  --source-manifest artifacts/parser-fixtures/source-manifest.json \
  --complex-fixture \
  --output-dir artifacts/parser-results
```

第一个 fetch 命令只显示计划，不联网、不创建目录；显式 `--download` 才下载缺失文件。已存在文件也必须通过 hash 核验，错误缓存会明确拒绝。生成的 `source-manifest.json` 只包含相对文件名及来源信息，不写入本机绝对路径；它可直接作为 compare 脚本的 `--source-manifest`。

上述流程无需安装 EdgarTools。若要复现第三方解析对照，可在隔离环境安装 `edgartools==5.62.1`，再给 compare 命令加 `--include-edgartools`；`--complex-fixture` 会自行生成复杂表样本。下载和解析是两个独立步骤，compare 脚本本身不联网。0.5.0 另提供 `verifin-inspect-html` 离线结构检查入口；旧 `read_filing` 默认提取路径及已发布 archive 保持原行为。
