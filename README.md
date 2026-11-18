# VeriFin · Group 6

VeriFin 用公告、可追溯的计算和逐项核验回答美股财报问题，支持在同一次对话中追问。组内交接包已展开，可以直接从仓库根目录开始。

## 第一次使用

通过仓库的 **Code → HTTPS** 复制地址，运行 `git clone <仓库地址>`，进入克隆的目录。私有仓库需要先用自己的 GitHub 账号接受邀请。打开 [组友接手任务单](00_组友接手任务单.md) 查看后续任务。

先用 Python 3.11 或更高版本从仓库根目录运行离线验收。结果写入新的空目录，无需模型凭据。历史交互 Demo 可直接打开 [实测浏览页](demo/打开看实测.html)。

```bash
python3 audit/offline_check.py my-first-check
```

要开发或运行应用，在仓库根目录执行以下命令。可编辑安装让以后拉取的 Python 源码立即生效。启动方式、模型配置和公告资料准备见 [应用说明](code/README.md)。

```bash
cd code
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[disclosures]'
```

## 拉取更新

在自己的克隆目录运行 `git pull --ff-only`。正在运行的 VeriFin 请重启进程。依赖声明有变化时，在原虚拟环境重新执行上面的可编辑安装命令。组员要修改代码时，可以从最新 main 开一个自己的分支，提交后通过 Pull Request 合并。

## 项目材料

- [课程指南](COMP7608B_Final_Project_Student_Guideline.pdf) 与 [已提交 Proposal](proposal/)。
- [实验与复现](evaluation/) 与 [麦当劳 E1 补充实验](VeriFin_MCD_E1_20261117/结果.md)。
- [历史 Demo 与原始对话](demo/) 与 [设计资料](design/)。

Git 标签 `handoff-2026-11-17` 保存组内交接基线。主包内原有的 `manifest.sha256` 对应这个标签。后续提交包括微软三轮调试相关的回执恢复、金额核对和改稿停止修复。
