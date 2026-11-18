# VeriFin research report

**Status:** complete
**Stop reason:** completed
**Request:** 你刚提到准备金释放。那持卡人的还款情况真的变好了吗，还是只是账上少提了？
**Model mode:** configured_model
**Scope version:** 8

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
      "id": "request:episode_0ca004d161414843826f7eea58bdb18c",
      "text": "核查美国运通2026年第三季度持卡人还款及信用质量是否实际改善：对比逾期、净核销金额和比率等已披露指标，区分实际信用表现与准备金释放对信用损失费用的影响，并说明口径和证据限制。"
    }
  ]
}
```

## Findings

### claim:credit-quality-assessment — Supported

不是纯粹“账上少提了”，但也不能把准备费用大降理解为还款情况同幅改善。美国运通2026年第三季度的逾期率和净核销率同比温和改善；与此同时，卡余额净核销金额仍增加，准备释放则对信用损失费用下降起了重要作用。

Evidence: span_b21f75af80e96caabb6b6935, chunk_0cc4f8ec81ab82245c7c9ab2
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

10-Q表1显示本季净核销率及期末逾期率较上年同期略降；表3同时显示卡余额净核销金额上升、准备由增提转为释放。综合判断保留了指标改善与费用下降之间的区别，没有把准备释放等同于实际还款改善。
span_b21f75af80e96caabb6b6935 chunk_0cc4f8ec81ab82245c7c9ab2

### claim:delinquency-comparison — Supported

看逾期指标，截至2026年9月末，消费及小企业卡余额的30天以上逾期率为1.2%，低于上年同期的1.3%；企业卡余额的90天以上逾账单日比率为0.3%，低于上年同期的0.4%。这支持信用质量同比有所改善，但两类客户使用的逾期定义不同，不能直接横向比较。

Evidence: span_b21f75af80e96caabb6b6935, chunk_5ebad3058c63ee247f781cea
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

表1的2026年及2025年9月末数据分别为：消费及小企业30天以上逾期率1.2%、1.3%，企业90天以上逾账单日比率0.3%、0.4%。脚注说明企业按账单日而非到期日追踪，并有提前启动催收的分类规定；草稿正确提示两类指标定义不同，未作直接横向比较。
span_b21f75af80e96caabb6b6935 chunk_5ebad3058c63ee247f781cea

### claim:writeoff-rate-comparison — Supported

净核销率也略有下降，但要分清口径：消费及小企业卡余额的仅本金净核销率，由2025年第三季度的1.9%降至2026年第三季度的1.8%；卡余额含本金、利息及费用的净核销率，由2.2%降至2.1%。公司因系统限制不提供企业卡余额的仅本金净核销率，因此不能把1.8%说成所有持卡人的统一指标。

Evidence: span_b21f75af80e96caabb6b6935, chunk_5ebad3058c63ee247f781cea
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

表1季度栏列示消费及小企业仅本金净核销率从1.9%降至1.8%，卡余额含本金、利息及费用的净核销率从2.2%降至2.1%。脚注明确企业卡仅本金净核销率因系统限制不可得，因此草稿对1.8%适用范围的限定正确。
span_b21f75af80e96caabb6b6935 chunk_5ebad3058c63ee247f781cea

### claim:card-credit-provision-components — Supported

但实际核销的金额没有下降。卡余额净核销金额由上年同期的11.11亿美元升至本季11.30亿美元；与此同时，卡余额准备由增提1.09亿美元变为释放2.35亿美元，卡余额信用损失费用因此从12.20亿美元降至8.95亿美元。原表显示：费用下降主要反映准备由增提转为释放，净核销金额增加则构成抵消，而不是坏账金额同步下降。

Evidence: chunk_0cc4f8ec81ab82245c7c9ab2
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

表3季度卡余额栏以百万美元列示：净核销由1,111升至1,130，准备由增提109变为释放235，费用由1,220降至895；换算为亿美元均正确。两期分别满足1,111＋109＝1,220及1,130－235＝895。正文也将费用下降主要归因于准备释放相对于上年增提，并注明净核销增加构成抵消。
chunk_0cc4f8ec81ab82245c7c9ab2

### claim:consolidated-credit-provision-scope — Supported

如果看合并口径、包括其他贷款等项目，信用损失费用是由上年同期的12.87亿美元降至本季9.85亿美元；这与上面的卡余额信用损失费用不是同一个范围。

Evidence: chunk_0cc4f8ec81ab82245c7c9ab2
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

表3合并信用损失费用季度总额为2026年985百万美元、2025年1,287百万美元，即9.85亿及12.87亿美元；表中另列其他贷款及其他项目，支持其范围不同于卡余额费用895及1,220百万美元。
chunk_0cc4f8ec81ab82245c7c9ab2

### claim:quarterly-reserve-release-cause — Supported

关键是释放原因：公司在10-Q中称，第三季度卡余额准备释放主要来自定期审查过程中对预期信用损失模型的改进，卡余额环比增加部分抵消了这一影响。不能把公司对前九个月“主要由逾期下降及模型改进驱动”的解释，直接套用到第三季度。

Evidence: chunk_0cc4f8ec81ab82245c7c9ab2
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

原文明确将本季度卡余额准备释放主要归因于定期审查中的预期信用损失模型改进，卡余额环比增加部分抵消；九个月的解释则同时包括逾期下降和模型改进。草稿正确区分季度与累计期间，并将原因归属于公司说明。
chunk_0cc4f8ec81ab82245c7c9ab2

### claim:reserve-interpretation-and-limits — Supported

准备释放属于对预期信用损失估计的调整，并不是当季收回了一笔现金；仅凭释放准备也不能断言公司操纵利润。更稳妥的结论是：信用质量同比温和改善，但本季准备费用下降还显著受模型估计调整影响。现有同比指标不能证明环比持续改善，更不能证明每位持卡人的还款都改善；这些材料也不足以独立验证模型调整是否恰当。

Evidence: chunk_f6625f7bd94d294c623a5532, chunk_0cc4f8ec81ab82245c7c9ab2, span_b21f75af80e96caabb6b6935
Calculations: 

术语表将准备释放定义为信用损失准备的减少，以及净核销超过费用的部分，而非现金回收；表3又明确本季释放主要涉及模型估计改进。同比逾期和核销率支持温和改善，但不能证明连续环比或个体还款改善。所引材料未提供独立验证模型合理性或认定利润操纵所需的证据，草稿对此保持了适当限制。
chunk_f6625f7bd94d294c623a5532 chunk_0cc4f8ec81ab82245c7c9ab2 span_b21f75af80e96caabb6b6935

## Answers to your questions

**你刚提到准备金释放。那持卡人的还款情况真的变好了吗，还是只是账上少提了？**

answered — 不是纯粹“账上少提了”，但也不能把准备费用大降理解为还款情况同幅改善。美国运通2026年第三季度的逾期率和净核销率同比温和改善；与此同时，卡余额净核销金额仍增加，准备释放则对信用损失费用下降起了重要作用。 看逾期指标，截至2026年9月末，消费及小企业卡余额的30天以上逾期率为1.2%，低于上年同期的1.3%；企业卡余额的90天以上逾账单日比率为0.3%，低于上年同期的0.4%。这支持信用质量同比有所改善，但两类客户使用的逾期定义不同，不能直接横向比较。 净核销率也略有下降，但要分清口径：消费及小企业卡余额的仅本金净核销率，由2025年第三季度的1.9%降至2026年第三季度的1.8%；卡余额含本金、利息及费用的净核销率，由2.2%降至2.1%。公司因系统限制不提供企业卡余额的仅本金净核销率，因此不能把1.8%说成所有持卡人的统一指标。 但实际核销的金额没有下降。卡余额净核销金额由上年同期的11.11亿美元升至本季11.30亿美元；与此同时，卡余额准备由增提1.09亿美元变为释放2.35亿美元，卡余额信用损失费用因此从12.20亿美元降至8.95亿美元。原表显示：费用下降主要反映准备由增提转为释放，净核销金额增加则构成抵消，而不是坏账金额同步下降。 如果看合并口径、包括其他贷款等项目，信用损失费用是由上年同期的12.87亿美元降至本季9.85亿美元；这与上面的卡余额信用损失费用不是同一个范围。 关键是释放原因：公司在10-Q中称，第三季度卡余额准备释放主要来自定期审查过程中对预期信用损失模型的改进，卡余额环比增加部分抵消了这一影响。不能把公司对前九个月“主要由逾期下降及模型改进驱动”的解释，直接套用到第三季度。 准备释放属于对预期信用损失估计的调整，并不是当季收回了一笔现金；仅凭释放准备也不能断言公司操纵利润。更稳妥的结论是：信用质量同比温和改善，但本季准备费用下降还显著受模型估计调整影响。现有同比指标不能证明环比持续改善，更不能证明每位持卡人的还款都改善；这些材料也不足以独立验证模型调整是否恰当。 [claim:credit-quality-assessment, claim:delinquency-comparison, claim:writeoff-rate-comparison, claim:card-credit-provision-components, claim:consolidated-credit-provision-scope, claim:quarterly-reserve-release-cause, claim:reserve-interpretation-and-limits]

## Unanswered items and issues

None identified.

## Evidence and source locations

- **metadata_286185e77f063ec7f55fd4c8** (AXP;  to ): manifest.json
  Locator: documents[0], documents[3], documents[1], documents[2]; snapshot SHA-256: 794232138fd621b28aeebb63d1b7c5b4c6cc5ba59533af7e631ca281132b19a3
- **span_3b74985e92f2fa70a68c88af** (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[0:6000] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38
- **span_4657ec4247a3a904927c380a** (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[5700:11700] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38
- **chunk_09bf3a35fd22eaf7ac9789ae** (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[5808:7537] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38
- **chunk_5a255fbc933ca3e7241888f6** (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[14373:16172] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38
- **chunk_75110ca1960387a6d1147821** (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000405/q326exhibit991.htm>)
  Locator: snapshots/q326exhibit991.htm.txt:characters[15922:16423] (zero-based Unicode offsets); snapshot SHA-256: 19053fbfc0e231460d39f697ac44c76e16acf71d71108d8dfba0380f47bbef38
- **chunk_0cc4f8ec81ab82245c7c9ab2**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000409/axp-20260930.htm>)
  Locator: snapshots/axp-20260930.htm.txt:characters[23446:25214] (zero-based Unicode offsets); snapshot SHA-256: 5b92b6270a3af3b993c334bc65d30bfd0484164c98075803d2198bed6853211a
- **chunk_0d5b2222830bc9f85af37d2f**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000409/axp-20260930.htm>)
  Locator: snapshots/axp-20260930.htm.txt:characters[15880:17646] (zero-based Unicode offsets); snapshot SHA-256: 5b92b6270a3af3b993c334bc65d30bfd0484164c98075803d2198bed6853211a
- **chunk_f6625f7bd94d294c623a5532**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000409/axp-20260930.htm>)
  Locator: snapshots/axp-20260930.htm.txt:characters[110755:112506] (zero-based Unicode offsets); snapshot SHA-256: 5b92b6270a3af3b993c334bc65d30bfd0484164c98075803d2198bed6853211a
- **chunk_5ebad3058c63ee247f781cea**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000409/axp-20260930.htm>)
  Locator: snapshots/axp-20260930.htm.txt:characters[13078:14822] (zero-based Unicode offsets); snapshot SHA-256: 5b92b6270a3af3b993c334bc65d30bfd0484164c98075803d2198bed6853211a
- **chunk_8ffbe7ea37b6eb0858848bef**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000409/axp-20260930.htm>)
  Locator: snapshots/axp-20260930.htm.txt:characters[43448:45160] (zero-based Unicode offsets); snapshot SHA-256: 5b92b6270a3af3b993c334bc65d30bfd0484164c98075803d2198bed6853211a
- **chunk_672726df128e0888e2db0ac2**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000409/axp-20260930.htm>)
  Locator: snapshots/axp-20260930.htm.txt:characters[41897:43696] (zero-based Unicode offsets); snapshot SHA-256: 5b92b6270a3af3b993c334bc65d30bfd0484164c98075803d2198bed6853211a
- **span_b21f75af80e96caabb6b6935**（本轮留存） (AXP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/4962/000000496226000409/axp-20260930.htm>)
  Locator: snapshots/axp-20260930.htm.txt:characters[7000:13000] (zero-based Unicode offsets); snapshot SHA-256: 5b92b6270a3af3b993c334bc65d30bfd0484164c98075803d2198bed6853211a

## Reproducible calculations


## Resource use

Model attempts: 5; tool attempts: 2; active seconds: 77.52.
Token usage (when returned by provider): {"planner_steps": 3, "total_tokens": 132801, "total_tokens_reported_calls": 5, "prompt_tokens": 128401, "prompt_tokens_reported_calls": 5, "completion_tokens": 4400, "completion_tokens_reported_calls": 5, "cached_input_tokens": 75756, "cached_input_tokens_reported_calls": 5, "cache_write_tokens": 29068, "cache_write_tokens_reported_calls": 5, "cache_write_30m_tokens": 29068, "cache_write_30m_tokens_reported_calls": 5, "cache_write_12h_tokens": 0, "cache_write_12h_tokens_reported_calls": 5, "reasoning_tokens": 39, "reasoning_tokens_reported_calls": 5}
