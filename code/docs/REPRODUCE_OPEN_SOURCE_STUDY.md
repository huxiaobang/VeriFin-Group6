# 公开研究实验：跨机器复跑

以下在解压后的源码根目录执行。本轮固定实验依赖使用 **Python 3.12**（NumPy 2.5.4 要求 3.12+）；核心 Agent 仍支持 Python 3.11+。命令中的 `work` 是接收者自行创建的工作目录，不依赖开发机器路径。原始报告、模型和公开数据各自保留来源/许可，见同目录 NOTICE 文件。没有主模型 API 调用。

## 1. 直接看结果

打开 `examples/open-source-study.html`，无需安装、网络或模型。它回放 100 个真实问题的 BM25/Qwen/Hybrid 证据结果及 Apple 表格结构。更多模型的结果见 `OPEN_SOURCE_MODELS_2026-11-06.md`；回放没有把它们伪装成原有交互选项。

## 2. 从实际向量独立重算

安装只用于矩阵运算的 NumPy，然后获取固定 ConvFinQA dev 数据。下载器先展示计划，明确添加 `--download` 才下载；复用文件时仍核对字节 SHA256。

```bash
python -m venv .venv-study
. .venv-study/bin/activate
python -m pip install numpy==2.5.4
python scripts/fetch_public_benchmark_data.py --dataset convfinqa --output-dir work/public
python scripts/fetch_public_benchmark_data.py --dataset convfinqa --output-dir work/public --download

python scripts/benchmark_public_retrieval.py rescore \
  --data work/public/convfinqa/dev_turn.json \
  --selection data/evaluation/public_convfinqa_selection.json \
  --artifact data/evaluation/open_source_dense_public/convfinqa-qwen0.6-vectors.json \
  --output work/qwen-rescored.json
```

同一命令将 `--artifact` 改为 `convfinqa-e5-small-vectors.json` 或 `convfinqa-bge-m3-vectors.json`，并使用新的 `--output`，即可分别复算 E5/BGE。重算不加载 Torch/模型权重，不重新生成向量。源码包保留实际模型身份、精确输入 hash 和编码资源记录；重算进程的 RAM 不能冒充模型 RAM。

冻结选择文件在测量前固定，包含 100 轮、18 个来源页。脚本核对官方 dev 文件 SHA、选择文件 SHA 和每条原文 SHA。结果路径必须新建，避免覆盖历史测量。参考标签只用于评分，不进入查询或向量。

## 3. 复跑真实 HTML 结构提取

```bash
python -m pip install '.[html]'
python scripts/fetch_parser_fixtures.py --output-dir work/parser-fixtures
python scripts/fetch_parser_fixtures.py --output-dir work/parser-fixtures --download
python scripts/compare_document_parsers.py --help
verifin-inspect-html --help
```

两个 Apple 样本固定为官方 SEC 报告在 EdgarTools 固定 commit 的测试快照。下载命令核对大小/SHA并输出可移植的 `source-manifest.json`。完整比较命令与结果解释见 `OPEN_SOURCE_COMPONENTS_2026-11-06.md` 第 8 节。CLI 输出新目录，保留结构 JSON、文本和可追踪块；不会覆盖原报告或旧引用。

## 4. 重新运行模型

新模型推理需要本地权重和相应可选依赖。普通 dense 使用 `.[embeddings]`；PPLX 还需要官方原生 processor 的视觉依赖。Qwen reranker 的固定 revision、官方 SHA256、模型输入和批次限制由 `verifin/reranking.py` 约束。具体命令与所有固定版本见 `OPEN_SOURCE_MODELS_2026-11-06.md` 及 `OPEN_SOURCE_RERANKER_2026-11-06.md`。

所有质量数字均为公开开发样本上的组件测量。它们不评价实际来源发现、真实多轮 Agent 报告、主模型幻觉或 Structured Critic 的端到端效果。
