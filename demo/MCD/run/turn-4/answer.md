# VeriFin research report

**Status:** complete
**Stop reason:** completed
**Request:** 你说国际运营市场不等于全是直营。那加盟店卖得好，麦当劳自己到底赚的是哪部分钱？
**Model mode:** configured_model
**Scope version:** 10

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
      "id": "request:episode_096de31c3a8646cfaee04efee9b63502",
      "text": "解释加盟店销售如何转化为麦当劳自身的租金、特许权使用费等收入，区分传统加盟与发展许可或附属公司模式、加盟店销售额与公司确认收入，并结合已有季度披露说明加盟餐厅利润口径及其不等于集团净利润。"
    }
  ],
  "period_start": "2026-07-01",
  "period_end": "2026-09-30"
}
```

## Findings

### claim:franchise_revenue_not_store_sales — Supported

简单说：加盟店卖得好，麦当劳主要赚的是合同约定的租金、特许权使用费等，而不是把加盟店全部营业额记成自己的收入。相关收费主要与销售额挂钩，也不是按加盟商净利润分成。因此，加盟店销售改善通常有利于麦当劳相关收入，但不能把门店多卖的钱全部算成麦当劳多赚的钱。

Evidence: chunk_149bb9c87a7ef7432668eebb, chunk_f20595b2abb202e49b610e85
Calculations: 

10-Q收入说明将加盟收入界定为租金、按销售额计算的特许权使用费及初始费用，而非加盟店全部销售额；传统加盟段也明确收费以销售额而非加盟商净利润为基础。销售改善通常有利于相关收入是合同机制的方向性解释，草稿未将其表述为固定比例的利润增长。
chunk_149bb9c87a7ef7432668eebb chunk_f20595b2abb202e49b610e85

### claim:conventional_franchise_economics — Supported

传统加盟模式下，麦当劳通常拥有餐厅土地和建筑，或取得长期租赁权；加盟商承担设备、招牌、座椅及装修等投入。麦当劳主要收取按销售额计算的租金和特许权使用费，其中租金设有最低支付额，并在新店开业或授予新加盟权时收取初始费用。具体费用因场地、公司投入及当地经营条件而异，不能套用一个统一收费比例。

Evidence: chunk_99d17792d84508e31fc75b83, chunk_f20595b2abb202e49b610e85
Calculations: 

传统加盟段明确公司通常拥有或长期租赁土地及建筑，加盟商支付设备、招牌、座椅和装修投入；后续原文列明按销售额收取租金及特许权使用费、最低租金和初始费用。费用因场地、公司投入及当地条件而异，支持不能套用统一收费比例的提醒。
chunk_99d17792d84508e31fc75b83 chunk_f20595b2abb202e49b610e85

### claim:developmental_license_economics — Supported

发展许可或附属公司模式下，经营方负责经营管理、提供资本（包括房地产权益），并开发和开设新餐厅；麦当劳通常不投入餐厅资本，主要收取按销售额计算的特许权使用费，通常还会在新店开业或授予新许可时收取初始费用。与传统加盟相比，这一模式的餐厅资本责任更多由经营方承担。

Evidence: chunk_f20595b2abb202e49b610e85
Calculations: 

Developmental License or Affiliate标题下原文明确经营方负责管理、资本投入（包括房地产权益）及新店开发，公司通常不投入餐厅资本，收取按销售额计算的特许权使用费及通常的初始费用。草稿保留了“通常”的限定，并正确区分两类模式的资本责任。
chunk_f20595b2abb202e49b610e85

### claim:quarterly_franchised_revenue — Supported

落到2026年第三季度，公司收入表直接披露的加盟收入为44.87亿美元。这是麦当劳确认的加盟收入，不是加盟餐厅向顾客销售食品的全部营业额。

Evidence: chunk_6dfa0efaee68fd8342f1524b, chunk_149bb9c87a7ef7432668eebb
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

收入表的Quarters Ended September 30、2026列下，Franchised revenues合计为4,487百万美元，换算为44.87亿美元正确，未误用上年或累计期间。收入定义说明该金额为公司收取的加盟费用，而非加盟餐厅全部营业额；这里只进行了单位换算。
chunk_6dfa0efaee68fd8342f1524b chunk_149bb9c87a7ef7432668eebb

### claim:franchised_restaurant_margin_definition — Supported

这些加盟收入还不是利润。公司将“加盟餐厅利润”（Franchised restaurant margins）定义为加盟收入减去加盟餐厅物业占用成本；这些成本包括租赁费用和折旧，因为麦当劳通常拥有或长期租赁相关土地及建筑。

Evidence: chunk_6d40ed804857fe9823b396ea
Calculations: 

Restaurant Margins原文明确将加盟餐厅利润定义为加盟收入减加盟餐厅物业占用成本，并说明成本包括租赁费用和折旧，原因是公司通常拥有或长期租赁餐厅土地及建筑。草稿准确区分收入与该层面的利润。
chunk_6d40ed804857fe9823b396ea

### claim:quarterly_franchised_margin_not_net_income — Supported

2026年第三季度，公司餐厅利润表直接披露的加盟餐厅利润为38.00亿美元。它只是扣除上述物业占用成本后的餐厅层面指标，并不等于再扣除集团管理费用、利息、税项等之后的集团净利润。

Evidence: chunk_6d40ed804857fe9823b396ea, span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度餐厅利润表的2026年Franchised合计为3,800百万美元，即38.00亿美元；同段定义显示它仅扣除加盟物业占用成本，并非集团净利润。公告另列本季集团净利润2,486百万美元，也印证两者指标不同。草稿是在解释尚未涵盖集团管理费用、利息及税项等的口径差异，并未声称从加盟餐厅利润仅靠这些扣项即可完整调节至集团净利润。
chunk_6d40ed804857fe9823b396ea span_de69cbdff59e9d164a29cd48

### claim:international_operated_mixed_ownership — Supported

所以，“国际运营市场”说的是业务分部，而不是全部直营的所有权模式：2026年第三季度的餐厅利润表，在该分部下同时列有加盟餐厅利润和公司自营餐厅利润。看这个分部的表现时，需要区分麦当劳从加盟店收取的费用，与自营店销售扣除餐厅经营成本后留下的利润。

Evidence: chunk_6d40ed804857fe9823b396ea
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

2026年第三季度餐厅利润表在International Operated Markets下分别列有加盟餐厅利润1,645百万美元及自营餐厅利润299百万美元，支持该分部并非全部直营。表前定义还区分加盟费用扣物业成本与自营销售扣餐厅经营成本的口径，支持草稿的经营模式解释。
chunk_6d40ed804857fe9823b396ea

## Answers to your questions

**你说国际运营市场不等于全是直营。那加盟店卖得好，麦当劳自己到底赚的是哪部分钱？**

answered — 简单说：加盟店卖得好，麦当劳主要赚的是合同约定的租金、特许权使用费等，而不是把加盟店全部营业额记成自己的收入。相关收费主要与销售额挂钩，也不是按加盟商净利润分成。因此，加盟店销售改善通常有利于麦当劳相关收入，但不能把门店多卖的钱全部算成麦当劳多赚的钱。 传统加盟模式下，麦当劳通常拥有餐厅土地和建筑，或取得长期租赁权；加盟商承担设备、招牌、座椅及装修等投入。麦当劳主要收取按销售额计算的租金和特许权使用费，其中租金设有最低支付额，并在新店开业或授予新加盟权时收取初始费用。具体费用因场地、公司投入及当地经营条件而异，不能套用一个统一收费比例。 发展许可或附属公司模式下，经营方负责经营管理、提供资本（包括房地产权益），并开发和开设新餐厅；麦当劳通常不投入餐厅资本，主要收取按销售额计算的特许权使用费，通常还会在新店开业或授予新许可时收取初始费用。与传统加盟相比，这一模式的餐厅资本责任更多由经营方承担。 落到2026年第三季度，公司收入表直接披露的加盟收入为44.87亿美元。这是麦当劳确认的加盟收入，不是加盟餐厅向顾客销售食品的全部营业额。 这些加盟收入还不是利润。公司将“加盟餐厅利润”（Franchised restaurant margins）定义为加盟收入减去加盟餐厅物业占用成本；这些成本包括租赁费用和折旧，因为麦当劳通常拥有或长期租赁相关土地及建筑。 2026年第三季度，公司餐厅利润表直接披露的加盟餐厅利润为38.00亿美元。它只是扣除上述物业占用成本后的餐厅层面指标，并不等于再扣除集团管理费用、利息、税项等之后的集团净利润。 所以，“国际运营市场”说的是业务分部，而不是全部直营的所有权模式：2026年第三季度的餐厅利润表，在该分部下同时列有加盟餐厅利润和公司自营餐厅利润。看这个分部的表现时，需要区分麦当劳从加盟店收取的费用，与自营店销售扣除餐厅经营成本后留下的利润。 [claim:franchise_revenue_not_store_sales, claim:conventional_franchise_economics, claim:developmental_license_economics, claim:quarterly_franchised_revenue, claim:franchised_restaurant_margin_definition, claim:quarterly_franchised_margin_not_net_income, claim:international_operated_mixed_ownership]

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
- **chunk_f20595b2abb202e49b610e85**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[41138:42932] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_ca1efc0490579de16a0391e0**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[99643:101194] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_99d17792d84508e31fc75b83**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[39724:41382] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_149bb9c87a7ef7432668eebb**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[60887:62439] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b
- **chunk_6dfa0efaee68fd8342f1524b**（本轮留存） (MCD;  to ): [source](<https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm>)
  Locator: snapshots/mcd-20260930.htm.txt:characters[62191:63657] (zero-based Unicode offsets); snapshot SHA-256: c11db334ef38593bda60e8677d7136c71e98e04b7ea2424422949ac79e4ec25b

## Reproducible calculations


## Resource use

Model attempts: 4; tool attempts: 1; active seconds: 73.23.
Token usage (when returned by provider): {"planner_steps": 2, "total_tokens": 122840, "total_tokens_reported_calls": 4, "prompt_tokens": 118923, "prompt_tokens_reported_calls": 4, "completion_tokens": 3917, "completion_tokens_reported_calls": 4, "cached_input_tokens": 72846, "cached_input_tokens_reported_calls": 4, "cache_write_tokens": 27373, "cache_write_tokens_reported_calls": 4, "cache_write_30m_tokens": 27373, "cache_write_30m_tokens_reported_calls": 4, "cache_write_12h_tokens": 0, "cache_write_12h_tokens_reported_calls": 4, "reasoning_tokens": 138, "reasoning_tokens_reported_calls": 4}
