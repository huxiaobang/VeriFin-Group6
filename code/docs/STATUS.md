# 开发状态与交接

日期：2026-11-06；版本 **0.5.0**。新增公开数据与组件实测见 [采用与验收报告](OPEN_SOURCE_ADOPTION_2026-11-06.md)。原 Proposal、0.3/0.4发布归档保留。**主模型研究闭环、自然输出核验与正式 E1/E2 效果结论仍未完成。** 公开参考标签已经可以减少基础核验/数值/检索任务的新标注工作，不再把全部评估笼统视为必须从零人工制作。

## v0.5 新增

- FinQA、ConvFinQA、TAT-QA 适配与客观评分；固定版本下载/许可记录，模型输入与参考分开。
- FinDVer 真假的公开评分入口；修复官方题号与排序泄漏标签，固定报告bytes核验；24声明/9完整报告/2971块的真实Qwen检索实验。
- ConvFinQA1490轮完整开发集BM25诊断及100轮/18页的Qwen对照；历史query序列化、相同候选和指标口径明确。
- lxml+pandas可追溯结构提取和离线CLI；真实SEC表格保真验证。默认Agent摄取/检索的结构接入仍是下一步，旧offset不迁移错位。
- Qwen reranker与PPLX v2 late多向量均完成真实100轮实验；前者没有改善本轮指标，后者MaxSim Recall@5为0.8368，仍未超过同条件BM25。量化4B路线已研究但未运行，详见[模型报告](OPEN_SOURCE_MODELS_2026-11-06.md)。
- [公开回放demo](../examples/open-source-study.html)，无需模型或API；另修复初始化WAL锁竞争与BM25累加顺序。

完整回归 **444项通过**，另在固定解析依赖环境中10项结构测试通过；测试日志为 `validation-open-source-final-unittest.txt` 与 `validation-structured-html-unittest.txt`。最初全量回归暴露WAL启动竞争，修复后再验收；不隐去该失败。性能实验与质量边界以各实测报告为准。

## 保留的 v0.4 功能

| 范围 | 已实现与验证 | 实际边界 |
|---|---|---|
| OpenAI 接入 | Responses 与 compatible Chat 双路径；明确 provider、reasoning、Standard 档位；拒绝、截断、异常、usage 与 served model 保留 | 通过注入 HTTP 响应的协议测试，未配置 key、未实际调用 LLM；JSON-object mode，非 strict Schema |
| 本地 embedding | E5-small、BGE-M3、Qwen3-0.6B 三种真实权重；固定 revision、CPU float32、同语料同预算对比 | 仅 4 份文档、10 个基础问题的 30 个相关表达＋4 控制项；开发诊断，不是独立 gold |
| 新查询与缓存 | 交互 query 在线本地编码、passage 增量缓存、跨进程复用、语料/模型 hash、超长拒绝 | 真实 E5 工具集成通过：新问题只编码 query，重开后无需重算；权重不放进发布包 |
| Pilot | 默认 preflight；显式 execute 才运行；多案例、多轮、逐轮重开 SQLite、候选/报告/事件/审计输出 | 默认检查已实际运行并正确报告未配置模型；脚本对话测试不是自主表现 |
| 人工材料辅助 | 单文件离线 HTML，拆分断言、内容单元、证据关联、草稿导入导出、签署前检查 | 不预填判断、不生成 gold；原盲审与双人/裁决协议保留 |
| 费用与实验一致性 | 普通/缓存读取/两种缓存写入/输出 token 分项账本；失败与未知保留；E1 记录返回模型漂移 | 估算不是账单或硬费用上限；旧两单价默认仅参考，不能冒充费用下界 |
| 研究复核 | 11 项技术选择、替代方案、官方来源与剩余证据；E3 负例和延迟口径修正 | 复杂财报表格结构仍是明确薄弱点；不是换大模型就已解决 |
| 控制器修复 | 排除检索计时/缓存统计造成的假进展；缓存冷/热 artifact hash 一致；编码批次/锁等待支持取消和期限 | 单次模型加载或 PyTorch batch 仍是同步执行，不能立刻抢占 |

v0.4 的历史测试、打包与本机验证见 [validation.json](validation.json) 和 [测试日志](validation-unittest.txt)，这些旧hash不是v0.5的源文件清单。v0.5另有公开数据/模型/解析来源记录；没有调用付费主模型或行情接口。

## 本地实测支持什么决定

本轮更可信的共同对照是冻结的 ConvFinQA 100轮/18页：加入此前问题后，BM25 Recall@5为0.8406；E5/BGE/Qwen的hybrid分别为0.8149/0.8226/0.8137。Qwen dense的首条证据排序更好，但没有找全更多证据，不能只凭MRR锁定最终模型。FinDVer的24声明/9完整报告实验也显示，当前Qwen dense/hybrid未稳定胜过BM25。新重排/多向量的状态与结果见[模型报告](OPEN_SOURCE_MODELS_2026-11-06.md)。

早期30个相关表达只对应10个独立信息需求，其hybrid为E5/BGE/Qwen 0.767/0.767/0.800，中文组0.60/0.70/0.80；保留为历史错误诊断，不能继续将其当作充分的选型证据。

更多证据与完整限定见 [embedding 实测](EMBEDDING_STUDY_2026-11-06.md)、[整体技术复核](TECHNOLOGY_REVIEW_2026-11-06.md)、[主模型/API 选型](MODEL_SELECTION_2026-11-06.md)。

## 可以直接交给同学的入口

- `python3 -m verifin serve`：原有多轮研究界面、报告、来源和图表。
- `python3 -m scripts.demo_replay --output .verifin/demo`：真实历史来源＋明确 scripted fixture 的两轮演示；实际 LLM 调用为零。
- `python3 -m verifin.pilot --suite examples/pilot-development.json --output .verifin/preflight`：无付费调用的配置、数据与预算检查。
- 打开 `examples/annotation-demo.html`：明显标注为虚构材料的空白表单演示，不是人工标签。
- `python3 -m verifin.annotation_form --reviewer-dir ... --output reviewer.html`：把已有盲审材料变成可填写页面。
- [Runtime embedding 配置](RUNTIME_EMBEDDINGS.md)、[Pilot 与标注工具](PILOT_AND_REVIEW_TOOLS.md)、[费用账本](COSTS.md)。

原有 E1 检测/共同修订器/评分、E2 配对规划、E3 冻结检索、来源缓存、确定性财务计算、行情导入、多轮 scope 修正和不可变旧报告继续保留。本次未要求组员先手动整理 JSON 才能检查开发状态。

## 仍然缺少什么

| 项目 | 目前为何未完成 | 谁需要提供什么 |
|---|---|---|
| 真实 LLM pilot | 没有本机 API 凭据、确定的账号模型和支出范围 | 团队提供账户可用性与预算，密钥只在本机配置；随后运行、修错和整理可继续由 agent 完成 |
| SEC/行情线上验收 | 未配置实际 SEC 联系身份及供应商权限 | 团队明确合法可用的数据接入；已有适配和 replay 验证不能替代线上验收 |
| 正式语义效果结论 | 已有公开核验/QA标签，但没有本系统自然输出的完整效果判断 | 可自动跑公开参考任务；自然输出的遗漏/修订另评，不能将公开二分类当作全部E1 |
| 进一步结构和检索改进 | 已有结构模块与真实对照；默认工具接入及摄取时预建索引仍待做 | 无需API即可继续；编码2971块约15分钟说明不能让初次查询承担全部索引工作 |
| 公开QA表格表示 | 已发现将首行一律视为header不适用于MA/FRT的余额表；原始table仍保留，本轮使用冻结表示 | 无需人工/API即可修正；须新版本重编向量，不能把新表示混入历史实验 |

这些不等于只差一个 API key，也不需要团队现在立即完成整批标注。可以先完成真实自动 pilot 和开发诊断，将真正需要人的判断压缩为可审阅的证据材料；如果最终仍不做人类评价，报告就必须收窄相应结论。

## 保留的工程选择

采用 Proposal 允许的 Python 状态机 fallback、SQLite、标准库本地 Web/SVG。当前语料使用精确向量排序，无须先迁移 LangGraph、ANN 或云端数据库。系统为本地单用户开发版；新闻/电话会、任意 PDF、多模型仲裁、后台持续任务及交易不在已完成范围。

本次主 agent 负责模型接入、runtime、费用整合与验收；三个 subagent 分别负责技术复核、embedding 实测、pilot/标注工具。使用内置浏览器验收本地页面；Chrome 路径未获工具授权且未创建新标签，已停止使用。
