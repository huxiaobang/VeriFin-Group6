# VeriFin research report

**Status:** complete
**Stop reason:** completed
**Request:** 百事最近一季表现怎么样？
**Model mode:** configured_model
**Scope version:** 3

Model verification is an assessment against the available evidence, not an independent guarantee.

## Scope

```json
{
  "cutoff": "2026-11-16",
  "requirements": [
    {
      "id": "R1",
      "text": "确认截至2026年11月16日可核实的百事公司最新已披露季度及数据覆盖限制。"
    },
    {
      "id": "R2",
      "text": "用中文概述该季度收入、盈利及同比表现，区分报告口径与有机或调整后口径，并依据可得披露说明主要业务表现、管理层展望和关键风险。"
    }
  ]
}
```

## Findings

### claim:coverage — Supported

截至2026年11月16日，在可核实的有限本地资料中，百事最新披露季度为2026年第三季度，即截至9月5日的12周；这不是实时全网核验。业绩公告正文日期为10月8日，而目录记录的SEC接收时间为10月7日，两者日期口径不同。

Evidence: metadata_ad760c6692f956a4d6a61c1a, span_e15075b0f56ab75de1fba9f1
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

目录明确仅覆盖截至截止日的4份本地文件，最新报告日为2026年9月5日；公告明确为第三季度、截至该日的12周。正文10月8日与目录SEC接收时间10月7日的区别准确，且结论限定于有限本地资料。
metadata_ad760c6692f956a4d6a61c1a span_e15075b0f56ab75de1fba9f1

### claim:revenue — Supported

第三季度报告净收入为25,274百万美元，同比增长5.6%；有机收入增长3.1%。公告另列收购及剥离净贡献1.7个百分点、汇率折算贡献0.7个百分点，因此报告收入增速不能全部视为内生增长；各项披露存在四舍五入。

Evidence: span_e15075b0f56ab75de1fba9f1
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度汇总表列示净收入25,274百万美元、同比5.6%；季度讨论列示有机增长3.1%、收购剥离贡献1.7个百分点、汇率贡献0.7个百分点。有机指标被明确标为非GAAP，后续汇总表注明数字可能因四舍五入不能加总。
span_e15075b0f56ab75de1fba9f1 span_866e7defb63fa38a7cf48e44

### claim:operating-profit — Supported

第三季度报告营业利润为4,260百万美元，同比增长19%；核心营业利润为4,277百万美元，同比增长3%。报告利润改善明显强于核心口径所反映的改善。

Evidence: span_e15075b0f56ab75de1fba9f1
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

第三季度而非年初至今列分别列示报告营业利润4,260百万美元、增长19%，核心营业利润4,277百万美元、增长3%；报告增幅明显高于核心增幅的比较成立。
span_e15075b0f56ab75de1fba9f1

### claim:margin — Supported

第三季度报告营业利润率为16.9%，同比上升195个基点；核心营业利润率同为16.9%，但同比下降35个基点。这里采用公告披露的变动幅度，不能仅凭四舍五入后显示的利润率重算。

Evidence: span_e15075b0f56ab75de1fba9f1
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度表直接披露报告营业利润率16.9%、上升195基点，核心营业利润率16.9%、下降35基点；采用原披露变动而非从一位小数显示值重算，处理恰当。
span_e15075b0f56ab75de1fba9f1

### claim:eps — Supported

第三季度报告每股收益为2.23美元，同比增长17%；核心每股收益为2.34美元，同比增长2%，核心固定汇率每股收益增长1.5%。因此，报告每股收益的两位数增长不代表调整后盈利也有同等强度。

Evidence: span_e15075b0f56ab75de1fba9f1
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度表对应EPS为2.23美元、增长17%，核心EPS为2.34美元、增长2%，核心固定汇率EPS增长1.5%。关于报告增长不能代表调整后同等增长的判断有直接比较依据。
span_e15075b0f56ab75de1fba9f1

### claim:reported-profit-attribution — Supported

管理层将本季度报告营业利润及利润率的改善，主要归因于收购及剥离相关费用或收益的有利净影响，以及商品衍生品盯市净收益的有利影响。这是公司对报告口径同比变化的解释，而非独立验证的因果分解。

Evidence: span_e15075b0f56ab75de1fba9f1
Calculations: 

季度讨论将19%的报告营业利润增长及195基点利润率扩张主要归于收购剥离相关费用或收益的有利净影响、商品衍生品盯市净收益。草稿保留公司归因，未误用年初至今的上年减值解释，也未宣称独立量化因果。
span_e15075b0f56ab75de1fba9f1

### claim:core-profit-attribution — Supported

就核心营业利润表现，管理层称生产率节约、有效净定价及关税退款提供支持，其中关税退款对核心营业利润增长有4个百分点的有利影响；部分支持被若干运营成本上升及更高的广告营销费用抵消。不能把本季核心利润增长全部理解为持续性经营改善。

Evidence: span_e15075b0f56ab75de1fba9f1
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度讨论明确列出生产率节约、有效净定价及关税退款的4个百分点有利影响，并列出运营成本和广告营销费用的抵消。草稿将该影响表述为核心营业利润增长贡献而非利润率贡献，且未将退款支持认定为持续性改善。
span_e15075b0f56ab75de1fba9f1

### claim:north-america-foods — Supported

北美食品业务仍承压：本季度PFNA报告收入和有机收入均基本持平，核心固定汇率营业利润下降12%。管理层称，方便食品收入趋势环比改善，咸味零食销量及销量份额改善，但较低的有效净定价形成抵消；趋势改善尚不等于利润恢复增长。

Evidence: span_866e7defb63fa38a7cf48e44, span_e15075b0f56ab75de1fba9f1
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

第三季度PFNA行的报告及有机收入变化均为横线，核心固定汇率营业利润为下降12%。季度讨论说明方便食品收入趋势环比改善，受咸味零食销量和份额改善支持、被较低净定价抵消；草稿正确区分趋势改善与利润同比恢复。
span_866e7defb63fa38a7cf48e44 span_e15075b0f56ab75de1fba9f1

### claim:north-america-beverages — Supported

北美饮料业务本季度报告收入增长5%，但有机收入基本持平，剔除收购及剥离影响的饮料销量下降2%；管理层称收入增长主要反映2025年收购的贡献，不能据报告收入增长认定其内生需求已明显转强。

Evidence: span_866e7defb63fa38a7cf48e44, span_e15075b0f56ab75de1fba9f1
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

第三季度PBNA行列示报告收入增长5%、有机收入持平、饮料销量下降2%；销量脚注明确排除收购剥离影响。季度讨论将收入增长主要归于2025年收购，因而草稿对内生需求推断的限制合理。
span_866e7defb63fa38a7cf48e44 span_e15075b0f56ab75de1fba9f1

### claim:international — Supported

国际业务增长较强：本季度国际饮料特许经营、欧洲中东非洲、拉美食品和亚太食品的有机收入分别增长7%、9%、6%和9%。这些分部增长率不能直接当作其对集团增长的贡献。

Evidence: span_866e7defb63fa38a7cf48e44
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

第三季度收入表的有机增长列依次列示IB Franchise 7%、EMEA 9%、LatAm Foods 6%、Asia Pacific Foods 9%。这些为各分部增长率，并非集团增长的加权贡献，草稿保持了该区别。
span_866e7defb63fa38a7cf48e44

### claim:revenue-outlook — Supported

全年收入展望方面，公司将2026年有机收入增长预期由2%—4%收窄至约3%，报告净收入增长预期由4%—6%调整至约6%；同期汇率及收购净贡献假设各由约1个百分点提高至约1.5个百分点，不能将报告收入指引的变化直接等同于有机增长加速。

Evidence: span_866e7defb63fa38a7cf48e44
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

2026财年指引表的当前及此前列分别支持有机增长约3%对2%—4%、净收入约6%对4%—6%，以及汇率、收购净贡献各约1.5%对约1%。草稿准确保留预测性质，未将报告指引变化认作有机增长加速。
span_866e7defb63fa38a7cf48e44

### claim:earnings-outlook — Supported

盈利展望下调更值得关注：2026年核心固定汇率每股收益增长指引，由此前4%—6%区间的低端降至1%—2%；核心每股收益增长指引，由此前5%—7%区间的低端降至2.5%—3.5%。这些是非GAAP前瞻指引，不是已实现结果。

Evidence: span_866e7defb63fa38a7cf48e44
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

指引表列示核心固定汇率EPS增长由4%—6%的低端改为1%—2%，核心EPS增长由5%—7%的低端改为2.5%—3.5%；此前区间低端的限定及非GAAP前瞻属性均保留正确。
span_866e7defb63fa38a7cf48e44

### claim:risks — Supported

后续主要观察风险是北美修复力度，以及投入与成本对利润率的挤压。管理层表示将加大创新、品牌建设和渠道执行投入，并计划以进一步结构性降本支持这些投入、缓解投入品成本通胀上升；这些措施的效果尚待兑现。本季核心利润率下降和全年核心EPS增长指引下调，意味着不能仅凭报告利润大增判断盈利压力已经解除。

Evidence: span_e15075b0f56ab75de1fba9f1, span_866e7defb63fa38a7cf48e44
Calculations: 

管理层原话涉及改善北美表现、创新和品牌及渠道投入、进一步结构性降本和上升的投入品成本通胀。结合本季核心利润率下降35基点及下调全年核心EPS增长指引，草稿将其作为后续观察风险而非已证实的未来结果，表述有据。
span_e15075b0f56ab75de1fba9f1 span_866e7defb63fa38a7cf48e44

## Answers to your questions

**百事最近一季表现怎么样？**

answered — 第三季度报告营业利润为4,260百万美元，同比增长19%；核心营业利润为4,277百万美元，同比增长3%。报告利润改善明显强于核心口径所反映的改善。 截至2026年11月16日，在可核实的有限本地资料中，百事最新披露季度为2026年第三季度，即截至9月5日的12周；这不是实时全网核验。业绩公告正文日期为10月8日，而目录记录的SEC接收时间为10月7日，两者日期口径不同。 第三季度报告净收入为25,274百万美元，同比增长5.6%；有机收入增长3.1%。公告另列收购及剥离净贡献1.7个百分点、汇率折算贡献0.7个百分点，因此报告收入增速不能全部视为内生增长；各项披露存在四舍五入。 第三季度报告营业利润率为16.9%，同比上升195个基点；核心营业利润率同为16.9%，但同比下降35个基点。这里采用公告披露的变动幅度，不能仅凭四舍五入后显示的利润率重算。 第三季度报告每股收益为2.23美元，同比增长17%；核心每股收益为2.34美元，同比增长2%，核心固定汇率每股收益增长1.5%。因此，报告每股收益的两位数增长不代表调整后盈利也有同等强度。 管理层将本季度报告营业利润及利润率的改善，主要归因于收购及剥离相关费用或收益的有利净影响，以及商品衍生品盯市净收益的有利影响。这是公司对报告口径同比变化的解释，而非独立验证的因果分解。 就核心营业利润表现，管理层称生产率节约、有效净定价及关税退款提供支持，其中关税退款对核心营业利润增长有4个百分点的有利影响；部分支持被若干运营成本上升及更高的广告营销费用抵消。不能把本季核心利润增长全部理解为持续性经营改善。 北美食品业务仍承压：本季度PFNA报告收入和有机收入均基本持平，核心固定汇率营业利润下降12%。管理层称，方便食品收入趋势环比改善，咸味零食销量及销量份额改善，但较低的有效净定价形成抵消；趋势改善尚不等于利润恢复增长。 北美饮料业务本季度报告收入增长5%，但有机收入基本持平，剔除收购及剥离影响的饮料销量下降2%；管理层称收入增长主要反映2025年收购的贡献，不能据报告收入增长认定其内生需求已明显转强。 国际业务增长较强：本季度国际饮料特许经营、欧洲中东非洲、拉美食品和亚太食品的有机收入分别增长7%、9%、6%和9%。这些分部增长率不能直接当作其对集团增长的贡献。 全年收入展望方面，公司将2026年有机收入增长预期由2%—4%收窄至约3%，报告净收入增长预期由4%—6%调整至约6%；同期汇率及收购净贡献假设各由约1个百分点提高至约1.5个百分点，不能将报告收入指引的变化直接等同于有机增长加速。 盈利展望下调更值得关注：2026年核心固定汇率每股收益增长指引，由此前4%—6%区间的低端降至1%—2%；核心每股收益增长指引，由此前5%—7%区间的低端降至2.5%—3.5%。这些是非GAAP前瞻指引，不是已实现结果。 后续主要观察风险是北美修复力度，以及投入与成本对利润率的挤压。管理层表示将加大创新、品牌建设和渠道执行投入，并计划以进一步结构性降本支持这些投入、缓解投入品成本通胀上升；这些措施的效果尚待兑现。本季核心利润率下降和全年核心EPS增长指引下调，意味着不能仅凭报告利润大增判断盈利压力已经解除。 [claim:operating-profit, claim:coverage, claim:revenue, claim:margin, claim:eps, claim:reported-profit-attribution, claim:core-profit-attribution, claim:north-america-foods, claim:north-america-beverages, claim:international, claim:revenue-outlook, claim:earnings-outlook, claim:risks]

## Unanswered items and issues

None identified.

## Evidence and source locations

- **metadata_ad760c6692f956a4d6a61c1a**（本轮留存） (PEP;  to ): manifest.json
  Locator: documents[3], documents[0], documents[1], documents[2]; snapshot SHA-256: dbf78f320aca77b3e4dfaa055e02f216fab9a11fcfa79426fb195da46f9d64f5
- **span_e15075b0f56ab75de1fba9f1**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[0:6000] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **span_866e7defb63fa38a7cf48e44**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[6000:12000] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899

## Reproducible calculations


## Resource use

Model attempts: 7; tool attempts: 4; active seconds: 106.43.
Token usage (when returned by provider): {"planner_steps": 5, "total_tokens": 78302, "total_tokens_reported_calls": 7, "prompt_tokens": 72692, "prompt_tokens_reported_calls": 7, "completion_tokens": 5610, "completion_tokens_reported_calls": 7, "cached_input_tokens": 33877, "cached_input_tokens_reported_calls": 7, "cache_write_tokens": 5611, "cache_write_tokens_reported_calls": 7, "cache_write_30m_tokens": 5611, "cache_write_30m_tokens_reported_calls": 7, "cache_write_12h_tokens": 0, "cache_write_12h_tokens_reported_calls": 7, "reasoning_tokens": 178, "reasoning_tokens_reported_calls": 7}
