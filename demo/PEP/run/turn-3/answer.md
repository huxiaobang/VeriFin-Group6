# VeriFin research report

**Status:** complete
**Stop reason:** completed
**Request:** 那你前面说的全年盈利指引下调，具体从多少改到多少？公司把原因也说清了吗？
**Model mode:** configured_model
**Scope version:** 8

Model verification is an assessment against the available evidence, not an independent guarantee.

## Scope

```json
{
  "cutoff": "2026-11-16",
  "requirements": [
    {
      "id": "request:episode_1448faa1340c4cd5ab29ba150d164b2f",
      "text": "核实百事2026财年盈利增长指引此前与修订后的数值及口径，并说明公司对下调原因的明确解释、量化程度和证据限制。"
    }
  ],
  "entities": [
    "PEP"
  ]
}
```

## Findings

### claim:guidance-constant-currency-eps — Supported

百事2026财年核心固定汇率每股收益（EPS）同比增长指引，由此前“4%—6%区间的低端”下调至“1%—2%”。这是非GAAP盈利增长率指引，不是每股收益的美元金额；此前也不是仍预期整个4%—6%区间。

Evidence: span_866e7defb63fa38a7cf48e44
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

2026财年指引表的Core Constant Currency EPS行，当前列为+1%至+2%，此前列为+4%至+6%区间低端。表前明确指引采用非GAAP口径，表中数值是增长率而非EPS美元金额；草稿正确保留此前“低端”的限定。
span_866e7defb63fa38a7cf48e44

### claim:guidance-core-eps — Supported

包含汇率折算影响的核心EPS同比增长指引，则由此前“5%—7%区间的低端”下调至“2.5%—3.5%”。这同样是非GAAP全年增长指引，而非GAAP每股收益预测。

Evidence: span_866e7defb63fa38a7cf48e44
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

指引表Core EPS行列示当前+2.5%至+3.5%，此前为+5%至+7%区间低端。汇率行另列对核心EPS增长的贡献，且指引定义明确核心固定汇率EPS额外剔除汇率折算影响，支持核心EPS包含该影响的口径区分。
span_866e7defb63fa38a7cf48e44 chunk_35a48ca15b32e7021c45b95a

### claim:management-investment-cost-context — Supported

公司明确谈到了经营压力与应对方向：管理层表示，要通过更多创新、有效品牌建设及更精准的渠道执行来改善北美表现，并将识别和实施进一步结构性降本，以支持这些投入、缓解投入品成本通胀上升的影响。这些是公司披露的经营背景和计划，不能直接当成其对全年盈利指引下调幅度的完整归因。

Evidence: span_e15075b0f56ab75de1fba9f1
Calculations: 

首席执行官原话明确涉及增加创新、品牌建设和渠道执行投入以改善北美表现，以及识别和实施结构性降本，为投入提供资金并缓解投入品成本通胀。原话未将这些因素逐项连接至全年EPS指引下调幅度，草稿保留了经营背景与完整下调归因的区别。
span_e15075b0f56ab75de1fba9f1

### claim:guidance-fx-assumption — Supported

同期，汇率对全年核心EPS增长的预期贡献反而由约1个百分点提高至约1.5个百分点。按指引表的方向判断，这是更有利的假设，而不是盈利增长指引下调的表面原因；它也不解释核心固定汇率EPS指引为何下调。

Evidence: span_866e7defb63fa38a7cf48e44
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

指引表Foreign Exchange Translation行将对核心EPS增长的预计贡献由约+1%提高至约+1.5%，方向更有利。核心固定汇率EPS指引定义明确剔除汇率折算，支持该变化不能解释其下调的判断；草稿未把方向判断冒充管理层量化归因。
span_866e7defb63fa38a7cf48e44 chunk_35a48ca15b32e7021c45b95a

### claim:guidance-tax-assumption — Supported

全年核心有效税率预期也由约22%降至约21%。在其他条件不变时，较低税率通常有利于税后盈利，因此这项假设变化本身也不是盈利增长指引下调的解释；公告没有据此量化其对EPS的抵消作用。

Evidence: span_866e7defb63fa38a7cf48e44
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

Core Annual Effective Tax Rate行明确由此前约22%降至当前约21%。草稿将较低税率有利于税后盈利限定为其他条件不变的分析判断；公告指引及讨论未提供这项变化对EPS的量化抵消金额，未作超出证据的推算。
span_866e7defb63fa38a7cf48e44

### claim:guidance-explanation-limit — Supported

所以，数字改了多少是清楚的，但现有证据中的原因解释不完整、也未量化：已取得的公告及10-Q段落没有把北美投入、成本通胀、降本及其他因素逐项连接成全年盈利指引下调桥。公告预告还会发布管理层准备稿并举行问答，但本次没有取得这些材料；因此不能进一步断言，公司在所有沟通中都没有解释下调原因。

Evidence: span_e15075b0f56ab75de1fba9f1, span_866e7defb63fa38a7cf48e44, chunk_d71dd73a1a5ed77942290805
Calculations: 

核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。

公告提供指引前后对照和经营背景，但未提供从此前到修订后全年盈利增长指引的逐项归因桥；所引10-Q段落为已实现EPS调节及商品衍生品说明，也非指引变动桥。公告明确预告另行发布管理层准备稿并举行问答，有限目录及本次取得记录未包含这些材料。草稿将否定结论限定于已取得段落，并未宣称公司所有沟通均无解释，证据边界恰当。
span_e15075b0f56ab75de1fba9f1 span_866e7defb63fa38a7cf48e44 chunk_d71dd73a1a5ed77942290805 metadata_ad760c6692f956a4d6a61c1a

## Answers to your questions

**那你前面说的全年盈利指引下调，具体从多少改到多少？**

answered — 百事2026财年核心固定汇率每股收益（EPS）同比增长指引，由此前“4%—6%区间的低端”下调至“1%—2%”。这是非GAAP盈利增长率指引，不是每股收益的美元金额；此前也不是仍预期整个4%—6%区间。 包含汇率折算影响的核心EPS同比增长指引，则由此前“5%—7%区间的低端”下调至“2.5%—3.5%”。这同样是非GAAP全年增长指引，而非GAAP每股收益预测。 [claim:guidance-constant-currency-eps, claim:guidance-core-eps]

**公司把原因也说清了吗？**

answered — 所以，数字改了多少是清楚的，但现有证据中的原因解释不完整、也未量化：已取得的公告及10-Q段落没有把北美投入、成本通胀、降本及其他因素逐项连接成全年盈利指引下调桥。公告预告还会发布管理层准备稿并举行问答，但本次没有取得这些材料；因此不能进一步断言，公司在所有沟通中都没有解释下调原因。 公司明确谈到了经营压力与应对方向：管理层表示，要通过更多创新、有效品牌建设及更精准的渠道执行来改善北美表现，并将识别和实施进一步结构性降本，以支持这些投入、缓解投入品成本通胀上升的影响。这些是公司披露的经营背景和计划，不能直接当成其对全年盈利指引下调幅度的完整归因。 同期，汇率对全年核心EPS增长的预期贡献反而由约1个百分点提高至约1.5个百分点。按指引表的方向判断，这是更有利的假设，而不是盈利增长指引下调的表面原因；它也不解释核心固定汇率EPS指引为何下调。 全年核心有效税率预期也由约22%降至约21%。在其他条件不变时，较低税率通常有利于税后盈利，因此这项假设变化本身也不是盈利增长指引下调的解释；公告没有据此量化其对EPS的抵消作用。 [claim:guidance-explanation-limit, claim:management-investment-cost-context, claim:guidance-fx-assumption, claim:guidance-tax-assumption]

## Unanswered items and issues

None identified.

## Evidence and source locations

- **metadata_ad760c6692f956a4d6a61c1a** (PEP;  to ): manifest.json
  Locator: documents[3], documents[0], documents[1], documents[2]; snapshot SHA-256: dbf78f320aca77b3e4dfaa055e02f216fab9a11fcfa79426fb195da46f9d64f5
- **span_e15075b0f56ab75de1fba9f1** (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[0:6000] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **span_866e7defb63fa38a7cf48e44** (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[6000:12000] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_2daf7bb6c7a896a72ec40dc6** (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[5355:7149] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_b0832db85ae6eb61c6f2a5a8** (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[2476:4225] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_95d83fe58fc0d574467eed76**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[0:1315] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_ae5bdbdc960fbb64f4203c72** (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[27029:28825] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_f33322f4a274b72c73b45362** (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[3978:5604] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **span_8c9a2825bb1288d3bfc9eebe** (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[28825:33825] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_a8a31aeef18e168803843ca8**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[9215:11010] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_8982971aecc0ecb7cbc84840**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[1073:2723] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_35a48ca15b32e7021c45b95a**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[24582:26116] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_de3092c3eeb14bd730017b88**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000050/q320268-kxexhibit991.htm>)
  Locator: snapshots/q320268-kxexhibit991.htm.txt:characters[25866:27276] (zero-based Unicode offsets); snapshot SHA-256: 6669dac8002dacf42b74325b81221b20e6975d573da2427a62d7968e29bc7899
- **chunk_d407d4affad50e074eff0707**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000048/pep-20260905.htm>)
  Locator: snapshots/pep-20260905.htm.txt:characters[17494:19292] (zero-based Unicode offsets); snapshot SHA-256: e6d7c8bd8fa095ef9320b6977f8450bc86f152da76ccc398c1f4bc2a2d4495f0
- **chunk_b7527a9031a54a66f74d68dd**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000048/pep-20260905.htm>)
  Locator: snapshots/pep-20260905.htm.txt:characters[23543:25161] (zero-based Unicode offsets); snapshot SHA-256: e6d7c8bd8fa095ef9320b6977f8450bc86f152da76ccc398c1f4bc2a2d4495f0
- **chunk_3ae3bfc1b2ddd3ecd30b084e**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000048/pep-20260905.htm>)
  Locator: snapshots/pep-20260905.htm.txt:characters[82206:83872] (zero-based Unicode offsets); snapshot SHA-256: e6d7c8bd8fa095ef9320b6977f8450bc86f152da76ccc398c1f4bc2a2d4495f0
- **chunk_7ef7ecff19e7c72fceeb8442**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000048/pep-20260905.htm>)
  Locator: snapshots/pep-20260905.htm.txt:characters[76884:78190] (zero-based Unicode offsets); snapshot SHA-256: e6d7c8bd8fa095ef9320b6977f8450bc86f152da76ccc398c1f4bc2a2d4495f0
- **chunk_d71dd73a1a5ed77942290805**（本轮留存） (PEP;  to ): [source](<https://www.sec.gov/Archives/edgar/data/77476/000007747626000048/pep-20260905.htm>)
  Locator: snapshots/pep-20260905.htm.txt:characters[121243:122999] (zero-based Unicode offsets); snapshot SHA-256: e6d7c8bd8fa095ef9320b6977f8450bc86f152da76ccc398c1f4bc2a2d4495f0

## Reproducible calculations


## Resource use

Model attempts: 5; tool attempts: 2; active seconds: 90.15.
Token usage (when returned by provider): {"planner_steps": 3, "total_tokens": 149099, "total_tokens_reported_calls": 5, "prompt_tokens": 145010, "prompt_tokens_reported_calls": 5, "completion_tokens": 4089, "completion_tokens_reported_calls": 5, "cached_input_tokens": 83085, "cached_input_tokens_reported_calls": 5, "cache_write_tokens": 36763, "cache_write_tokens_reported_calls": 5, "cache_write_30m_tokens": 36763, "cache_write_30m_tokens_reported_calls": 5, "cache_write_12h_tokens": 0, "cache_write_12h_tokens_reported_calls": 5, "reasoning_tokens": 314, "reasoning_tokens_reported_calls": 5}
