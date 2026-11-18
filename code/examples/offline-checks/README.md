# 离线工程检查

本目录记录 v0.3.0 实际运行的取数、计算和 BM25 开发查询。真实模型调用和公网请求均为 0。固定规划停在综合之前，没有研究答案；检索标注是开发示例，不能作为独立 gold 或有效性实验。缺真实向量的 dense/hybrid 保持 unavailable。

从项目根目录运行 `python3 -m verifin planning preview --task examples/offline-checks/task.json --output runs/preview`，以及 OFFLINE_WORKFLOW.md 中的 retrieval evaluate 命令可复现。数值来源与哈希见 result.json。
