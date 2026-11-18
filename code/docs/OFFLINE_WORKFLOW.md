# 不连接模型的操作指南

以下命令在源码根目录运行，`python3` 需为 Python 3.11+。运行核心不需要 API key；输出路径须新建或为空，已有人工材料不会覆盖。`--db`、`--data-dir` 是全局参数，放在子命令前。研究型 `ask/evaluate/planning run` 另需模型，不能由离线 preview 替代。

## 研究流程与来源

从公开案例提取首轮任务，然后执行固定取数：

```bash
python3 -c 'import json; p=json.load(open("docs/planning_case.example.json")); json.dump(p["turns"][0]["task"],open("task.json","w"),indent=2)'
python3 -m verifin planning preview --task task.json --output runs/source-preview
```

会保存来源副本、SQLite、每步选择/工具结果和 `preview.json`。示例获取 MSFT 季度收入和利润率，停止原因应为 `synthesis_required`，真实模型调用为 0，没有研究报告或语义核验。明确日期、相对最新期间、比较、利润率、增长、行情窗口和解释的格式见 [PLANNING.md](PLANNING.md)。

通用行情导入无需 SEC CIK。将合法来源整理成 [MARKET.md](MARKET.md) 的格式，用 `--market-data prices.json` 传入；对应任务使用 `kind: price` 和精确窗口。输入会按原始字节冻结保存。不会自动把周末改为相邻交易日。

## 检索与向量

```bash
python3 -m verifin retrieval corpus --output runs/corpus.json
python3 -m verifin retrieval search --corpus runs/corpus.json --query 'cloud revenue' --mode bm25
python3 -m verifin retrieval evaluate --corpus runs/corpus.json --cases data/evaluation/e3_development_queries.json --modes bm25 dense hybrid --output runs/retrieval.json
```

开发查询带真实来源 span，但不是独立人工 gold，结果只能作为实现检查。未提供向量时，dense/hybrid 明确 unavailable。不能用 BM25 成功冒充完成了全部 E3。

已有本地 SentenceTransformers safetensors 权重与必要运行库时，可离线编码精确的查询列表（`queries.json` 是字符串数组）：

```bash
python3 -m verifin retrieval build-embeddings --corpus runs/corpus.json --queries queries.json --model-path /absolute/local/model --model-id actual-encoder-id --revision actual-revision --output runs/vectors.json
python3 -m verifin retrieval evaluate --corpus runs/corpus.json --cases actual-cases.json --embeddings runs/vectors.json --output runs/e3.json
```

不会下载权重、调用生成模型 API 或允许自定义远程代码。模型 identity、revision、文件哈希和查询/文档前缀均保留；查询或语料变动后需重新编码。使用他人向量文件也必须满足语料/分块/模型标识匹配。排序耗时和编码耗时分别报告。

## E1 计划与人工评分

先准备空白材料：

```bash
python3 -m examples.study.prepare_materials --output runs/pending-study
python3 -m verifin study skeleton --count 12 --output runs/heldout-slots.json
```

这些文件只表明待选案例、待标注及待验收，不会生成成功结果。已有真实的复查前 case、detection 和 revisions 后：

```bash
python3 -m verifin study packet --case case.json --detection detection.json --revisions revisions.json --condition generic_review --trial-id trial-1 --output runs/packet.json
python3 -m verifin study annotations --packet runs/packet.json --annotators reviewer-a reviewer-b --output runs/annotation-bundle
python3 -m verifin study validate-annotation --packet runs/packet.json --annotation completed-a.json
python3 -m verifin study adjudicate --packet runs/packet.json --annotations completed-a.json completed-b.json --resolution resolved.json --output runs/adjudication.json
python3 -m verifin study score --packet runs/packet.json --adjudication runs/adjudication.json --output runs/score.json
python3 -m verifin study resources --detection detection.json --revisions revisions.json --trial-id trial-1 --output runs/resources.json
```

最后三个步骤必须使用实际人工完成的记录。`prices` 推荐使用带日期和来源的分项 catalog，按实际调用处理模型、档位和缓存读写；省略则货币费用未知。旧两费率文件默认仅提供算术参考，不能当费用下界，参见 [COSTS.md](COSTS.md)。失败调用仍计数；缺报 usage 时 token 只代表已报告部分，不能把未知费用当零。

`study entry/prepare/validate/freeze/aggregate` 管理案例分组、泄漏检查、冻结配置和汇总。完整 JSON 格式与字段解释见 [ANNOTATION.md](ANNOTATION.md)。同一事件、对话与扰动留在同一 split。空计划允许准备，但不能冻结为已完成研究。汇总不删除缺失、失败或无法判断的条目。

## 会话、范围、图表与验收

```bash
python3 -m scripts.demo_replay --output runs/scripted-demo
python3 -m verifin --db runs/scripted-demo/sessions.sqlite3 serve
python3 -m verifin --db runs/scripted-demo/sessions.sqlite3 sessions
python3 -m verifin --db runs/scripted-demo/sessions.sqlite3 scope SESSION_ID
python3 -m verifin --db runs/scripted-demo/sessions.sqlite3 charts SESSION_ID runs/charts --version 1
python3 -m verifin --db runs/scripted-demo/sessions.sqlite3 audit SESSION_ID --output runs/audit.json
```

使用 `sessions` 中的实际 ID。直接编辑范围可在页面完成，也可 `scope SESSION_ID --file scope.json --expected-version N`。保存时必须匹配当前版本。澄清期间修改范围同样使旧核验失效；旧报告保持原始范围和来源。

图表只展示已保存且新鲜的数值记录，按单位、指标、期间长度、会计/业务口径与复权版本分组；不会把冲突数值任意挑一个。图表不增加新的语义核验。

`audit` 检查历史报告自己的范围、规则与用量，保留 `semantic_acceptance: not_assessed`。6 个真实应用验收场景和待填表见 [APPLICATION_ACCEPTANCE.md](APPLICATION_ACCEPTANCE.md)。完整模型会话、未见发行人的实际来源与人工语义评价仍是后续工作。

## E2 人工质量与调用评价

配对模型运行在获得模型配置后进行：

```bash
python3 -m verifin planning run --case docs/planning_case.example.json --output runs/e2
```

已保存 `runs/e2/result.json` 后，以下命令只读取文件、生成材料和计分，不再调用模型：

```bash
python3 -m verifin planning packet --case runs/e2/case.json --run runs/e2/result.json --condition adaptive --turn 1 --output runs/e2-packet.json
python3 -m verifin planning annotations --packet runs/e2-packet.json --annotators reviewer-a reviewer-b --output runs/e2-annotations
python3 -m verifin planning validate-annotation --packet runs/e2-packet.json --annotation completed-e2-a.json
python3 -m verifin planning adjudicate --packet runs/e2-packet.json --annotations completed-e2-a.json completed-e2-b.json --resolution resolved-e2.json --output runs/e2-adjudication.json
python3 -m verifin planning score --packet runs/e2-packet.json --adjudication runs/e2-adjudication.json --output runs/e2-score.json
python3 -m verifin planning summarize --run runs/e2/result.json --scores runs/e2-score.json --output runs/e2-summary.json
```

对 fixed 及其他轮次分别生成材料；汇总缺少的条件/轮次会保留为 missing。两个人的填写和裁决仍需真实完成。判断研究前后内容质量与不当调用时，允许不同但有效的路径；失败、重复和耗时本身不直接表示错误。指南见 [PLANNING_ANNOTATION.md](PLANNING_ANNOTATION.md)。
