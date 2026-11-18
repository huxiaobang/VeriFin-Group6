# 本地 embedding：从固定向量走向新查询

`--retrieval-vectors` 仍用于精确语料和查询已冻结的实验。新增 `--retrieval-encoder config.json` 用于交互研究：新问题可在本机编码，新增文档只编码新的片段，原文和模型没有变化的向量从磁盘复用。两者互斥。默认检索仍为 BM25，只有选择 dense/hybrid 的工具请求会加载本地模型。

## 配置与运行

核心仍只用标准库。建议在独立虚拟环境安装可选依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[embeddings]'
```

本轮实测环境另存 `requirements-embeddings-macos-arm64.lock.txt`，它记录这台 macOS arm64 的实际版本，不代表所有平台均已验证。

先下载固定 revision 的完整 Sentence Transformers 权重，使用本地 safetensors 目录，再修改 `examples/retrieval-encoder.example.json` 的 `model_path`。示例 E5-small 是资源基线，不代表最终选型。其他模型必须同时更换 model ID、revision 和官方要求的 query/passage 前缀，不能只换模型名。对比脚本 `scripts/benchmark_embedding_candidates.py` 提供三个候选的显式下载和开发测试入口，下载与运行分开。

```bash
.venv/bin/python -m verifin serve --retrieval-encoder my-encoder.json
.venv/bin/python -m verifin --db .verifin/sessions.sqlite3 \
  ask SESSION_ID '使用 hybrid 检索解释微软云业务毛利率变化，并引用原文。' \
  --retrieval-encoder my-encoder.json
```

相对模型路径按 config 文件所在目录解析。本机 loader 固定 CPU float32，禁止远端自定义模型代码与隐式下载，使用 `token=False`、`local_files_only=True`。候选模型的真正质量和资源测量见 [embedding 选型报告](EMBEDDING_STUDY_2026-11-06.md)。

## 一致性与边界

- 模型文件、revision、前缀、实现版本与原文参与缓存身份；模型变化或原文变化时不会误用旧向量。下载器的 `.cache` 元数据不参与模型权重指纹。
- 每条查询导出匹配当时语料的冻结向量 artifact，保留 hash，便于复查。重复查询的冷/热结果保持同一 artifact hash。
- 输入先经实际 tokenizer 检查；超过模型长度会明确失败，不静默截断。600/80 字符分块是共同测试配置，不是最佳分块结论。
- 工具加载、文件指纹、缓存锁等待和编码批次之间检查取消/时间。正在执行的单次模型加载或单个 PyTorch batch 无法被同步接口立即中断，可能超过协作式预算；大语料可先离线预热，适当减小 batch。
- 过滤后的检索分数是相关性排名，不能用来证明证据支持结论。负例的空返回行为和正式语义拒答分开报告。
- 目前 paired E2 仍要求 BM25 或冻结向量，以避免在线 encoder/cache 状态成为未控制变量。交互 runtime 已可用，不等于正式 E2 的所有检索组合均已验收。
- 每查询 artifact 含当时语料向量，适合当前小语料审计。大规模会重复占磁盘；扩展前应评估分离不可变 passage 索引与 query artifact，而不是无限保留全量副本。

实际系统同时保留 XBRL 数值取数和确定性计算。Embedding 的作用是召回候选文本，它不会替代财务期间、单位、计算和原文支持检查。
