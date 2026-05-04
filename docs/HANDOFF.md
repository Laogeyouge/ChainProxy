# HANDOFF — 在 Windows 上接手这个项目

> 这个文件给"刚在 Windows 上 clone 完仓库、第一次打开 Claude Code"的你看。

## 第一次启动检查清单

```powershell
# 1. clone
git clone https://github.com/Laogeyouge/ChainProxy.git
cd ChainProxy

# 2. 装依赖
python -m pip install -r requirements.txt

# 3. 装 mihomo (Windows)
#    方式 a: 下载 mihomo Windows 二进制（在 release 解压到 PATH 目录里）
#       https://github.com/MetaCubeX/mihomo/releases
#    方式 b: scoop install mihomo  （如果你装了 scoop）

# 4. 在 Claude Code 里打开这个目录
#    Claude 会自动读 CLAUDE.md 和 docs/ 拿到完整上下文
```

## Claude 接手后该做的事（按优先级）

1. **读 `CLAUDE.md`**——项目布局、命令、设计决策、用户偏好都在那
2. **读 `docs/HISTORY.md`**——知道这个软件是怎么从 Tkinter 演化到 Qt 的、踩过哪些坑
3. **读 `docs/WINDOWS_PORT_PLAN.md`**——Windows 移植的完整路线图
4. **不要重读整个 `chainproxy_qt.py` 全文**（约 1900 行）——按需 grep / 局部 read
5. **不要再改 macOS 部分的功能逻辑**——已经稳定发布；只在需要 cross-platform 重构时碰

## 当前文件状态

- macOS 版本已发布 1.0.1：https://github.com/Laogeyouge/ChainProxy/releases
- 主分支：`main`
- 最新 commit：`Laogeyouge/ChainProxy@main`
- 没有未推送的本地改动（除非 macOS 那边后来又改了；先 `git pull` 确认）

## 上一次会话的关键结论

用户问能不能做 Windows 版。我给了个评估（详见 `docs/WINDOWS_PORT_PLAN.md`）：

- ✅ GUI 100% 可移植（PyQt6 跨平台）
- ❌ TUN 模式没法做"输一次密码后免密"——Windows 的 UAC 不允许这种持久授权
- ⚠️ 后端 (`chainproxy_core.py`) 里所有跟系统交互的代码要重写一遍
- 🚫 macOS 上没法跑 Windows 二进制做端到端测试，必须有 Windows 环境

用户当前**还没明确同意启动 Windows 版**。Claude 接手后第一件事应该是问用户：

> "看了 `docs/WINDOWS_PORT_PLAN.md`，你接受 TUN 模式每次启动都要点一次 UAC 这个限制吗？接受的话我开始拆 core 模块、写 Windows 后端。"

如果用户同意，按 `WINDOWS_PORT_PLAN.md` 的"实施步骤"那一节开干。

## 用户的工作环境（Windows 这边假设）

- Windows 11（或更新）
- 有 Python 3.9+（用 `python --version` 确认；没有就 `winget install Python.Python.3`）
- 有 git（用 `git --version` 确认；没有就 `winget install Git.Git`）
- Claude Code 已装并能用
- **可能没装 mihomo**——首次启动需要装

## 仓库 git 状态

- `origin` → https://github.com/Laogeyouge/ChainProxy（用户名：Laogeyouge）
- 在 macOS 那边 `gh auth login` 过；Windows 这边需要重新 auth：
  ```powershell
  winget install GitHub.cli
  gh auth login
  ```

## 不能做的事

- 不要把 `~/.config` 之类的东西搬过来——那是 Linux 路径
- 不要假设 `/usr/local/bin` 存在
- 不要用 `bash` 写脚本（用户不一定有 WSL）；用 `.ps1` 或 `.bat`
- 不要 `os.system` / `subprocess` 调 `networksetup` / `osascript` / `pkill`——这些都是 macOS-only

## 安全 / 隐私提醒

- `~/Library/Application Support/ChainProxy/config.json`（Windows: `%APPDATA%\ChainProxy\config.json`）含用户的真实节点信息——**永远不要 commit**
- `.gitignore` 里已经排除了 `config.json`
- 用户实际的二跳节点服务器/账号密码绝不能进任何文档、commit message、GitHub issue
