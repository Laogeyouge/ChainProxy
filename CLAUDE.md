# CLAUDE.md — project context for Claude Code

> 这个文件 Claude Code 会自动加载。它告诉 Claude 这个项目是什么、怎么布局、有什么坑、用户的偏好是什么。改了它你的 Claude session 立刻能拿到新上下文。

## 项目一句话

ChainProxy 是 macOS 上的链式代理 GUI（本机 → 第一跳 → 第二跳 → 目标），底层用 [mihomo](https://github.com/MetaCubeX/mihomo)，UI 用 PyQt6。当前版本 1.0.1，已在 GitHub 公开发布：https://github.com/Laogeyouge/ChainProxy

## 仓库布局

```
ChainProxy/
├── chainproxy_qt.py            ← GUI 全部代码（PyQt6 单文件）
├── chainproxy_core.py          ← thin shim：from core import *
├── core/                       ← 平台分发后端
│   ├── __init__.py             ← 按 sys.platform import _macos / _windows
│   ├── _common.py              ← 跨平台：YAML 生成、规则集下载、URL 测试、配置 schema
│   ├── _macos.py               ← macOS：networksetup、osascript、sudoers helper、fcntl
│   └── _windows.py             ← Windows：注册表、UAC ShellExecute、Mutex、taskkill
├── config.example.json         ← 示例配置
├── icon.png                    ← README 头图
├── icon.ico                    ← Windows .exe 图标（多分辨率 16-256）
├── ChainProxy.app/             ← .app 骨架（macOS 用）
├── scripts/
│   ├── build.sh / make_dmg.sh / make_icon.py        ← macOS 打包
│   ├── build_windows.ps1                            ← Windows 打包驱动
│   ├── chainproxy_windows.spec                      ← PyInstaller spec
│   └── make_icon_windows.py                         ← icon.ico 生成
├── tests/{smoke_test.py,test_yaml_parity.py}        ← headless 测试
├── dist/                       ← 构建产物（gitignored）
├── docs/                       ← 项目历史、handoff 笔记
└── README.md / LICENSE / CLAUDE.md
```

**单一来源原则**：顶层 `chainproxy_qt.py` / `chainproxy_core.py` / `core/` 是源码。`ChainProxy.app/Contents/Resources/` 里**不**提交 `.py`（`.gitignore` 里排除了），由 `scripts/build.sh` 在打包时拷进去。Windows 用 PyInstaller 直接从仓库根打包。

## 常用命令

```bash
# 直接跑 GUI（开发用）
python3 chainproxy_qt.py

# 把源码拷进 .app（构建自包含 bundle）
bash scripts/build.sh

# 打 .dmg
bash scripts/make_dmg.sh 1.0.2

# 重新生成图标（同时输出 .icns 给 bundle 和 icon.png 给 README）
python3 scripts/make_icon.py

# 发新 release
git tag v1.0.2 && git push origin v1.0.2
gh release create v1.0.2 dist/ChainProxy-1.0.2.dmg --title "ChainProxy 1.0.2" --notes "..."
```

## 配置文件位置（运行时）

**macOS：**
- 配置：`~/Library/Application Support/ChainProxy/config.json`
- 运行时：`~/Library/Application Support/ChainProxy/runtime/`（mihomo.yaml, mihomo.log, ruleset/）
- TUN 模式装的 sudoers 助手：`/usr/local/bin/chainproxy-helper.sh` + `/etc/sudoers.d/chainproxy`

**Windows：**
- 配置：`%APPDATA%\ChainProxy\config.json`（即 `C:\Users\<U>\AppData\Roaming\ChainProxy\`）
- 运行时：`%APPDATA%\ChainProxy\runtime\`
- mihomo.exe 推荐放：`%APPDATA%\ChainProxy\mihomo.exe`，也支持 PATH / Program Files / scoop / chocolatey
- 系统代理：HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings 注册表
- TUN：每次启动 mihomo 都触发 UAC（Windows 没有 macOS 那种"装一次免密"机制）

## 关键设计决定

- **mihomo 1.19+ 取消了 `relay` proxy-group**，链式代理通过 second-hop 节点的 `dialer-proxy` 字段串起来（指向 first-hop 名字）。
- **TUN 模式回环**：mihomo 的 TUN 会捕获**所有** IPv4 流量，包括第一跳客户端自己的拨号。所以要在 `first_hop_process_names` 里把客户端的所有 binary（GUI 进程 + 实际代理引擎）都列出来，加 `PROCESS-NAME,xxx,DIRECT` 规则跳过。漏一个就会 "context deadline exceeded"。
- **TUN 免密**：第一次开 TUN 装一个 root 拥有的 helper.sh + sudoers NOPASSWD 条目，之后用 `sudo -n` 调用永不弹密码。`HELPER_VERSION` 字段做版本号；改 helper 内容时记得 bump。
- **去抖重启**：用户改任何配置（节点、规则、设置）会调 `maybe_restart_for_config_change`，1.5 秒去抖后单次重启 mihomo。手动 `stop()` 会取消待执行的重启。
- **silent 模式 start**：自动重启走 `start(silent=True)`，错误用 toast，不弹模态框打断用户输入。
- **侧边栏**：单 QListWidget + 不可选的 section header（之前用两个 QListWidget 互相 cross-deselect，遇到 Qt 的 `setCurrentRow(-1)` 在 processEvents 后会被复位的坑）。
- **focusOutEvent monkey-patch on QPlainTextEdit**：PyQt6 的 Python 实例属性优先于 C++ 默认 dispatch，验证可行。用于自定义规则的失焦自动保存。

## 用户偏好（从过往交互沉淀）

- 沟通用**中文**
- UI 风格：**Apple System Settings**，扁平、无 emoji 装饰、浅深色跟随系统
- 改完代码默认要重启验证、看 app.log 是否干净
- 提交信息和文档写**为什么**而不是**做了什么**
- 不要弹"我做了 X"的总结，用户自己看 diff
- 节点信息绝对不能进 git（仓库已脱敏，`.gitignore` 里有 `config.json`）

## 当前状态（2026-05-05）

- ✅ macOS 版 1.0.1 已发布
- ✅ Windows 版完成端到端移植 + 真机验证：
  - core 拆成平台分发（`core/_common.py` + `core/_macos.py` + `core/_windows.py`）
  - 系统代理：HKCU 注册表 snapshot/restore，启动失败时自动恢复用户原代理
  - TUN：GUI 启动时一次 UAC（`relaunch_elevated`），整个会话启停 mihomo 不再弹
  - mihomo 启动后 poll controller 端口 2 秒确认存活，避免代理被设到死端口
  - 规则集监控：mihomo controller `/logs` API 实时拉日志（解决 elevated mihomo stdout 不可重定向）
  - PyInstaller spec + Inno Setup `installer.iss`：installer 自带 mihomo.exe，用户可选安装路径 + 桌面快捷方式
  - 测试：smoke / yaml parity / proxy lifecycle / live runner 四套全过
- 🚀 下版本（1.1.0）：把 Windows installer 加进 Releases；release 流程见 `docs/RELEASE.md`

## 给 Claude 的工作约定

- **改代码前**用 Read 看完整文件再 Edit；不要凭记忆改。
- **测试方式**：headless 跑 PyQt6 时设 `os.environ['QT_QPA_PLATFORM'] = 'offscreen'`，能在没显示器的情况下构造完整 MainWindow 跑断言。
- **改完 GUI 别忘了 sync `/Applications/ChainProxy.app/Contents/Resources/`**——开发时改 `chainproxy_qt.py` 不会自动反映到正在跑的 .app，需要 `cp` 一下。
- 仓库 remote 已配好（`origin` → `https://github.com/Laogeyouge/ChainProxy`），`gh` 已认证。可以直接 push / 发 release。
- 有问题先看 `docs/HISTORY.md` 了解为什么某段代码写成那样。
