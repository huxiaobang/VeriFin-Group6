# VeriFin research report

**Status:** complete
**Stop reason:** completed
**Request:** 麦当劳最近一季表现怎么样？
**Model mode:** configured_model
**Scope version:** 4

Model verification is an assessment against the available evidence, not an independent guarantee.

## Scope

```json
{
  "cutoff": "2026-11-16",
  "entities": [
    "MCD"
  ],
  "requirements": [
    {
      "id": "R1",
      "text": "确认截至2026年11月16日可获得的麦当劳最新已披露季度及资料发布日期，明确证据覆盖限制。"
    },
    {
      "id": "R2",
      "text": "用最新季度原始披露概述收入、利润及每股收益的同比表现，区分GAAP与调整后口径。"
    },
    {
      "id": "R3",
      "text": "概述同店销售及主要地区表现、公司披露的经营驱动与主要风险或展望，以中文给出有证据支持的整体评价。"
    }
  ]
}
```

## Findings

### claim:latest_quarter — Supported

截至2026年11月16日，在本次可用资料中，麦当劳最新已披露季度为截至2026年9月30日的2026年第三季度；业绩公告于11月4日发布，相关10-Q于11月6日提交。

Evidence: metadata_292ee90e389c62c63a2d1c21, span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

公告明确为截至2026年9月30日的第三季度；目录列明公告11月4日、10-Q于11月6日获SEC接收，均早于截止日。最新季度的判断明确限于本次可用资料。
metadata_292ee90e389c62c63a2d1c21 span_de69cbdff59e9d164a29cd48

### claim:coverage_limit — Supported

证据范围仅为截至该日的有限本地四份文件目录，本次未联网核验，因此不能宣称已穷尽麦当劳截至11月16日的全部披露。

Evidence: metadata_292ee90e389c62c63a2d1c21
Calculations: 

目录明确为截止日下的有限本地回放目录，共四份文件；调用记录均为本地读取、网络请求为零，因此未将该目录当作完整在线披露核验。
metadata_292ee90e389c62c63a2d1c21

### claim:revenue — Supported

第三季度合并收入为71.57亿美元，同比增长1%；按固定汇率计算也增长1%。这些是公司业绩公告直接披露的数字。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

合并关键财务指标表第三季度收入为7,157百万美元，即71.57亿美元；披露同比及剔除汇率折算影响的增幅均为1%，未误用九个月数据。
span_de69cbdff59e9d164a29cd48

### claim:gaap_operating_income — Supported

第三季度GAAP营业利润为35.95亿美元，同比增长7%。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度合并营业利润为3,595百万美元，上年同期3,357百万美元，公告列示同比增长7%；金额单位换算正确。
span_de69cbdff59e9d164a29cd48

### claim:gaap_net_income — Supported

第三季度GAAP净利润为24.86亿美元，同比增长9%。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度净利润2,486百万美元、上年同期2,278百万美元，同比增幅列示9%；净利润调节表确认该金额为GAAP口径。
span_de69cbdff59e9d164a29cd48

### claim:gaap_eps — Supported

第三季度GAAP摊薄每股收益为3.51美元，同比增长10%；固定汇率下增长11%。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度GAAP摊薄每股收益为3.51美元，上年同期3.18美元；原表分别列示报告同比增长10%、固定汇率增长11%。
span_de69cbdff59e9d164a29cd48

### claim:adjusted_operating_income — Supported

剔除本季5,600万美元及上年同期3,900万美元的净税前费用后，公司披露的非GAAP营业利润为36.51亿美元，同比增长8%；这些费用主要与“Accelerating the Organization”重组有关，调整后数字不应与GAAP口径混用。

Evidence: chunk_bf02f4d88a13ecc63477b8ab, span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

营业利润调节表季度列为GAAP 3,595加费用56等于非GAAP 3,651百万美元，上年为3,357加39等于3,396百万美元，披露增长8%。公告说明两年费用主要与相关重组有关，草稿正确区分调整后与GAAP。
chunk_bf02f4d88a13ecc63477b8ab span_de69cbdff59e9d164a29cd48

### claim:adjusted_net_income — Supported

按公司对上述费用的税后调整口径，第三季度非GAAP净利润为25.29亿美元，同比增长10%。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度净利润调节表列示税后费用43百万美元，GAAP净利润2,486调整至非GAAP 2,529百万美元；上年调整后为2,308百万美元，披露同比增长10%。
span_de69cbdff59e9d164a29cd48

### claim:adjusted_eps — Supported

第三季度非GAAP摊薄每股收益为3.57美元，同比增长11%，上年同期为3.22美元。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度非GAAP摊薄每股收益表明确列示2026年3.57美元、2025年3.22美元及同比增长11%，不是以本季调整值对比上年GAAP值。
span_de69cbdff59e9d164a29cd48

### claim:global_comps — Supported

第三季度全球同店销售同比增长1.5%，上年同期披露的同比增幅为3.6%，显示本季同店增长较为温和。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

同店销售表Total Company季度列分别为2026年1.5%、2025年3.6%；草稿将后者表述为上年同期的同比增幅，比较口径正确，温和增长属于有数据支撑的定性评价。
span_de69cbdff59e9d164a29cd48

### claim:us_comps — Supported

美国第三季度同店销售同比下降0.2%。公司解释为同店客流下降，基本被客单价增长抵消；这意味着美国需求端仍有压力，不能把盈利增长等同于客流全面恢复。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

美国同店销售为负0.2%；公司紧接表格说明同店客流下降、正向客单增长基本抵消。需求仍有压力及不能认定客流全面恢复的判断与负客流事实一致，未将盈利增长解释为客流增长。
span_de69cbdff59e9d164a29cd48

### claim:iom_comps — Supported

国际直营市场第三季度同店销售同比增长1.4%；公司称多数市场实现正增长，德国和澳大利亚领跑，法国表现形成部分拖累。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

International Operated Markets季度同店销售增长1.4%；对应文字明确多数市场正增长，由德国和澳大利亚引领、法国部分抵消，草稿保留公司归因。
span_de69cbdff59e9d164a29cd48

### claim:idlm_comps — Supported

国际发展许可市场第三季度同店销售同比增长4.2%；公司称各地理区域均为正增长，日本表现领先，但中国同店销售下降形成部分抵消。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

International Developmental Licensed Markets季度同店销售增长4.2%；公司说明所有地理区域正增长、日本引领，中国负同店销售部分抵消。草稿未将区域层面正增长错误延伸为每个国家均增长。
span_de69cbdff59e9d164a29cd48

### claim:management_profit_drivers — Supported

对于剔除上述费用后的第三季度营业利润增长，管理层主要归因于销售带动的加盟餐厅利润增加，以及各分部其他营业收入提高。这是公司对经营驱动的解释，并非本次独立量化的利润变动归因。

Evidence: chunk_bf02f4d88a13ecc63477b8ab
Calculations: 

非GAAP营业利润调节表后的说明明确将季度及九个月增长主要归于销售带动的加盟利润增加和各分部其他营业收入提高；草稿限定为管理层解释，未声称已独立量化，也未将仅涉及九个月的管理费用抵消因素移至季度。
chunk_bf02f4d88a13ecc63477b8ab

### claim:management_outlook — Supported

管理层表示，在采取价值优惠措施后，美国业务随季度推进有所改善，并将继续强化当前业务、投资未来增长；这属于管理层的定性判断，不代表已证明美国客流恢复增长。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

CEO原话称价值措施后美国表现随季度推进改善，并强调强化当前业务及投资下一阶段增长；草稿保留管理层定性归属，不将改善升级为客流已恢复正增长。
span_de69cbdff59e9d164a29cd48

### claim:forward_risks — Supported

前瞻风险方面，10-Q提示通胀及宏观经济变化可能影响消费者可支配收入和支出，商品与人工成本波动可能影响餐厅盈利。这些属于风险披露，不能据此断定它们就是本季业绩变化的实际原因。

Evidence: chunk_a2157b43fcb6278657ae786d, chunk_ca4eaef28c619ddbed9e3319
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

宏观风险段明确通胀可能影响可支配收入与消费；成本风险段说明商品和人工波动可能影响餐厅盈利，后者具体涉及自营餐厅。草稿仅作前瞻风险概述，未将其认定为本季度实际变动原因。
chunk_a2157b43fcb6278657ae786d chunk_ca4eaef28c619ddbed9e3319

### claim:overall_assessment — Supported

整体评价：这是一个“利润表现强于销售、地区表现分化”的季度。收入增长1%，而GAAP营业利润和净利润分别增长7%和9%；但全球同店销售仅增长1.5%，美国同店销售及客流仍下降。因此，盈利表现稳健，但需求端尚不能称为全面强劲复苏。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度收入增长1%、GAAP营业利润增长7%、净利润增长9%，支持利润增速高于销售；全球同店增长1.5%、美国同店负0.2%且客流下降，结合国际分部正增长支持地区分化及不能认定全面需求复苏的分析。
span_de69cbdff59e9d164a29cd48

## Answers to your questions

**麦当劳最近一季表现怎么样？**

answered — 整体评价：这是一个“利润表现强于销售、地区表现分化”的季度。收入增长1%，而GAAP营业利润和净利润分别增长7%和9%；但全球同店销售仅增长1.5%，美国同店销售及客流仍下降。因此，盈利表现稳健，但需求端尚不能称为全面强劲复苏。 截至2026年11月16日，在本次可用资料中，麦当劳最新已披露季度为截至2026年9月30日的2026年第三季度；业绩公告于11月4日发布，相关10-Q于11月6日提交。 证据范围仅为截至该日的有限本地四份文件目录，本次未联网核验，因此不能宣称已穷尽麦当劳截至11月16日的全部披露。 第三季度合并收入为71.57亿美元，同比增长1%；按固定汇率计算也增长1%。这些是公司业绩公告直接披露的数字。 第三季度GAAP营业利润为35.95亿美元，同比增长7%。 第三季度GAAP净利润为24.86亿美元，同比增长9%。 第三季度GAAP摊薄每股收益为3.51美元，同比增长10%；固定汇率下增长11%。 剔除本季5,600万美元及上年同期3,900万美元的净税前费用后，公司披露的非GAAP营业利润为36.51亿美元，同比增长8%；这些费用主要与“Accelerating the Organization”重组有关，调整后数字不应与GAAP口径混用。 按公司对上述费用的税后调整口径，第三季度非GAAP净利润为25.29亿美元，同比增长10%。 第三季度非GAAP摊薄每股收益为3.57美元，同比增长11%，上年同期为3.22美元。 第三季度全球同店销售同比增长1.5%，上年同期披露的同比增幅为3.6%，显示本季同店增长较为温和。 美国第三季度同店销售同比下降0.2%。公司解释为同店客流下降，基本被客单价增长抵消；这意味着美国需求端仍有压力，不能把盈利增长等同于客流全面恢复。 国际直营市场第三季度同店销售同比增长1.4%；公司称多数市场实现正增长，德国和澳大利亚领跑，法国表现形成部分拖累。 国际发展许可市场第三季度同店销售同比增长4.2%；公司称各地理区域均为正增长，日本表现领先，但中国同店销售下降形成部分抵消。 对于剔除上述费用后的第三季度营业利润增长，管理层主要归因于销售带动的加盟餐厅利润增加，以及各分部其他营业收入提高。这是公司对经营驱动的解释，并非本次独立量化的利润变动归因。 管理层表示，在采取价值优惠措施后，美国业务随季度推进有所改善，并将继续强化当前业务、投资未来增长；这属于管理层的定性判断，不代表已证明美国客流恢复增长。 前瞻风险方面，10-Q提示通胀及宏观经济变化可能影响消费者可支配收入和支出，商品与人工成本波动可能影响餐厅盈利。这些属于风险披露，不能据此断定它们就是本季业绩变化的实际原因。 [claim:overall_assessment, claim:latest_quarter, claim:coverage_limit, claim:revenue, claim:gaap_operating_income, claim:gaap_net_income, claim:gaap_eps, claim:adjusted_operating_income, claim:adjusted_net_income, claim:adjusted_eps, claim:global_comps, claim:us_comps, claim:iom_comps, claim:idlm_comps, claim:management_profit_drivers, claim:management_outlook, claim:forward_risks]

## Unanswered items and issues

None identified.

## Evidence and source locations

- **metadata_292ee90e389c62c63a2d1c21**（本轮留存） (MCD;  to ): manifest.json
  Locator: documents[0], documents[3], documents[1], documents[2]; snapshot SHA-256: c98ef96e09ba9ea5452972813fe81d0bc5ab0d0a90cbc81905b9e9b7251dd5d2
- **span_de69cbdff59e9d164a29cd48**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000080/exhibit991-9302026.htm>)
  Locator: snapshots/exhibit991-9302026.htm.txt:characters[0:6000] (zero-based Unicode offsets); snapshot SHA-256: f772a70440693cc4aa39a2d00eb13472a9f4139dece91f3fac19d52ad7fc2087
- **chunk_20aff608c3e8516e185830d7**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[58126:59772] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_bf02f4d88a13ecc63477b8ab**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[73848:75519] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_d4b6ec17fa2cd6a1a3d7b0b1**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[59528:61135] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_6d40ed804857fe9823b396ea**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[67691:69457] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_a2157b43fcb6278657ae786d**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[119754:121525] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_ca4eaef28c619ddbed9e3319**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[121275:122853] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_f0b1d3202995d32310105213**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[96661:98436] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b

## Reproducible calculations


## Resource use

Model attempts: 7; tool attempts: 4; active seconds: 113.28.
Token usage (when returned by provider): {"planner_steps": 5, "total_tokens": 101083, "total_tokens_reported_calls": 7, "prompt_tokens": 94596, "prompt_tokens_reported_calls": 7, "completion_tokens": 6487, "completion_tokens_reported_calls": 7, "cached_input_tokens": 46355, "cached_input_tokens_reported_calls": 7, "cache_write_tokens": 9268, "cache_write_tokens_reported_calls": 7, "cache_write_30m_tokens": 9268, "cache_write_30m_tokens_reported_calls": 7, "cache_write_12h_tokens": 0, "cache_write_12h_tokens_reported_calls": 7, "reasoning_tokens": 310, "reasoning_tokens_reported_calls": 7}
