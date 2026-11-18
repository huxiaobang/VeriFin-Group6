# VeriFin research report

**Status:** complete
**Stop reason:** completed
**Request:** 那美国以外呢？哪块业务在撑着全球同店增长？
**Model mode:** configured_model
**Scope version:** 8

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
      "id": "request:episode_26c60a00e9584d49892c26b077e4f290",
      "text": "那美国以外呢？哪块业务在撑着全球同店增长？"
    }
  ],
  "period_start": "2026-07-01",
  "period_end": "2026-09-30"
}
```

## Findings

### claim:international_support — Supported

2026年第三季度，全球同店销售同比增长1.5%，美国下降0.2%，而两大国际分部均为正增长。因此，美国以外的业务在支撑全球同店增长。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

11月4日发布的第三季度公告中，截至2026年9月30日季度的同店销售表列示全球增长1.5%、美国下降0.2%、国际运营市场增长1.4%、国际发展许可市场增长4.2%。期间、指标及百分比单位匹配，支持两大国际分部共同支撑全球同店增长的方向性判断，未声称已量化贡献。
span_de69cbdff59e9d164a29cd48

### claim:international_licensed_performance — Supported

国际发展许可市场同店销售同比增长4.2%，是三大分部中增速最高的。公司称，日本表现领先，但中国同店销售下降形成部分抵消；因此不能把这块业务的增长理解为所有国家都在增长。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

季度同店销售表中，国际发展许可市场的4.2%高于国际运营市场的1.4%及美国的负0.2%，确为三大分部最高。对应说明称日本引领、中国同店销售下降部分抵消；草稿保留公司归因，并正确区分地理区域整体正增长与所有国家均增长。
span_de69cbdff59e9d164a29cd48

### claim:international_operated_performance — Supported

国际运营市场同店销售同比增长1.4%，也提供了正向支撑。公司称，多数市场实现正增长，德国和澳大利亚领跑，法国表现形成部分拖累。这里的“国际运营市场”是分部名称，不应理解为其中餐厅全部由公司直营。

Evidence: span_de69cbdff59e9d164a29cd48, chunk_6d40ed804857fe9823b396ea
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

公告直接支持国际运营市场季度同店销售增长1.4%，以及多数市场正增长、德国和澳大利亚引领、法国部分抵消的公司解释。餐厅利润表另在该分部下分别列示加盟餐厅利润及公司自营餐厅利润，支持该分部并非全部直营的澄清。后一个引文是美元计价的餐厅利润表，不是同店销售百分比证据，但其在本项主张中仅用于核验所有权结构，未混用指标或单位。
span_de69cbdff59e9d164a29cd48 chunk_6d40ed804857fe9823b396ea

### claim:international_contribution_limit — Supported

简而言之：国际发展许可市场“跑得最快”，两大国际分部共同支撑增长。但现有公告引文没有提供同店销售权重及贡献拆分，不能仅凭增速判断哪个分部对全球增长的绝对贡献最大，也不能计算各自贡献了多少个百分点。

Evidence: span_de69cbdff59e9d164a29cd48
Calculations: 

公告所列三个分部同店增速支持国际发展许可市场增速最高、两大国际分部共同提供正向支撑。现有公告引文未列出同店销售计算权重或分部贡献百分点，因此不能仅按增速排列绝对贡献，也不能据此计算贡献百分点；草稿明确把这一限制限定于现有引文，没有声称全部披露均不存在相关资料。
span_de69cbdff59e9d164a29cd48

## Answers to your questions

**那美国以外呢？**

answered — 国际运营市场同店销售同比增长1.4%，也提供了正向支撑。公司称，多数市场实现正增长，德国和澳大利亚领跑，法国表现形成部分拖累。这里的“国际运营市场”是分部名称，不应理解为其中餐厅全部由公司直营。 国际发展许可市场同店销售同比增长4.2%，是三大分部中增速最高的。公司称，日本表现领先，但中国同店销售下降形成部分抵消；因此不能把这块业务的增长理解为所有国家都在增长。 [claim:international_operated_performance, claim:international_licensed_performance]

**哪块业务在撑着全球同店增长？**

answered — 简而言之：国际发展许可市场“跑得最快”，两大国际分部共同支撑增长。但现有公告引文没有提供同店销售权重及贡献拆分，不能仅凭增速判断哪个分部对全球增长的绝对贡献最大，也不能计算各自贡献了多少个百分点。 2026年第三季度，全球同店销售同比增长1.5%，美国下降0.2%，而两大国际分部均为正增长。因此，美国以外的业务在支撑全球同店增长。 [claim:international_contribution_limit, claim:international_support]

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

## Reproducible calculations


## Resource use

Model attempts: 3; tool attempts: 0; active seconds: 47.13.
Token usage (when returned by provider): {"planner_steps": 1, "total_tokens": 72524, "total_tokens_reported_calls": 3, "prompt_tokens": 70099, "prompt_tokens_reported_calls": 3, "completion_tokens": 2425, "completion_tokens_reported_calls": 3, "cached_input_tokens": 50530, "cached_input_tokens_reported_calls": 3, "cache_write_tokens": 9692, "cache_write_tokens_reported_calls": 3, "cache_write_30m_tokens": 9692, "cache_write_30m_tokens_reported_calls": 3, "cache_write_12h_tokens": 0, "cache_write_12h_tokens_reported_calls": 3, "reasoning_tokens": 110, "reasoning_tokens_reported_calls": 3}
