# VeriFin research report

**Status:** complete
**Stop reason:** completed
**Request:** 你说每股盈利涨了 31%，调整后却只涨 13%。这两个数为什么差这么远？
**Model mode:** configured_model
**Scope version:** 6

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
      "id": "request:episode_1bc8ee25e95f42c8b8787968a2868c04",
      "text": "解释美国运通2026年第三季度报告EPS同比增长31%、调整后EPS增长13%的差异，核对两期比较基数及公司非GAAP调整项目，区分出售收益、相关重组成本与其他利润影响因素。"
    }
  ]
}
```

## Findings

### claim:eps-comparison-basis — Supported

美国运通2026年第三季度报告摊薄EPS为5.42美元，同比增长31%；公司非GAAP调整后EPS为4.68美元，同比增长13%。2025年第三季度两种口径均为4.14美元，因此增速差异不是比较基数不同，而是本季是否剔除特定项目。

Evidence: chunk_5a255fbc933ca3e7241888f6
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

附录I截至9月30日的季度栏明确列示：2026年GAAP摊薄EPS为5.42美元、同比增长31%，调整后为4.68美元、同比增长13%；2025年两种口径均为4.14美元。因此比较基数相同，差异来自本期调整项目。草稿使用的是季度数据，而非相邻的九个月累计数据，期间、美元每股单位及公司整体口径一致。
chunk_5a255fbc933ca3e7241888f6

### claim:eps-adjustment-bridge — Supported

差距主要来自出售GBTG股权带来的收益，但还要同时处理相关重组费用。公司附录列示的税后每股调节关系为：报告EPS 5.42美元－GBTG出售收益1.11美元＋重组费用0.37美元＝调整后EPS 4.68美元。也就是说，调整时既剔除出售收益，也加回重组费用，不能把出售收益全额当作两种EPS的差额；上述为公司披露的每股调节数。

Evidence: chunk_5a255fbc933ca3e7241888f6
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

附录I季度EPS调节表将GBTG出售收益的税后调整列为负1.11美元，将重组费用的税后调整列为正0.37美元，最终调整后EPS为4.68美元。草稿所列5.42－1.11＋0.37＝4.68与原表及算术一致，正确区分剔除收益与加回费用，没有将全部出售收益误作两种EPS的净差额。
chunk_5a255fbc933ca3e7241888f6

### claim:adjusted-growth-interpretation — Supported

13%也不等于“完全剔除所有非持续因素后的经营增速”。本次公司非GAAP调节仅针对GBTG出售收益及相关重组费用；较低有效税率、准备金释放和平均摊薄股数下降并未作为单独项目剔除。公告另称，本季税率下降主要反映解决以前期间税务事项带来的离散税收利益，信用损失准备费用下降则反映本季释放准备金、上年同期增提准备金等因素。因此，13%应理解为公司特定调整口径下的EPS增速，而不是纯粹的基础经营增长率。

Evidence: chunk_5a255fbc933ca3e7241888f6, span_3b74985e92f2fa70a68c88af
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

附录I的非GAAP调节仅列GBTG出售收益、重组费用及各自税项，未另行剔除有效税率变化、准备金释放或股数变化。公告季度表显示平均摊薄股数由693百万股降至674百万股；正文将有效税率下降主要归于解决以前期间税务事项产生的离散税收利益，并说明信用损失准备费用下降反映本期释放准备金、上年同期增提准备金，且较高净核销构成部分抵消。草稿以“等因素”保留解释的非穷尽性，未虚构贡献金额；据此提示13%并非完全排除所有非持续因素后的基础经营增速，边界恰当。
chunk_5a255fbc933ca3e7241888f6 span_3b74985e92f2fa70a68c88af

## Answers to your questions

**你说每股盈利涨了 31%，调整后却只涨 13%。这两个数为什么差这么远？**

answered — 差距主要来自出售GBTG股权带来的收益，但还要同时处理相关重组费用。公司附录列示的税后每股调节关系为：报告EPS 5.42美元－GBTG出售收益1.11美元＋重组费用0.37美元＝调整后EPS 4.68美元。也就是说，调整时既剔除出售收益，也加回重组费用，不能把出售收益全额当作两种EPS的差额；上述为公司披露的每股调节数。 美国运通2026年第三季度报告摊薄EPS为5.42美元，同比增长31%；公司非GAAP调整后EPS为4.68美元，同比增长13%。2025年第三季度两种口径均为4.14美元，因此增速差异不是比较基数不同，而是本季是否剔除特定项目。 13%也不等于“完全剔除所有非持续因素后的经营增速”。本次公司非GAAP调节仅针对GBTG出售收益及相关重组费用；较低有效税率、准备金释放和平均摊薄股数下降并未作为单独项目剔除。公告另称，本季税率下降主要反映解决以前期间税务事项带来的离散税收利益，信用损失准备费用下降则反映本季释放准备金、上年同期增提准备金等因素。因此，13%应理解为公司特定调整口径下的EPS增速，而不是纯粹的基础经营增长率。 [claim:eps-adjustment-bridge, claim:eps-comparison-basis, claim:adjusted-growth-interpretation]

## Unanswered items and issues

None identified.

## Evidence and source locations

- **metadata_286185e77f063ec7f55fd4c8** (AXP;  to ): manifest.json
  Locator: documents[0], documents[3], documents[1], documents[2]; snapshot SHA-256: 794232138fd621b28aeebb63d1b7c5b4c6cc5ba59533af7e631ca281132b19a3
- **span_3b74985e92f2fa70a68c88af** (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[0:6000] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38
- **span_4657ec4247a3a904927c380a**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[5700:11700] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38
- **chunk_09bf3a35fd22eaf7ac9789ae**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[5808:7537] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38
- **chunk_5a255fbc933ca3e7241888f6**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[14373:16172] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38
- **chunk_75110ca1960387a6d1147821**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[15922:16423] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38

## Reproducible calculations


## Resource use

Model attempts: 5; tool attempts: 2; active seconds: 71.33.
Token usage (when returned by provider): {"planner_steps": 3, "total_tokens": 89612, "total_tokens_reported_calls": 5, "prompt_tokens": 86583, "prompt_tokens_reported_calls": 5, "completion_tokens": 3029, "completion_tokens_reported_calls": 5, "cached_input_tokens": 46478, "cached_input_tokens_reported_calls": 5, "cache_write_tokens": 19063, "cache_write_tokens_reported_calls": 5, "cache_write_30m_tokens": 19063, "cache_write_30m_tokens_reported_calls": 5, "cache_write_12h_tokens": 0, "cache_write_12h_tokens_reported_calls": 5, "reasoning_tokens": 276, "reasoning_tokens_reported_calls": 5}
