# 本批4538中性E2：Codex本地coordinator阅卷

依据本批未改原版baseline/actual-first-final.zip中的16份最终用户展示，逐份与同ZIP首稿和手册(1)比较。不是旧503c149批次，不是人类盲评；原始truth_score仍为null。财务分为发行人/期间、值/币种、所问范围的来源支持，各0/1；固定臂承认latest未获证实时第三项为0，同时保留其正确数值。无草稿的技术失败不分配质量分。05首轮另评歧义识别/有效澄清，两项0/1。

|手册/中性ID|臂/轮|首稿→最终评分|最终用户财务/澄清与证据覆盖|产品状态|模型/工具（本轮增量；累计）|
|---|---|---|---|---|---|
|01-direct/k8m2a|adaptive/1|3/3 → 3/3|MSFT69,632m；2024-10-01至12-31；截止前来源；所问范围与引用一致|complete|5/1；5/1|
|01-direct/k8m2a|fixed/1|3/3 → 3/3|MSFT69,632m；2024-10-01至12-31；截止前来源；所问范围与引用一致|complete|2/2；2/2|
|02-previous/p4n7s|adaptive/1|3/3 → 3/3|MSFT69,632m；2024-10-01至12-31；截止前来源；所问范围与引用一致|complete|5/1；5/1|
|02-previous/p4n7s|adaptive/2|— → —|无首稿/无数值用户答；ModelError终止，不对未交付内容打分|partial|1/0；1/0|
|02-previous/p4n7s|fixed/1|2/3 → 2/3|MSFT69,632m；2024-10-01至12-31；截止前来源；明确未证明latest，精确季度数有来源，但请求的latest覆盖缺口仍在|partial|4/5；4/5|
|02-previous/p4n7s|fixed/2|2/3 → 2/3|MSFT65,585m；2024-07-01至09-30；前季上下文正确；明确未证明latest，精确季度数有来源，但请求的latest覆盖缺口仍在|partial|4/4；4/4|
|03-source-outage/r9v3b|adaptive/1|3/3 → 3/3|MSFT69,632m；事实工具失败后原公告单季表支持；非仅累计值；所问范围与引用一致|complete|6/2；6/2|
|03-source-outage/r9v3b|fixed/1|3/3 → 3/3|MSFT69,632m；事实工具失败后原公告单季表支持；非仅累计值；所问范围与引用一致|complete|2/3；2/3|
|04-switch/t6q1d|adaptive/1|3/3 → 3/3|MSFT69.6bn原公告舍入数，明确非精确million；期间/截止正确；所问范围与引用一致|complete|6/2；6/2|
|04-switch/t6q1d|adaptive/2|— → —|无首稿/无数值用户答；ModelError终止，不对未交付内容打分|partial|1/0；1/0|
|04-switch/t6q1d|fixed/1|2/3 → 2/3|MSFT69,632m；2024-10-01至12-31；截止前来源；明确未证明latest，精确季度数有来源，但请求的latest覆盖缺口仍在|partial|4/5；4/5|
|04-switch/t6q1d|fixed/2|2/3 → 2/3|NVDA35,082m；2024-07-29至10-27；未混用截止后Q4；明确未证明latest，精确季度数有来源，但请求的latest覆盖缺口仍在|partial|4/5；4/5|
|05-date/w2j8f|adaptive/1|— → 2/2|明确询问Jan2还是Feb1；无日期预泄露|waiting_for_clarification|1/0；1/0|
|05-date/w2j8f|adaptive/2|3/3 → 3/3|MSFT69,632m；2024-10-01至12-31；截止前来源；所问范围与引用一致|complete|5/1；6/1|
|05-date/w2j8f|fixed/1|— → 0/2|只问TaskSpec/schema，未指出日期歧义；0/2|waiting_for_clarification|0/0；0/0|
|05-date/w2j8f|fixed/2|2/3 → 2/3|MSFT69,632m；2024-10-01至12-31；截止前来源；明确未证明latest，精确季度数有来源，但请求的latest覆盖缺口仍在|partial|4/5；4/5|

16/16计划格均有状态/用户展示归档：12份可评财务答、2份日期澄清、2份无草稿技术失败。12份财务答中7份覆盖所问范围，5份精确数值正确但latest支持明确留缺口；日期澄清1份有效、1份schema提示无效。首稿到终稿未见数值修复或新增数值回归；无稿格不能算未修复的财务错误。模型/工具总数54/36（累计episode按ID去重；05续集不重复计费），receipt token详见baseline/completion.json。以上是本地coordinator评分，不替换原始truth_score或独立人评。
