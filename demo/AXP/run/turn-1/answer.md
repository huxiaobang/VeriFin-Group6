# VeriFin research report

**Status:** complete
**Stop reason:** completed
**Request:** 美国运通最近一季表现怎么样？
**Model mode:** configured_model
**Scope version:** 4

Model verification is an assessment against the available evidence, not an independent guarantee.

## Scope

```json
{
  "cutoff": "2026-11-16",
  "entities": [
    "AXP"
  ],
  "requirements": [
    {
      "id": "R1",
      "text": "确认截至2026-11-16美国运通最新可核实的已披露财季及资料时效限制。"
    },
    {
      "id": "R2",
      "text": "用原始披露概述该季收入、净利润和每股收益及同比表现，说明主要业务驱动、信用风险或费用变化，并在有披露时交代全年展望；以中文给出有证据支持的简明评价。"
    }
  ]
}
```

## Findings

### claim:latest-quarter — Supported

截至2026年11月16日，在有限本地资料快照中，美国运通最新可核实的已披露财季为截至2026年9月30日的第三季度，业绩公告及10-Q均于10月23日披露。这不是实时联网核验，不能据此保证本地目录以外没有更新披露。

Evidence: metadata_286185e77f063ec7f55fd4c8
Calculations: 

截至2026-11-16的有限回放目录列示，最新报告日为2026-09-30，相关10-Q和业绩公告均于2026-10-23公开。声明明确限制于本地快照，未把目录覆盖等同于实时完整核验。
metadata_286185e77f063ec7f55fd4c8

### claim:revenue — Supported

第三季度合并收入（扣除利息支出后）约201亿美元，同比增长9%，创公司第三季度收入纪录。

Evidence: span_3b74985e92f2fa70a68c88af
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

公告季度表列示2026年第三季度扣除利息支出后收入20,062百万美元、上年同期18,426百万美元，同比增长9%；标题明确为第三季度收入纪录。约201亿美元的换算和合并口径正确。
span_3b74985e92f2fa70a68c88af

### claim:net-income — Supported

第三季度净利润约37亿美元，同比增长28%。

Evidence: span_3b74985e92f2fa70a68c88af
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度表列示净利润3,712百万美元，上年同期2,902百万美元，披露同比增长28%；约37亿美元的表述与原始金额一致，未误用九个月累计数据。
span_3b74985e92f2fa70a68c88af

### claim:eps — Supported

第三季度报告摊薄每股收益为5.42美元，同比增长31%；公司披露的非GAAP调整后每股收益为4.68美元，同比增长13%。两种口径的增长力度应加以区分。

Evidence: span_3b74985e92f2fa70a68c88af
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度表分别列示摊薄EPS为5.42美元、同比增长31%，调整后EPS为4.68美元、同比增长13%。公告脚注明确调整后摊薄EPS为非GAAP指标，草稿正确区分两种口径。
span_3b74985e92f2fa70a68c88af

### claim:adjustment — Supported

调整后业绩剔除了出售GBTG股权的收益影响，但扣除了其中用于相关效率提升重组的部分。公告披露，该出售收益为税前9.82亿美元、税后7.51亿美元；不能将出售收益全额直接视为报告利润与调整后利润的差额。

Evidence: span_3b74985e92f2fa70a68c88af
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

公告净利润说明段披露GBTG股权出售收益税前982百万美元、税后751百万美元，并说明调整剔除的是扣除用于效率提升重组部分后的收益影响。因此不能把全部出售收益直接视为报告与调整后利润之差；草稿未擅自量化净调整额。
span_3b74985e92f2fa70a68c88af

### claim:business-drivers — Supported

管理层将收入增长主要归因于持卡人消费增加、卡余额增长支持净利息收入上升，以及卡费收入强劲增长。

Evidence: span_3b74985e92f2fa70a68c88af
Calculations: 

Third-Quarter 2026 Results段将收入增长主要归因于持卡人消费增加、卡余额增长支持净利息收入增加及卡费增长。草稿保留管理层归因，未声称独立验证因果关系。
span_3b74985e92f2fa70a68c88af

### claim:credit — Supported

信用损失准备费用约10亿美元，低于上年同期的13亿美元。公司解释，本季释放准备金，而上年同期增提准备金，但净核销金额增加部分抵消了这一影响；净核销率则由1.9%降至1.8%。因此，准备费用下降并不等同于所有信用指标都在改善。

Evidence: span_3b74985e92f2fa70a68c88af
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

公告披露合并信用损失准备费用由13亿美元降至10亿美元，本期释放准备金、上年同期增提准备金，较高净核销金额部分抵消该影响；净核销率由1.9%降至1.8%。草稿正确区分核销金额和核销率，所作谨慎评价有据。
span_3b74985e92f2fa70a68c88af

### claim:expenses — Supported

合并费用约145亿美元，同比增长9%。公司称，主要压力来自消费增加及美国白金卡焕新带来的可变客户互动成本上升；经营费用下降部分抵消了压力，而经营费用下降主要受GBTG出售收益影响，法律准备金增加和重组成本则构成抵消。

Evidence: span_3b74985e92f2fa70a68c88af
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

公告披露费用145亿美元、同比增长9%，并依次说明客户互动成本上升、经营费用下降的抵消作用，以及GBTG出售收益、法律准备金增加和重组成本对经营费用的相反方向影响；草稿方向和管理层归属均一致。
span_3b74985e92f2fa70a68c88af

### claim:tax — Supported

有效税率由上年同期的24.1%降至18.1%。公司称，主要原因是解决部分以前期间税务事项带来的当期离散税收利益；较低税率也是本季报告净利润的有利因素，但公告这段说明未量化其对利润增长的贡献。

Evidence: span_3b74985e92f2fa70a68c88af
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

公告明确本季有效税率18.1%、上年同期24.1%，主要反映解决以前期间税务事项的当期离散税收利益。较低税率有利于税后利润属于合理会计解释；草稿只指出该段未量化利润增长贡献，未否认已披露的原因，也未虚构金额。
span_3b74985e92f2fa70a68c88af

### claim:outlook — Supported

公司上调2026年全年EPS指引至18.30—18.60美元，调整后EPS指引为17.60—17.90美元，并表示仍有望实现全年收入增长10%。这些是管理层展望，并非已实现业绩。

Evidence: span_3b74985e92f2fa70a68c88af
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

首席执行官引述明确上调2026全年EPS指引至18.30—18.60美元，调整后为17.60—17.90美元，并表示有望实现全年收入增长10%。草稿正确将其作为2026-10-23披露的管理层展望，而非已实现结果。
span_3b74985e92f2fa70a68c88af

### claim:assessment — Supported

简评：这一季收入和调整后EPS均保持增长，表现稳健；但报告EPS的高增长不能全部归因于持续经营改善，GBTG出售收益、较低税率及准备金释放都需单独看待，同时客户互动成本仍在上升。

Evidence: span_3b74985e92f2fa70a68c88af
Calculations: 

收入增长9%、调整后EPS增长13%支持稳健增长的定性判断；出售收益、税率下降、准备金释放及客户互动成本上升均有原文支持。草稿未把全部报告EPS增幅归因于持续经营改善，也未分配未经量化的贡献。
span_3b74985e92f2fa70a68c88af

## Answers to your questions

**美国运通最近一季表现怎么样？**

answered — 简评：这一季收入和调整后EPS均保持增长，表现稳健；但报告EPS的高增长不能全部归因于持续经营改善，GBTG出售收益、较低税率及准备金释放都需单独看待，同时客户互动成本仍在上升。 截至2026年11月16日，在有限本地资料快照中，美国运通最新可核实的已披露财季为截至2026年9月30日的第三季度，业绩公告及10-Q均于10月23日披露。这不是实时联网核验，不能据此保证本地目录以外没有更新披露。 第三季度合并收入（扣除利息支出后）约201亿美元，同比增长9%，创公司第三季度收入纪录。 第三季度净利润约37亿美元，同比增长28%。 第三季度报告摊薄每股收益为5.42美元，同比增长31%；公司披露的非GAAP调整后每股收益为4.68美元，同比增长13%。两种口径的增长力度应加以区分。 调整后业绩剔除了出售GBTG股权的收益影响，但扣除了其中用于相关效率提升重组的部分。公告披露，该出售收益为税前9.82亿美元、税后7.51亿美元；不能将出售收益全额直接视为报告利润与调整后利润的差额。 管理层将收入增长主要归因于持卡人消费增加、卡余额增长支持净利息收入上升，以及卡费收入强劲增长。 信用损失准备费用约10亿美元，低于上年同期的13亿美元。公司解释，本季释放准备金，而上年同期增提准备金，但净核销金额增加部分抵消了这一影响；净核销率则由1.9%降至1.8%。因此，准备费用下降并不等同于所有信用指标都在改善。 合并费用约145亿美元，同比增长9%。公司称，主要压力来自消费增加及美国白金卡焕新带来的可变客户互动成本上升；经营费用下降部分抵消了压力，而经营费用下降主要受GBTG出售收益影响，法律准备金增加和重组成本则构成抵消。 有效税率由上年同期的24.1%降至18.1%。公司称，主要原因是解决部分以前期间税务事项带来的当期离散税收利益；较低税率也是本季报告净利润的有利因素，但公告这段说明未量化其对利润增长的贡献。 公司上调2026年全年EPS指引至18.30—18.60美元，调整后EPS指引为17.60—17.90美元，并表示仍有望实现全年收入增长10%。这些是管理层展望，并非已实现业绩。 [claim:assessment, claim:latest-quarter, claim:revenue, claim:net-income, claim:eps, claim:adjustment, claim:business-drivers, claim:credit, claim:expenses, claim:tax, claim:outlook]

## Unanswered items and issues

None identified.

## Evidence and source locations

- **metadata_286185e77f063ec7f55fd4c8**（本轮留存） (AXP;  to ): manifest.json
  Locator: documents[0], documents[3], documents[1], documents[2]; snapshot SHA-256: 794232138fd621b28aeebb63d1b7c5b4c6cc5ba59533af7e631ca281132b19a3
- **span_3b74985e92f2fa70a68c88af**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[0:6000] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38

## Reproducible calculations


## Resource use

Model attempts: 5; tool attempts: 2; active seconds: 88.39.
Token usage (when returned by provider): {"planner_steps": 3, "total_tokens": 52766, "total_tokens_reported_calls": 5, "prompt_tokens": 48159, "prompt_tokens_reported_calls": 5, "completion_tokens": 4607, "completion_tokens_reported_calls": 5, "cached_input_tokens": 21805, "cached_input_tokens_reported_calls": 5, "cache_write_tokens": 3376, "cache_write_tokens_reported_calls": 5, "cache_write_30m_tokens": 3376, "cache_write_30m_tokens_reported_calls": 5, "cache_write_12h_tokens": 0, "cache_write_12h_tokens_reported_calls": 5, "reasoning_tokens": 194, "reasoning_tokens_reported_calls": 5}
