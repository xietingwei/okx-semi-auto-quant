# GitHub 自动同步钩子

Codex 每轮工作停止时运行 `push_to_github.py`。

只有同时满足以下条件才会推送：

- 当前目录属于 Git 仓库；
- `origin` 指向 GitHub；
- 当前不是 detached HEAD；
- 工作区没有未提交变更；
- 当前分支存在尚未同步的提交。

运行日志写入 `.git/codex-github-push.log`，不会进入仓库。

Codex 会对项目钩子执行信任审核。修改钩子后，需要在 Codex 的“设置 → 钩子”
页面重新审核并信任当前版本。
