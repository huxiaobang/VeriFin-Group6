# 本轮新增组件复核：标签隔离、模型评分与证据来源

复核范围：`claim_benchmarks.py`、`audit_findver.py`、`reranking.py`，以及 `findver/export-v2` 的实际导出。主结论是发现并修复了一个实质性的 FinDVer 标签泄漏；Qwen reranker 的最后位置 yes/no 评分与官方定义一致。

## 已修复：公开样本 ID 本身编码了真假标签

初版虽然从模型输入删去了 `entailment_label`、`explanation`、`relevant_context`，但仍导出了 `findver:testmini:ie-val-10` 等官方 ID，并沿用官方数组顺序。这不能称为盲化输入。对固定 revision 的完整两个 annotation 文件进行计数，得到：

| split / subset | False 的 ID 尾数 | True 的 ID 尾数 |
|---|---|---|
| testmini / ie | 0–124 | 125–249 |
| testmini / numeric | 0–124 | 125–249 |
| testmini / knowledge | 100–199 | 0–99 |
| test / ie | 0–299 | 300–599 |
| test / numeric | 300–599 | 0–299 |
| test / knowledge | 0–248 | 249–499 |

仅凭这些 ID 区间、不读取 statement 或财报，就能还原 testmini 的 700/700 及 test 的 1,700/1,700 个标签。因此“输出 JSON 没有名为 label 的字段”不是足够的隔离条件。这里是确定性的泄漏诊断，不是任何模型的验证成绩。来源是 [固定版本的 FinDVer 数据与说明](https://github.com/yilunzhao/FinDVer/tree/e8bb237def4ce555a606a45edba22666e31df248)。

主 Agent 已作修复：

- `model_case_id` 仅由不含 gold 的 task input 内容哈希派生，不再包含官方序号或 subset。
- 实际模型输入顶层仅保留 `case_id`、`input`、`input_sha256`。
- 官方 ID、subset、split、provenance、标签和解释只保留在 scorer/reference 文件。
- 导出按 opaque ID 排序，不再保留官方先假后真的数组次序。
- scorer 接受 opaque ID，并在结果中提供与官方 ID 的映射。

对 `export-v2/inputs.jsonl` 的独立复核结果：24 条输入均符合上述约束；递归字段没有参考标签、解释、subset/split/provenance；三个原 ID 前缀均不出现；opaque IDs 唯一、已排序；逐条重算与 `ClaimCase.input_record()` 完全相同；reference 的 opaque ID 集合一致。输入文件 SHA-256 为 `60e12122e1917a512c20c0ccfe8aaf9636087351ad903d1d4de24580203f72dd`。

原报告英文正文中自然出现 `explanation` 或 `split` 单词，不属于字段泄漏，不能用粗糙全文关键字计数代替结构审查。完整 testmini/test 也没有相同 report+statement 的重复记录，当前内容哈希不会合并这两套数据里的不同样本。

初版没有产生真实 LLM 判定成绩；既有 BM25 诊断的 ranker 只接收 statement 和 source blocks，因此这次输入文件泄漏不改变此前的 BM25 结果。后续生成模型实验只能使用 v2 导出，旧 `export` 不能作为安全模型输入。

## Qwen reranker：计算定义与引用保真通过复核

`LocalQwenReranker.score()` 采用左 padding，输入末尾固定到 assistant 答案位置，提取最后位置的 `no` 和 `yes` logits，再在两者之间作 softmax。它与 [Qwen 官方 Transformers 示例](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) 的 `log_softmax(...).exp()` 数学等价。`logits_to_keep=1` 只减少输出张量，不改变所读取的最后位置。代码验证 yes/no 各为一个独立 token，并拒绝过长输入的静默截断。

`select_reranked()` 保留候选的原文、document ID、证据 ID 和偏移，新增重排分数后才使用统一的最终字符预算。分数表示 query/passage 相关性，代码没有把它当成事实真假或校准过的拒答概率。这些接口检查通过，不等于该模型一定提高实际检索指标；效果需看真实固定候选比较。

## 尚需明确的来源验证边界

1. `audit_findver.py` 对 annotation 文件有固定预期 hash；report 文件在缓存存在时直接复用，manifest 记录的是当次观测 hash。当前本轮文件确实来自已记录的官方下载，但通用重跑接口不能仅凭文件名与 declared revision 证明缓存字节来自该 revision。建议在正式冻结流程加入 9 份 report 的预期 hash，或明确写成 observed provenance。
2. `LocalQwenReranker` 核对 Qwen3 架构并记录所有本地文件 hash，但一般 Qwen3 生成模型也符合相同架构。若通用 CLI 接受任意目录，却固定写入 reranker 的模型名和 revision，身份声明可能失真。正式入口应核对固定模型文件 manifest，或区分 declared identity 与 verified artifact identity。该问题不否定本轮已从指定官方模型取得的本地权重。
3. FinDVer 的 `context_id` 是上游预处理报告里的 block 定位，不是原 SEC HTML 的字节偏移或浏览器坐标。当前 reference 已注明 `published_context_block`；对外报告应继续保持此措辞，并区分“给定报告的 claim verification”与 VeriFin 从公司发现、数据摄取、检索到修订的端到端任务。

公开基准降低了这部分组件实验对新人工标注的依赖，但不能自动填补自然生成答案的遗漏、引用充分性、工具规划或修订质量标签。把这些任务边界写清楚，比把所有检查合称“自动评估已完成”更可复现。
