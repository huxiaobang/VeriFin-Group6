# VeriFin research report

**Status:** complete
**Stop reason:** completed
**Request:** 回到最开始，你说利润比收入涨得快。那这季到底更多是加盟业务撑起来的，还是另有原因？
**Model mode:** configured_model
**Scope version:** 12

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
      "id": "request:episode_ad223a58ece545b9aa3227556e8c9e64",
      "text": "解释麦当劳2026年第三季度利润增长快于收入的原因，量化加盟餐厅利润、自营餐厅利润、管理费用及其他营业收支对集团营业利润同比增量的贡献，判断加盟业务是否为主要增量来源，并区分公司归因、报表算术及GAAP与调整后口径。"
    }
  ],
  "period_start": "2026-07-01",
  "period_end": "2026-09-30"
}
```

## Findings

Research skills: unit-profit-reconciliation

### claim:profit_driver_bounded_answer — Supported

不是主要靠加盟业务单独撑起来。2026年第三季度，加盟餐厅利润是重要增量来源，但按披露整数比较，其贡献未过半，与其他营业净收入改善的贡献几乎相当；管理费用下降也提供了帮助。这是报表增量比较，不是对经营原因的因果分配。

Evidence: chunk_6d40ed804857fe9823b396ea, chunk_20aff608c3e8516e185830d7, span_b081d896504fbf43a75bb556
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度加盟餐厅利润由3,697升至3,800百万美元，显示整数增量为103；其他营业净收入由37升至139百万美元，增加102；管理费用由785降至759百万美元。加盟增量不足集团披露增量239的一半，与其他营业净收入增量接近，支持“不靠加盟单独撑起”的限定判断；草稿正确区分算术比较与因果分配。
chunk_6d40ed804857fe9823b396ea chunk_20aff608c3e8516e185830d7 span_b081d896504fbf43a75bb556

### claim:quarterly_operating_profit_increment_bridge — Supported

第三季度营业利润增量桥如下，正号表示提高集团营业利润，金额单位均为百万美元。这是原表披露整数的近似算术，并非已保存的工具计算结果，也不是管理层对各项原因的贡献分配。  \| 项目 \| 2025年第三季度 \| 2026年第三季度 \| 对营业利润同比增量的贡献 \| \|---\|---:\|---:\|---:\| \| 加盟餐厅利润 \| 3,697 \| 3,800 \| 约+103 \| \| 自营餐厅利润 \| 391 \| 392 \| 约+1 \| \| 销售、一般及管理费用 \| 785 \| 759 \| 约+26，费用下降 \| \| 其他营业净收入 \| 37 \| 139 \| +102，公告直接披露该项变动 \| \| 其他收入减其他餐厅费用 \| 151−135 \| 189−167 \| 约+6 \| \| 上述贡献合计 \| — \| — \| 约+238 \| \| 公告直接披露的集团营业利润增量 \| — \| — \| +239 \|  加盟与其他营业净收入的贡献接近，自营餐厅利润则基本持平。其他收入及其他餐厅费用这两行也必须纳入，不能只把餐厅利润、管理费用和其他营业收支相加。

Evidence: chunk_6d40ed804857fe9823b396ea, chunk_20aff608c3e8516e185830d7, span_b081d896504fbf43a75bb556
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

已逐项复核2026及2025年第三季度美元百万列：加盟利润差103、自营利润差1、管理费用下降贡献26、其他营业净收入增加102；其他收入减其他餐厅费用的变化为(189−167)−(151−135)=6，合计238。公告另直接列示营业利润增加239。桥中各项未重复计入，费用下降的贡献符号正确，并明确了显示整数与工具计算的区别。
chunk_6d40ed804857fe9823b396ea chunk_20aff608c3e8516e185830d7 span_b081d896504fbf43a75bb556

### claim:quantitative_bridge_limitation — Supported

上述近似桥与公告直接披露增量相差约1百万美元，应保留为披露整数精度下的勾稽差异，不强行凑平。两期各自也不能用显示整数完全勾稽：2026年营业利润显示为3,595百万美元，2025年为3,357百万美元，而公告直接列示同比增加239百万美元。公司提示财务表格行列可能因舍入不相加。本次分析器未生成可保存的利润桥，营业利润率调节调用及加盟利润指标查询也未成功；因此这里没有声称完成工具计算核验。

Evidence: span_b081d896504fbf43a75bb556, chunk_80fac8d893ffff0b3c0923ee, chunk_90e48c44adbcf3862d8a712e
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

公告显示营业利润3,595及3,357百万美元，同时直接列示增加239；两显示值相减为238。按草稿所用餐厅利润等显示整数重建，两期分别为3,594及3,356，也各差1。10-Q明确提示表格可能因舍入不相加，因此保留精度差异而不归为经营原因恰当。上下文调用记录另确认分析器未生成结果、调节及指标查询失败，计算列表为空，草稿没有虚称工具核验完成。
span_b081d896504fbf43a75bb556 chunk_80fac8d893ffff0b3c0923ee chunk_90e48c44adbcf3862d8a712e

### claim:other_operating_income_components — Supported

其他营业净收入并不全是加盟店日常销售带来的收益。季度明细包括：餐厅业务出售收益本季97百万美元、上年35百万美元；未合并附属公司收益分别为75和63百万美元；资产处置及其他净收支由上年21百万美元净费用转为本季23百万美元净收入；减值及其他净费用则由39升至56百万美元，形成抵消。这些都是其他营业净收入小计的组成部分，不能再重复加到集团增量桥里。

Evidence: chunk_d202b96c8de92effd96b8946
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

其他营业收支表的季度列分别列示餐厅出售收益97及35、附属公司收益75及63、资产处置及其他净收支本季收入23及上年费用21、减值等净费用56及39百万美元。草稿正确转换收入和费用符号，指出费用增加形成抵消，并避免将小计明细再次加入集团桥。
chunk_d202b96c8de92effd96b8946

### claim:other_operating_income_management_explanation — Supported

对于上述明细，公司解释：餐厅出售收益增加反映美国出售的餐厅更多；附属公司收益改善主要反映中国餐厅经营表现改善；资产处置及其他净收支的变化主要反映剩余物业出售收益提高。这些解释各自针对相应项目，不能统称为同店销售增长，也不能把某项原因套到整个其他营业净收入小计。

Evidence: chunk_d202b96c8de92effd96b8946
Calculations: 

表后说明将季度餐厅出售收益增加归于美国出售餐厅更多，将附属公司收益改善主要归于中国餐厅经营表现改善，将资产处置及其他净收支变化主要归于剩余物业出售收益提高。草稿逐项保留管理层归属，未把仅适用于九个月的国际运营市场出售因素移入季度，也未把单项解释扩大至整个小计。
chunk_d202b96c8de92effd96b8946

### claim:adjusted_profit_management_attribution — Supported

管理层对剔除相关费用后的季度营业利润增长，主要归因于销售带动的加盟餐厅利润增加，以及各分部其他营业收入提高。这与增量桥显示的两项重要支撑方向一致，但管理层没有在这段说明中分配各自贡献比例。

Evidence: chunk_bf02f4d88a13ecc63477b8ab
Calculations: 

非GAAP调节表后的说明明确将季度及九个月调整后营业利润增长主要归于销售带动的加盟餐厅利润和各分部其他营业收入提高，未提供两者贡献比例。草稿准确限定为调整后季度口径及管理层解释，没有把九个月管理费用上升的抵消因素误用于季度。
chunk_bf02f4d88a13ecc63477b8ab

### claim:gaap_adjusted_distinction — Supported

所以，“利润比收入涨得快”应理解为多项因素共同作用：公司披露本季收入同比增长1%，GAAP营业利润增长7%；剔除本季56百万美元、上年同期39百万美元的相关费用后，非GAAP营业利润增长8%。调整表只加回这些费用，并没有一并剔除餐厅业务出售收益等，因此调整后增长也不等于纯粹的日常经营增长。

Evidence: span_de69cbdff59e9d164a29cd48, chunk_bf02f4d88a13ecc63477b8ab, chunk_d202b96c8de92effd96b8946
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度公告披露收入增长1%、GAAP营业利润增长7%；营业利润调节表分别加回56及39百万美元，得到非GAAP营业利润3,651及3,396百万美元、增长8%。该调节没有剔除其他营业收支表中的餐厅出售收益，支持不能把调整后增速视为纯粹日常经营增长的提醒。
span_de69cbdff59e9d164a29cd48 chunk_bf02f4d88a13ecc63477b8ab chunk_d202b96c8de92effd96b8946

## Answers to your questions

**回到最开始，你说利润比收入涨得快。那这季到底更多是加盟业务撑起来的，还是另有原因？**

answered — 不是主要靠加盟业务单独撑起来。2026年第三季度，加盟餐厅利润是重要增量来源，但按披露整数比较，其贡献未过半，与其他营业净收入改善的贡献几乎相当；管理费用下降也提供了帮助。这是报表增量比较，不是对经营原因的因果分配。 第三季度营业利润增量桥如下，正号表示提高集团营业利润，金额单位均为百万美元。这是原表披露整数的近似算术，并非已保存的工具计算结果，也不是管理层对各项原因的贡献分配。

\| 项目 \| 2025年第三季度 \| 2026年第三季度 \| 对营业利润同比增量的贡献 \| \|---\|---:\|---:\|---:\| \| 加盟餐厅利润 \| 3,697 \| 3,800 \| 约+103 \| \| 自营餐厅利润 \| 391 \| 392 \| 约+1 \| \| 销售、一般及管理费用 \| 785 \| 759 \| 约+26，费用下降 \| \| 其他营业净收入 \| 37 \| 139 \| +102，公告直接披露该项变动 \| \| 其他收入减其他餐厅费用 \| 151−135 \| 189−167 \| 约+6 \| \| 上述贡献合计 \| — \| — \| 约+238 \| \| 公告直接披露的集团营业利润增量 \| — \| — \| +239 \|

加盟与其他营业净收入的贡献接近，自营餐厅利润则基本持平。其他收入及其他餐厅费用这两行也必须纳入，不能只把餐厅利润、管理费用和其他营业收支相加。 上述近似桥与公告直接披露增量相差约1百万美元，应保留为披露整数精度下的勾稽差异，不强行凑平。两期各自也不能用显示整数完全勾稽：2026年营业利润显示为3,595百万美元，2025年为3,357百万美元，而公告直接列示同比增加239百万美元。公司提示财务表格行列可能因舍入不相加。本次分析器未生成可保存的利润桥，营业利润率调节调用及加盟利润指标查询也未成功；因此这里没有声称完成工具计算核验。 其他营业净收入并不全是加盟店日常销售带来的收益。季度明细包括：餐厅业务出售收益本季97百万美元、上年35百万美元；未合并附属公司收益分别为75和63百万美元；资产处置及其他净收支由上年21百万美元净费用转为本季23百万美元净收入；减值及其他净费用则由39升至56百万美元，形成抵消。这些都是其他营业净收入小计的组成部分，不能再重复加到集团增量桥里。 对于上述明细，公司解释：餐厅出售收益增加反映美国出售的餐厅更多；附属公司收益改善主要反映中国餐厅经营表现改善；资产处置及其他净收支的变化主要反映剩余物业出售收益提高。这些解释各自针对相应项目，不能统称为同店销售增长，也不能把某项原因套到整个其他营业净收入小计。 管理层对剔除相关费用后的季度营业利润增长，主要归因于销售带动的加盟餐厅利润增加，以及各分部其他营业收入提高。这与增量桥显示的两项重要支撑方向一致，但管理层没有在这段说明中分配各自贡献比例。 所以，“利润比收入涨得快”应理解为多项因素共同作用：公司披露本季收入同比增长1%，GAAP营业利润增长7%；剔除本季56百万美元、上年同期39百万美元的相关费用后，非GAAP营业利润增长8%。调整表只加回这些费用，并没有一并剔除餐厅业务出售收益等，因此调整后增长也不等于纯粹的日常经营增长。 [claim:profit_driver_bounded_answer, claim:quarterly_operating_profit_increment_bridge, claim:quantitative_bridge_limitation, claim:other_operating_income_components, claim:other_operating_income_management_explanation, claim:adjusted_profit_management_attribution, claim:gaap_adjusted_distinction]

## Unanswered items and issues

None identified.

## Evidence and source locations

- **metadata_292ee90e389c62c63a2d1c21** (MCD;  to ): manifest.json
  Locator: documents[0], documents[3], documents[1], documents[2]; snapshot SHA-256: c98ef96e09ba9ea5452972813fe81d0bc5ab0d0a90cbc81905b9e9b7251dd5d2
- **span_de69cbdff59e9d164a29cd48** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000080/exhibit991-9302026.htm>)
  Locator: snapshots/exhibit991-9302026.htm.txt:characters[0:6000] (zero-based Unicode offsets); snapshot SHA-256: f772a70440693cc4aa39a2d00eb13472a9f4139dece91f3fac19d52ad7fc2087
- **chunk_20aff608c3e8516e185830d7** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[58126:59772] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_bf02f4d88a13ecc63477b8ab** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[73848:75519] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_d4b6ec17fa2cd6a1a3d7b0b1** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[59528:61135] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_6d40ed804857fe9823b396ea** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[67691:69457] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_a2157b43fcb6278657ae786d** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[119754:121525] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_ca4eaef28c619ddbed9e3319** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[121275:122853] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_f0b1d3202995d32310105213** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[96661:98436] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_f20595b2abb202e49b610e85** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[41138:42932] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_ca1efc0490579de16a0391e0** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[99643:101194] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_99d17792d84508e31fc75b83** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[39724:41382] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_149bb9c87a7ef7432668eebb** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[60887:62439] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_6dfa0efaee68fd8342f1524b** (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[62191:63657] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_d202b96c8de92effd96b8946**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[71526:73154] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_90e48c44adbcf3862d8a712e**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[55125:56924] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_f5c04d16342049b67b1d3a2f**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[5287:7086] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_80fac8d893ffff0b3c0923ee**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[35400:37181] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **span_b081d896504fbf43a75bb556**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000080/exhibit991-9302026.htm>)
  Locator: snapshots/exhibit991-9302026.htm.txt:characters[8000:12693] (zero-based Unicode offsets); snapshot SHA-256: f772a70440693cc4aa39a2d00eb13472a9f4139dece91f3fac19d52ad7fc2087

## Reproducible calculations


## Resource use

Model attempts: 11; tool attempts: 5; active seconds: 215.80.
Token usage (when returned by provider): {"planner_steps": 7, "total_tokens": 430565, "total_tokens_reported_calls": 11, "prompt_tokens": 419452, "prompt_tokens_reported_calls": 11, "completion_tokens": 11113, "completion_tokens_reported_calls": 11, "cached_input_tokens": 294265, "cached_input_tokens_reported_calls": 11, "cache_write_tokens": 46879, "cache_write_tokens_reported_calls": 11, "cache_write_30m_tokens": 46879, "cache_write_30m_tokens_reported_calls": 11, "cache_write_12h_tokens": 0, "cache_write_12h_tokens_reported_calls": 11, "reasoning_tokens": 1020, "reasoning_tokens_reported_calls": 11}
