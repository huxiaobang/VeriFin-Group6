# Qwen3-Reranker-0.6B：真实公开样本实验

**本轮不采用为默认步骤。** 在相同历史问题和同一 top20 候选上，重排没有带来证据覆盖提升，却增加了约4.62秒的每题中位推理时间。这是当前输入表示、官方默认指令和CPU运行方式的结果，不能推导出整个reranker模型家族没有价值。

## 固定协议

- ConvFinQA dev 中预先冻结的100轮、18个来源页，共550个不同原始来源块。每题候选为所给报告片段的全部原始行/句子，没有先按gold筛选。
- 查询为此前用户问题与当前问题的纯换行拼接；不含参考答案、未来问题、参考证据或人工判断。
- 先用同一Qwen embedding + BM25 RRF（k=60）获得前20，再对这20条用官方Qwen相关性判断重排，取前5。100轮实际共1,968个query/passage对。
- 官方模型固定为 `Qwen/Qwen3-Reranker-0.6B` revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`；权重1,191,588,280字节，SHA256为 `27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b`。实际下载并核对官方LFS hash。
- 官方system指令、`<Instruct>`/`<Query>`/`<Document>`格式、left padding、think后缀和最终位置yes/no评分。采用官方默认检索指令；没有针对已看标签调参。
- CPU FP32、4线程、batch4、本地safetensors、禁远程Python代码；不生成文本，不调用模型API，不静默截断。

主对照是 **same-history hybrid → same-history reranked**，不能把仅当前问题到重排的变化全部归因reranker。[Qwen官方固定模型卡与Apache-2.0许可](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/blob/e61197ed45024b0ed8a2d74b80b4d909f1255473/README.md)

## 实际结果

| 方法 | 证据Recall@5 | 全部证据命中率@5 | MRR@5 |
|---|---:|---:|---:|
| 带历史BM25 | 84.06% | 68% | 0.7675 |
| 带历史Qwen hybrid | 81.37% | 62% | 0.8200 |
| 同一hybrid候选经Qwen重排 | 80.82% | 60% | 0.7550 |

候选前20的Recall和全部证据覆盖均为100%，因此这里的损失发生在最终前5排序，而非候选中完全没有标注证据。该子集有1题需要7块，固定前5的平均Recall上限为99.714%、全部证据命中率上限99%。20条候选平均仅约2,875字符；这些provided-context片段较小，不能据此证明实际应用必须增加检索/重排层。正式生成实验也应保留“预算允许时直接给完整片段”的基线。

按18个来源页整组重采样4,000次，报告turn-weighted paired mean。重排减hybrid的Recall差值为 **−0.55个百分点**，95%开发样本区间 **[−8.23, +5.38]个百分点**；MRR差−0.065，区间[−0.1828,+0.0235]。全部证据命中率差−2个百分点，区间[−12.75,+7.09]。区间均跨0，不能声称提升，也不能声称已经证明普遍劣于hybrid。100个相关轮次不是100份独立报告。

warm scorer总耗时476.08秒，每题median **4.62秒**、p95 **6.85秒**；实际重排进程峰值RSS **2.65GiB**。时间包含tokenization与forward，排除权重校验/加载、warm-up、候选检索和最终排序，因此不是端到端延迟。该数字也不是GPU/Metal性能。

## 错误揭示的改进方向

10题Recall提高、7题下降、83题不变。INTC设施面积问题补回了计算比例所需的total facilities分母行；IP债务追问补回total obligations行。MA诉讼负债的两个问题却丢掉2007期末余额行，提升了年度付款说明；FRT问题把2004期末余额换成期间折旧费用，涉及余额与流量的区别。

独立检查原始表又发现一个**共享表示层弱点**：当前公开QA adapter 的 `_blocks` 总把 `table[0]` 当表头，但MA/FRT的首行本身是期初余额数据，导致某些行出现 `$476915:404436` 这样的数字列名，并缺少首行日期标签。所有方法使用同一表示，对照仍可复算；但上述退步不能全部归因模型。原始table仍保存在输入中。下一轮应先采用不假定首行是header的保真表示，重新冻结版本后测量；不能在本轮运行中改文本、复用旧向量，再保留同一个实验名称。新的HTML结构模块与这一公开数据adapter是两个不同入口。

历史问题拼接只是诊断基线。Planner将追问解析成明确的当前检索需求，可能更适合重排；这仍是假设，需要另一个冻结实验验证。没有在本100题上不断改指令直到获得有利分数。

## 可复跑与独立核查

先按 `REPRODUCE_OPEN_SOURCE_STUDY.md` 获取固定数据和安装实验依赖，再通过官方Hugging Face下载固定revision到本机 `work/models/qwen3-reranker-0.6b`。不要只改model ID或把普通生成Qwen权重冒充reranker。

```bash
python scripts/benchmark_public_retrieval.py rerank \
  --data work/public/convfinqa/dev_turn.json \
  --selection data/evaluation/public_convfinqa_selection.json \
  --model-path work/models/qwen3-reranker-0.6b \
  --artifact data/evaluation/open_source_dense_public/convfinqa-qwen0.6-vectors.json \
  --output work/qwen-reranked.json --threads 4 --batch-size 4

python scripts/validate_public_reranking.py \
  --data work/public/convfinqa/dev_turn.json \
  --result data/evaluation/open_source_results/convfinqa-qwen0.6-reranked.json \
  --canonical data/evaluation/open_source_results/convfinqa-qwen0.6-canonical-baselines.json \
  --output work/reranker-validation.json
```

第二条命令不加载模型，可直接审计已发布分数。独立检查已通过：6条baseline逐题与canonical完全一致；1,968个分数有限且在[0,1]；原top20及ID平分顺序一致；前5、全部逐题指标、上下文字数和分组汇总均重算通过。结果和validation JSON均位于 `data/evaluation/open_source_results/`，包含各3个改善/退步实例。相关性分数不是真假概率，也没有被用作已校准的拒答阈值。
