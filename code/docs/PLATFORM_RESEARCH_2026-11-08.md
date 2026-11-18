# VeriFin 平台参考研究：用户证据、公开实测与采用建议

研究日期：2026-11-08。**这次应新增重点参考 Hudson Labs 和 Hebbia；保留 AlphaSense、Reportify、Fiscal.ai，但分别学习它们的证据核查、研究交互和数据组织。** 不再把熟悉程度、官网宣称或评论星级当作推荐依据。Perplexity 适合作通用搜索对照；不能代替金融研究平台对照。

本轮是有纳入规则的定向调查，未穷尽市场。两名 subagent 分别调查实际用户证据、候选扩展与评测争议；主 agent 进行浏览器操作、财报数值核对及整合。没有购买订阅、注册新账号或调用付费模型。本文的优先级是“对 VeriFin 的参考价值”，不是准确率榜单。

## 1. 按我们的任务筛选

核心任务是：围绕公司集合与明确期间，检索财报/电话会等证据，计算、比较、生成可核查结论，并在后续对话更改期间或发现问题时更新结果。重点考察：

- 原始证据能否支持具体数字和解释；能否回到表格、段落及正确文件版本。
- 财年、自然年、累计期间、观察日、披露日、币种和指标定义是否保持一致。
- 跨公司、跨报告是否覆盖完整，缺失材料是否明确说明。
- 计算值、管理层指引、第三方预测和系统估算是否分开。
- 多轮修改能否改变研究范围、使旧结论失效，并保留可审阅历史。

选股、交易执行、价格预测、投行整套建模不是本项目的主比较任务。相关平台只作邻近参考。

## 2. 推荐对象与证据强弱

| 对象 | 具体值得参考什么 | 本轮实际依据 | 决定与局限 |
|---|---|---|---|
| **Hudson Labs** | 跨期财务/KPI 提取、逐数回源、日期口径 | 独立作者展示 Vail 五年财报、20 季 KPI 的操作和核查，也报告提示歧义和引用故障 | 新增重点；比泛泛“金融 AI”更贴近任务，但不是已证实准确率冠军 |
| **AlphaSense** | 可见的资料范围、跨文档研究、原文审阅与继续追问 | 分析师具名评价、近期使用评价、HBS 图书馆操作指南；有旧版跨期综合失败反馈 | 重点研究流程参考；旧评价用来设计测试，不当最新版本缺陷 |
| **Hebbia Matrix** | 公司/文件 × 指标/问题的矩阵，每格独立审阅和纠错 | G2 有金融尽调用户对跨文件提取的具体反馈，也有组织资料和偶发错误的问题 | 新增重点界面参考；评价没有可复现的准确率对照 |
| **Reportify** | 中文公司研究入口、任务模板、技能和资料工作区 | 实际查看界面并尝试提问；公开独立深测证据较弱 | 保留，产品定位贴近；目前不能把其回答当 teacher gold |
| **Fiscal.ai** | 公司/文件类型筛选、财务指标和原文片段、资料层 | 本轮实际运行 Apple 文档搜索，返回 35 份文档并核对四个历史财报值 | 重点数据与检索交互参考；未测生成、多轮、付费原文定位 |
| **Quartr** | 财报季的一手电话会、IR 材料与原文检查 | 买方机构 Navera 的年度报告披露实际使用；少量具体用户评价 | 重点资料层参考，不能把资料好用推成 Agent 推理更强 |
| **Qovest / 万睿研见** | 持续研究空间、同业比较和任务产物 | 2026-11-04 第三方实际操作图文 | 新增观察对象；没有逐项正确性核验，本轮没有登录试用 |
| **Samaya** | 真实研究任务、逐项 rubric、系统/数据比较方式 | 公开评测和示例；同时有竞品提出具体 rubric 争议 | 学习测试设计，先复核任务，不能直接采用厂商总榜或答案 |
| **Rogo / Finster** | 金融机构上下文与工作成果衔接 | 机构使用报道、合作方发布；未找到足够强的同题实测 | 次级参考，当前不足以优先花时间或钱 |
| **Perplexity** | 通用公开网络资料搜索的对照 | 本轮匿名默认 Search 提问进入登录限制 | 不把这次访问结果算 Finance 或付费 Deep Research 测试 |

关键来源：[Hudson 独立实测](https://www.buysideaireviews.com/p/hudson-labs-review-1)、[AlphaSense 分析师体验](https://www.trustradius.com/reviews/alphasense-2025-06-03-15-01-55)、[HBS 使用指南](https://www.library.hbs.edu/services/help-center/libary-ai-research-tools)、[Hebbia 用户页](https://www.g2.com/products/hebbia-ai-2026-02-24/reviews?page=8)、[Navera 机构披露](https://www.naverainvestment.com/wp-content/uploads/2026/04/Stewardship-Code-Report-2025-nav.pdf)、[Qovest 实操](https://www.woshipm.com/ai/6488795.html)。

另需纠正旧候选：微软官方收购历史列出 **2026-04-18 收购 Fintool**；不能继续把旧网址和旧评论当作独立服务现状。[微软记录](https://www.microsoft.com/en-us/Investor/Acquisition-History)

## 3. 实际操作结果：哪些测到了，哪些没测到

全部在应用内浏览器进行，没有打开 Chrome。固定首题要求只用截至 2025-05-02 的 Apple 官方资料，分别给出截至 2025-03-29 的三个月、六个月净销售额与上年同期，单位为百万美元，计算同比并引用原表。输入没有给参考答案。

| 平台/入口 | 实际动作 | 观察结果 | 能支持的结论 |
|---|---|---|---|
| Reportify Lite | 输入固定题并提交 | 显示“登录后立即为你回答” | 本会话不能匿名完成生成测试；不是答案错误 |
| Perplexity 默认 Search | 关闭登录提示后提交同题 | 先出现处理中，随后要求登录继续 | 没获得可评分答案；不是 Finance/Deep Research 质量测试 |
| Fiscal Terminal v6.0.7 | Tour → Query → Apple → `net sales` → Search | 显示 35 文档；若干 Q2 2025 片段包含四个目标数值及期间标签；多数详情入口是升级页面 | 公开关键词检索可以实际使用；不是同题自然语言回答或多轮测试 |

Fiscal 搜索片段中的四个值与 [Apple 原始财务报表第一页](https://www.apple.com/newsroom/pdfs/fy2025-q2/FY25_Q2_Consolidated_Financial_Statements.pdf)一致：

| 期间 | 2025 | 2024 | 同比（本研究独立计算） |
|---|---:|---:|---:|
| 三个月 | 95,359 | 90,753 | 5.0753% |
| 六个月 | 219,659 | 210,328 | 4.4364% |

这是 **一个公司、一组历史数字的检索片段核对**。没有验证所有 35 份文档、所有引用、检索召回率或平台计算能力。检索页面不要求自然语言计算，所以不能因未计算而扣分。我们没有完整平台回答，不能编造评分、延迟对比或胜负。

留存：[公开访问操作记录](../../../work/platform-research-2026-11-08/public-access-tests.json)、[固定问题及独立参考值](../../../work/platform-research-2026-11-08/apple-period-test-pack.json)。后者的后续四题仅准备，未运行；没有将准备完成写成测试通过。

### 公开成品的原文抽查

另对 Hudson [公开 AI 收益样例](https://www.hudson-labs.com/research/ai-adoption-impact-2026)按表格顺序固定取 IBM、ServiceNow、TD 前三行核对，没有替换来源难找的样本。这是已有成品审查，不是现场生成，也只核查公司是否作出相应披露，不验证 AI 投资的真实因果回报。

| 样本 | 原文核对结果 | 对我们有用的要求 |
|---|---|---|
| IBM | 官方 Q1 prepared remarks 支持累计 45 亿及 2026 年预计新增 10 亿节约；整体转型包含流程、自动化、数据和 AI。附带收入子句未完成公司托管原文核验 | 不把整体转型收益改写为 AI 单独净收益；不同子句分别标证据状态 |
| ServiceNow | 官方 CFO 文章将 3 亿描述为 2026 年预计年化节约；样例整行 Actual 难以表达混合状态。会议转录另有不同金额/再投资口径，未完成全部公司原文核验 | 拆开实际、预计、年化与利润影响，不能只用一个标签统领整段 |
| TD | 发行人 Q2 转录转载支持全年 2 亿目标与半年接近 1.45 亿；Q1 原文支持 10 亿目标构成。仍需补 CAD 和财年半年，部分目标期限未完整核验 | 明确币种、报告期和目标期；来源等级及未验证子句不得隐藏 |

原始材料：[IBM 发言稿](https://www.ibm.com/downloads/documents/us-en/15db805fff4249f1)、[ServiceNow CFO 文章](https://www.servicenow.com/workflow/news/servicenow-is-fortune-500-company-fourth-year.html)、[TD 发行人转录转载](https://ca.marketscreener.com/news/toronto-dominion-bank-quarterly-earnings-document-ce7f5ddbd089f722)、[TD 报告口径](https://td.mediaroom.com/2026-05-28-TD-Bank-Group-Reports-Second-Quarter-2026-Results)。完整子句、日期和限制保存在[用户证据报告](../../../work/platform-research-2026-11-08/user-evidence.md)末尾。这三行不能形成产品准确率，但确实说明“有引用、有正确数字”还不足以保证整个结论成立。

## 4. 第三方证据真正改变了什么

**Hudson 的具体体验比营销口号有价值。** 独立作者需要明确“按披露原样提取”，并区分财季结束日与业绩发布时的 KPI 观察日，才得到较好的结果。文中正文和图注的核查格数不一致，因此本报告不引用“100%准确率”。厂商主页转载这篇测评，也不构成第二份独立证据。[原测评](https://www.buysideaireviews.com/p/hudson-labs-review-1)

**“零幻觉”不等于完整可靠。** Hudson 官方保证的范围排除了遗漏、推理、计算、用户手动选择的历史上下文，以及超出一定跨度/指标数的请求。这说明即便数值能够回源，任务仍可能因漏项、错期间、错推导而失败；VeriFin 的 Critic 和评测需要分别覆盖这些维度。[保证范围](https://www.hudson-labs.com/blog/no-hallucination-guarantee)

**跨期综合不能靠单篇摘要表现推断。** AlphaSense 的一位分析师在 2025 年具名激励评价中肯定单篇用途，却反映跨 12 个月综合效果有限。该评价不能描述 2026 新版本，但给出了应测试的失败模式：是否真的读完所需期间，而非将少数搜索片段拼起来。[使用评价](https://www.trustradius.com/reviews/alphasense-2025-06-03-15-01-55)

**评价者自己也可能放松财务口径。** FinChat 的一篇有广告披露的实测核对了 Tesla 营收，却把含糊现金流名词被替换成另一指标视作优点。我们的要求应是说明采用的定义，必要时澄清，不能默默换指标。[实测原文](https://investingintheweb.com/blog/finchat-review/)

**金融专用资料的价值与模型能力分开。** Quartr 的机构使用披露说明它进入了资料收集流程，也说明研究人员会回原文检查。Fiscal 的实际检索则展示了资料与元数据组织的作用。这些证据支持先把摄取和来源做好，不能支持“换成该平台模型就会更可靠”。[Navera 报告](https://www.naverainvestment.com/wp-content/uploads/2026/04/Stewardship-Code-Report-2025-nav.pdf)

## 5. 哪些“评测”不应相信

- Reportify 搜索结果中，TrueSight 的文章自标 **AI GENERATED INTELLIGENCE**，却用“我们测试过”叙述，没有给可复查输入输出；不采用其准确性或性能结论。[示例](https://tsight.io/articles/7393865)
- 有导航页显示“0 条评测”却给星级/综合分；这不构成用户满意度或准确率证据。[示例](https://adk.wang/tools/reportify)
- 旧价格、旧免费问数、旧截图不当今天规格；FinChat 改名/转型也不等于产品停运。
- G2/TrustRadius 标注验证、激励和日期后才能使用；几条 5 星评价不能与另一平台的大样本横向排名。
- 对手写的比较也有利益关系。Samaya 公开 benchmark 与 Primer 的批评都提供具体线索，但本轮没有重审整套题，不能直接采信任何一方的总分或“有问题比例”。[公开评测](https://samaya.ai/benchmarks/frontier-finance) · [竞品批评](https://www.primerapp.com/blog/finance-ai-benchmarks-are-misleading/)

更完整的来源身份、日期、利益关系与排除记录见两份分工材料：[用户证据](../../../work/platform-research-2026-11-08/user-evidence.md)、[候选扩展](../../../work/platform-research-2026-11-08/discovery.md)。

## 6. 对项目的具体采用决定

| 优先级 | 采用的做法 | 与现有工作的关系 |
|---|---|---|
| P0 | 保留表格行列、币种/单位、累计期间、观察日与披露日 | 继续先修已发现的表格表示问题；不是再换一个 embedding 就能解决 |
| P0 | 研究结果显示“公司 × 期间 × 指标”的证据表，每格可回源 | 从 Hebbia/Hudson 学审阅方式；让缺失、冲突和推导可见，避免只交长文 |
| P0 | 展示研究范围、已覆盖文件、缺失期间和停止原因 | 从 AlphaSense/Quartr 学资料组织；全文来源发现与 provided-context 测试分开 |
| P1 | 多轮修改后只重做受影响计算，明确哪些旧结论失效 | 现有 SessionStore 已有版本与失效机制；应接入可见研究流程并做真实模型验收，而非重造记忆层 |
| P1 | 原始事实、计算、解释、假设/预测分别存储和显示 | Critic 对期间错配、遗漏、缺支持和计算分别反馈，Planner 决定下一步工具动作 |
| P1 | 首次答案与提示纠正后的答案分别评分 | 不只展示最终漂亮答案；记录纠错轮数、引用核验、工具成本和耗时 |
| P2 | 从 FinSight 读取中间产物与报告组织实现 | 有开源代码，但不整套照搬长报告、多次搜索流程；结合预算做必要消融 |

[FinSight 仓库](https://github.com/RUC-NLPIR/FinSight)可作为实现参考；[FinNextAssist 论文](https://arxiv.org/abs/2610.03174)的表格和证据处理设计也相关，但本轮未核实可运行的公开代码。两者均未在本轮运行，作者成绩不当作我们的成绩。已提交 Proposal 与发行归档保持原样。

## 7. 下一次有账号时如何测，不再凭感觉选 teacher

优先在 Reportify、Hudson Labs 与一个综合研究平台上使用同一小题组。第一条轨道只给相同官方资料，比较理解、计算和多轮修正；第二条允许各自原生资料，比较整体产品体验。两条成绩分别报告。

题组至少包括：季度/累计期间区分、从累计值推导单季、后续缩小期间、纠正用户错误前提、指定材料未披露时不编造指引、跨公司同口径对比。每题保存首次提问、完整回答、实际模式、来源、后续修改、耗时和费用；第一次与修改后结果分开。没有证据支持的项目标未验证，不给臆测分数。

**当前可以决定参考哪些设计；还不能决定哪个平台的答案适合直接蒸馏。** Teacher 输出必须回到原始材料复核，不能用同一平台答案同时出题、作 gold、当裁判。仅有会员账号也不能推出批量训练使用权。
