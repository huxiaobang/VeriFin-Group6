# 标注编辑器 DEMO

直接打开 [annotation-demo.html](annotation-demo.html) 即可试用。页面包含明显的 DEMO 标记、虚构金融材料和空白人工字段；**不是人工标注、真实财报、模型结果或评测 gold**。所有演示文件独立放在 `examples/`，不进入正式 `data/evaluation/`。

`annotation-demo-draft.json` 是未签署草稿，可测试页面的导入按钮。可以增加/删除内容单元、拆分断言、填写草稿再导出。空白内容即使勾选签署也不能导出为已完成标注。请勿把演示草稿提交至正式结果汇总。

重建页面：

```bash
python -m examples.build_annotation_demo
```

输入保存在 `annotation-demo-input.json` 与 `annotation-demo-draft.json`。构建脚本拒绝带完成签署或已填写 verdict 的演示草稿。源材料含一段长中文引用，用于检查换行与滚动；其 source URL 使用不可解析的 `.invalid` 演示域名，页面本身不发网络请求。

2026-11-06 验证结果：

- 主 agent 已通过应用内置浏览器访问本地页面，检查中英文布局、空白缺项提示、添加/删除内容单元和重新加载恢复空白状态；未观察到 JavaScript console 错误。展开的长中文证据区无横向溢出（clientWidth = scrollWidth = 346），高度限制为 440 像素并可内部纵向滚动。截图见 [annotation-demo.jpg](../docs/verification/annotation-demo.jpg)。没有打开 Chrome 标签。
- jsdom 31.0.0 离线执行了真实 DOM 事件处理器：初始渲染、增加/删除内容单元、增加/删除断言、空白禁止完成、修改清除签署、未签署草稿导出、草稿导入回环、错误 packet 拒绝、长中文引用保留以及无外部资源元素，全部通过。
- DOM 测试拦截下载 Blob 并检查导出的 JSON，没有生成真实人工判断；它不替代浏览器的布局与原生文件对话框检查。

复现 DOM 检查时在独立临时目录安装 `jsdom@31.0.0`，然后运行：

```bash
node examples/check_annotation_demo.cjs /path/to/node_modules/jsdom
```

jsdom 只是开发验收依赖，不是 VeriFin 运行依赖。相关 API 见 [jsdom 官方文档](https://github.com/jsdom/jsdom)。

边界检查：生成器拒绝 administrative packet、错误 packet 绑定和系统 Supported 等标签；源文本通过转义后的惰性 JSON 保存，并使用 DOM `textContent` 显示。页面 CSP 禁止外部资源与连接，只允许自身固定哈希脚本。草稿导入检查 packet、标注者与协议；最终仍由 sealed packet 对已完成 JSON 做权威 Python 校验。此处没有把格式检查当作独立性或金融正确性的证明。
