# VeriFin research report

**Status:** complete
**Stop reason:** completed
**Request:** 你说国际业务较强、北美还在修复。北美具体是哪块拖了后腿，卖得少了还是价格变了？
**Model mode:** configured_model
**Scope version:** 5

Model verification is an assessment against the available evidence, not an independent guarantee.

## Scope

```json
{
  "cutoff": "2026-11-16",
  "requirements": [
    {
      "id": "request:episode_66d039dd17404a7ab46b26c725992e32",
      "text": "说明百事2026年第三季度北美食品与北美饮料分别在哪些方面拖累表现，依据同季度披露区分销量、有效净定价、有机收入与收购贡献，并明确量价口径及证据限制。"
    }
  ],
  "entities": [
    "PEP"
  ],
  "period_start": "2026-06-14",
  "period_end": "2026-09-05"
}
```

## Findings

Research skills: reconcile-comparable-growth

### claim:pfna-volume-pricing — Supported

北美食品（PFNA）主要是有效净定价偏弱，不是分部整体销量下降。百事披露，2026年第三季度（截至9月5日的12周）有机收入基本持平；在有机收入增长桥中，有机销量贡献约+0.5个百分点，有效净定价贡献约-1个百分点。管理层同时称，北美方便食品收入趋势环比改善，咸味零食销量和销量份额改善，但较低的有效净定价形成抵消。桥中数字经过四舍五入，不能要求其机械加总为零。

Evidence: chunk_ae5bdbdc960fbb64f4203c72, span_e15075b0f56ab75de1fba9f1, span_8c9a2825bb1288d3bfc9eebe
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

截至2026年9月5日的12周有机收入表中，PFNA有机收入增速为横线，有机销量对增速的影响为+0.5、有效净定价为-1；季度汇总表另列分部单位销量基本持平，支持并非分部整体销量下降的判断。季度讨论明确将北美方便食品趋势环比改善归于咸味零食销量及份额改善，并指出较低有效净定价形成抵消。脚注注明四舍五入，不能要求显示值精确闭合。这里的量价数字正确表述为收入增速贡献，而非单位销量或单品价格变化率。
chunk_ae5bdbdc960fbb64f4203c72 span_e15075b0f56ab75de1fba9f1 span_866e7defb63fa38a7cf48e44 span_8c9a2825bb1288d3bfc9eebe

### claim:pfna-profit-pressure — Supported

从盈利看，北美食品也是明显的薄弱点：同期核心营业利润同比下降12%。因此，销量有所改善并不意味着盈利已经修复；但这项利润降幅不能仅凭收入量价桥全部归因于定价。

Evidence: span_8c9a2825bb1288d3bfc9eebe
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

12周分部表的Core Operating Profit % Change行明确列示PFNA为-12%，属于核心营业利润同比变化，并非误用报告利润或年初至今口径。销量改善不代表利润恢复是有据的比较；同时保留不能用收入量价桥解释全部利润降幅的限制，没有把定价直接认定为完整利润原因。
span_8c9a2825bb1288d3bfc9eebe

### claim:pbna-volume-pricing — Supported

北美饮料（PBNA）则主要是量弱、定价抵消：同季度有机收入基本持平，其中有机销量对收入增速的贡献约为-3个百分点，有效净定价贡献约为+3个百分点。也就是说，内生收入没有增长，并不是有效净定价下滑，而是正向定价贡献被负向销量贡献抵消。

Evidence: chunk_ae5bdbdc960fbb64f4203c72
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

同一12周有机收入增长桥中，PBNA有机收入增速为横线，有机销量贡献为-3、有效净定价贡献为+3。草稿据此解释为负向销量贡献抵消正向定价贡献，方向、分部和期间均匹配，也没有将-3个百分点误称为单位销量下降3%。
chunk_ae5bdbdc960fbb64f4203c72

### claim:pbna-acquisition-bridge — Supported

北美饮料报告收入同比增长5%，管理层称主要来自2025年收购的贡献，不能把它理解成原有业务销量转强。收入桥列示的汇率调整约为零，“收购及剥离影响”为-6个百分点；这里的负号表示从报告增速调整至有机增速时剔除该影响，并非收购拖累收入。表内增速和调整项经过四舍五入，不应把显示的5%与-6个百分点机械相加，取代公司披露的有机收入基本持平。

Evidence: span_e15075b0f56ab75de1fba9f1, chunk_ae5bdbdc960fbb64f4203c72, span_8c9a2825bb1288d3bfc9eebe
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度讨论列示北美饮料报告收入增长5%，并明确主要反映2025年收购的贡献。有机收入表的PBNA列将汇率影响显示为横线、收购及剥离调整显示为-6，且列出从报告收入中扣除收购相关收入及调整上年剥离影响的金额，支持负号属于剔除调整而非收购拖累的解释。脚注注明四舍五入；草稿采用公司披露的有机收入基本持平，没有自行构造闭合结果。
span_e15075b0f56ab75de1fba9f1 chunk_ae5bdbdc960fbb64f4203c72 span_8c9a2825bb1288d3bfc9eebe

### claim:volume-definition-limits — Supported

量价口径要分清：北美饮料另行披露的单位销量同比下降2%，已剔除收购及剥离影响；它不等于有机收入桥中的销量贡献-3个百分点。公司脚注说明，单位销量变化与销量对收入增速的影响，可能因产品组合、非合并合营企业销量等因素而不同；脚注还对特许饮料业务列举了装瓶商销量与浓缩液发货的时点差异，但没有把北美饮料这两个数字的差别逐项量化。有效净定价也不能直接当成某个单品的标价变化，因此不能据这些汇总数字断言全面降价、终端需求变化的具体原因，或某个品牌拖累。

Evidence: span_866e7defb63fa38a7cf48e44, chunk_ae5bdbdc960fbb64f4203c72, span_8c9a2825bb1288d3bfc9eebe
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度汇总表列示PBNA饮料销量下降2%，脚注明确剔除收购及剥离影响；有机收入桥另列销量贡献-3个百分点。两处脚注明确两种口径可能因产品组合、非合并合营企业销量及特许饮料业务的发货时点差异而不同。草稿未将特许业务时点因素直接归因为PBNA差异，也未宣称已有逐项量化解释。分部汇总有效净定价并不提供具体单品标价、品牌或终端需求原因，保留这些推断限制合理。
span_866e7defb63fa38a7cf48e44 chunk_ae5bdbdc960fbb64f4203c72 span_8c9a2825bb1288d3bfc9eebe

## Answers to your questions

**你说国际业务较强、北美还在修复。北美具体是哪块拖了后腿，卖得少了还是价格变了？**

answered — 北美食品（PFNA）主要是有效净定价偏弱，不是分部整体销量下降。百事披露，2026年第三季度（截至9月5日的12周）有机收入基本持平；在有机收入增长桥中，有机销量贡献约+0.5个百分点，有效净定价贡献约-1个百分点。管理层同时称，北美方便食品收入趋势环比改善，咸味零食销量和销量份额改善，但较低的有效净定价形成抵消。桥中数字经过四舍五入，不能要求其机械加总为零。 从盈利看，北美食品也是明显的薄弱点：同期核心营业利润同比下降12%。因此，销量有所改善并不意味着盈利已经修复；但这项利润降幅不能仅凭收入量价桥全部归因于定价。 北美饮料（PBNA）则主要是量弱、定价抵消：同季度有机收入基本持平，其中有机销量对收入增速的贡献约为-3个百分点，有效净定价贡献约为+3个百分点。也就是说，内生收入没有增长，并不是有效净定价下滑，而是正向定价贡献被负向销量贡献抵消。 北美饮料报告收入同比增长5%，管理层称主要来自2025年收购的贡献，不能把它理解成原有业务销量转强。收入桥列示的汇率调整约为零，“收购及剥离影响”为-6个百分点；这里的负号表示从报告增速调整至有机增速时剔除该影响，并非收购拖累收入。表内增速和调整项经过四舍五入，不应把显示的5%与-6个百分点机械相加，取代公司披露的有机收入基本持平。 量价口径要分清：北美饮料另行披露的单位销量同比下降2%，已剔除收购及剥离影响；它不等于有机收入桥中的销量贡献-3个百分点。公司脚注说明，单位销量变化与销量对收入增速的影响，可能因产品组合、非合并合营企业销量等因素而不同；脚注还对特许饮料业务列举了装瓶商销量与浓缩液发货的时点差异，但没有把北美饮料这两个数字的差别逐项量化。有效净定价也不能直接当成某个单品的标价变化，因此不能据这些汇总数字断言全面降价、终端需求变化的具体原因，或某个品牌拖累。 [claim:pfna-volume-pricing, claim:pfna-profit-pressure, claim:pbna-volume-pricing, claim:pbna-acquisition-bridge, claim:volume-definition-limits]

## Unanswered items and issues

None identified.

## Evidence and source locations

- **metadata_ad760c6692f956a4d6a61c1a** (PEP;  to ): manifest.json
  Locator: documents[3], documents[0], documents[1], documents[2]; snapshot SHA-256: dbf78f320aca77b3e4dfaa055e02f216fab9a11fcfa79426fb195da46f9d64f5
- **span_e15075b0f56ab75de1fba9f1** (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[0:6000] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **span_866e7defb63fa38a7cf48e44** (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[6000:12000] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_2daf7bb6c7a896a72ec40dc6**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[5355:7149] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_b0832db85ae6eb61c6f2a5a8**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[2476:4225] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_95d83fe58fc0d574467eed76**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[0:1315] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_ae5bdbdc960fbb64f4203c72**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[27029:28825] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_f33322f4a274b72c73b45362**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[3978:5604] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **span_8c9a2825bb1288d3bfc9eebe**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[28825:33825] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899

## Reproducible calculations


## Resource use

Model attempts: 8; tool attempts: 2; active seconds: 138.48.
Token usage (when returned by provider): {"planner_steps": 4, "total_tokens": 194687, "total_tokens_reported_calls": 8, "prompt_tokens": 186554, "prompt_tokens_reported_calls": 8, "completion_tokens": 8133, "completion_tokens_reported_calls": 8, "cached_input_tokens": 115067, "cached_input_tokens_reported_calls": 8, "cache_write_tokens": 23775, "cache_write_tokens_reported_calls": 8, "cache_write_30m_tokens": 23775, "cache_write_30m_tokens_reported_calls": 8, "cache_write_12h_tokens": 0, "cache_write_12h_tokens_reported_calls": 8, "reasoning_tokens": 246, "reasoning_tokens_reported_calls": 8}
