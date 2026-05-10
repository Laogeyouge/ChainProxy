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

## 当前状态（2026-05-10）

- ✅ **macOS 1.1.10 已发布**（DMG 在 release v1.1.10）——分流规则页「命中后」combo 的
  三个 GUI bug 一次性修：(a) 直接 setCellWidget 取代 wrap+layout，QTableWidget 强制
  combo 尺寸 = cell rect，QSS `border-radius` 不可能溢出 cell 被裁；(b) 列宽 140 → 180，
  「FirstHopOnly」完整显示；(c) 新增 `NoWheelComboBox`（继承 QComboBox 把 wheelEvent
  ignore 掉）防滚轮误触，应用到 RulesPage 的两个 combo。
- ⏳ **Windows 1.1.10 待打包发布**——源码已就绪（`chainproxy_qt.py` 是跨平台共享单文件，
  fix 已 push 到 main；`scripts/installer.iss` 的 `MyAppVersion` 已 bump 到 1.1.10）。
  在 Windows 机器上：`git pull` → `powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1`
  → 产物 `dist\ChainProxy-Setup-1.1.10.exe` 上传到现有 v1.1.10 release（`gh release
  upload v1.1.10 dist\ChainProxy-Setup-1.1.10.exe`）。详见下方「Windows 1.1.10 打包」。
- ✅ Windows 1.1.9 工程完成 + 真机验证通过（brand chooser、`.exe` 后缀剥除、conhost/svchost
  噪音过滤、旧版残留自动清理）；安装器 `dist\ChainProxy-Setup-1.1.9.exe` 已打包并发 release
- ✅ macOS 1.1.9 已发布——彻底放弃品牌列表，改为 **`netstat -anvp tcp` 拿 listener PID
  → 走 `.app` bundle → 把 bundle 内进程作为家族返回**。`netstat -v` 不需要 sudo 就能看
  到 root listener 的 PID（FastLink 的 AtlasCore、Clash Verge 的 verge-mihomo、猫猫云
  的 CatCore 都是 root 拥有），所以 macOS 端「识别本机机场客户端进程」按钮**只识别
  用户配置的那个端口对应的那一个 .app**，不会把机器上其他机场客户端拉进白名单。
- 🚀 历史发布：1.0.1（macOS 首发） → 1.1.0–1.1.6（多版本 Win/Mac 同步） → 1.1.7（GeoIP
  bundle + auto-detect button） → 1.1.8（brand-grouped chooser、SOCKS5 探测兜底、helper
  版本号修复、`<defunct>` 过滤、配置 `.bak` 备份；macOS DMG 已打但未发，被 1.1.9 取代）→
  1.1.9（Windows: `.exe` 后缀、shell 噪音过滤、自动清理旧版残留；macOS: netstat-driven
  auto-detect 按 .app bundle 路径分组，废弃品牌列表路径）→ 1.1.10（GUI fix：rule-set
  picker 不再被裁，combo 滚轮事件统一忽略）

## Windows 1.1.10 打包（无需改任何代码）

源码 fix 已通过共享 `chainproxy_qt.py` 同步到 main；installer 版本号已 bump 到 1.1.10。
在 Windows 机器上执行：

```powershell
# 1. 拉最新代码
git pull

# 2. 一键构建 mihomo 捆绑 + PyInstaller .exe + Inno Setup 安装器
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1

# 产物：
#   dist\ChainProxy\ChainProxy.exe      ← 解压版（可直接跑）
#   dist\ChainProxy-Setup-1.1.10.exe    ← 安装器（要发布的）

# 3. 上传到已存在的 v1.1.10 release（macOS DMG 已挂在那里）
gh release upload v1.1.10 dist\ChainProxy-Setup-1.1.10.exe
```

**验证 GUI 修复**（开任意 ChainProxy.exe，进入「分流规则」页）：
- 内置规则集表格里的「命中后」下拉框，下边圆角不再被裁
- 把规则切到 `FirstHopOnly`，文字「y」必须完整显示，不能被截
- 鼠标悬停在下拉框上滚动滚轮，**值不能变**（必须点击下拉才能改）

## 1.1.8 改动清单（核心）

- **brand-grouped airport-client detection**：`core/_common.py::AIRPORT_BRANDS` + `airport_brand_for_name`，
  支持 FastLink / Karing / Clash Verge / Mihomo Party / Surge / V2RayU/N/NG / NekoBox /
  Stash / Shadowrocket / Quantumult X / Pluto + 通用 mihomo / sing-box / v2ray / xray /
  hysteria / trojan / shadowsocks。
- **SOCKS5 握手兜底**：`socks5_handshake_succeeds()`，绕开 root/SYSTEM listener 不可见的
  限制；`detect_first_hop_processes` 在 lsof/netstat 找不到 PID 时若 SOCKS5 通就走 ps -A
  按品牌枚举，单家族直接返回，多家族返回 `[]`，让 GUI 调 `list_airport_client_families`
  弹选项让用户挑。
- **helper 版本号同步修复（致命修复！）**：之前 `HELPER_VERSION` Python 常量改了但 bash
  HELPER_SCRIPT 字面里的 `# version: N` 没对齐，导致每次启动都重装、每次都要输密码。
- **`<defunct>` 过滤**：`_clean_proc_name` 过滤 `<defunct>` / `(spinning)` 等 ps 噪音，
  避免 `PROCESS-NAME,<defunct>,DIRECT` 进规则。
- **配置自动备份**：`save_config` 在写入前把上一份完好的 `config.json` 拷到 `.bak`，前提
  是 `first_hops`/`second_hops` 都非空（避免覆盖一个 known-good backup）。
- **节点 add/dup/rename/del 触发 mihomo 重启**：之前重命名节点 mihomo.yaml 不重新生成，
  导致 URL 测试显示老节点名。现在统一调 `maybe_restart_for_config_change`。
- **TUN 冲突检测加 IFF_UP 校验**：之前误把自己 down 但残留 IP 的 utun 当成另一个 TUN 软件。
- **诊断 trace**：`runtime/tail-debug.log` 记录 tail 的 START/EXIT/IDLE/REOPEN 事件 +
  GUI 的 stop()/start() 调用栈，方便日后排"日志卡住"类问题。

## 1.1.9 改动清单（Windows 真机验证修补）

- **`.exe` 后缀剥除**：`airport_brand_for_name` 入口剥 `.exe`，让 5 个 `$` 锚定的 brand
  patterns（`^mihomo$` / `^v2ray$` / `^xray$` / `^sgw$` / `^Stash$`）能匹配 Windows 形式。
  之前裸跑 `mihomo.exe` 当机场核心时 SOCKS5 兜底返回空。
- **shell/console 噪音过滤**：`_NEVER_WHITELIST` 改为 bare 名，`name_should_skip` 入口剥
  `.exe`。`_windows.py` 的直接 PID 分支 `push()` 改为调 `name_should_skip`，挡住 `conhost.exe`
  / `svchost.exe` / `python.exe` / `powershell.exe` 等渗进 whitelist。
- **`_auto_detect_processes` 自动清理旧版残留**：每次点「识别」时把 existing 列表里命中
  `_NEVER_WHITELIST` 的项剔除并保存，让升级用户的 stale config 自愈。
- **`name_should_skip` 公开导出**：加进 `core/_windows.py` + `core/_macos.py` 的 `__all__`，
  GUI 通过 `core.name_should_skip` 访问。

## 注意事项（来自 2026-05-09 调试经验）

- **不要 SIGTERM 用户正在跑的 ChainProxy 来发新版本**——用户的 NodeEditor 草稿（QLineEdit 里
  没失焦的字段）会被一并杀掉。应该让用户自己关 .app 或者用 osascript quit。如果非要部署，
  优先 build DMG + 让用户自己装。
- **重命名节点必须触发 mihomo 重启**——否则 yaml 没更新，URL 测试会一直显示老节点名。所有
  结构性 cfg 改动（add/dup/rename/del）都要调 `maybe_restart_for_config_change`。

## 给 Claude 的工作约定

- **改代码前**用 Read 看完整文件再 Edit；不要凭记忆改。
- **测试方式**：headless 跑 PyQt6 时设 `os.environ['QT_QPA_PLATFORM'] = 'offscreen'`，能在没显示器的情况下构造完整 MainWindow 跑断言。
- **改完 GUI 别忘了 sync `/Applications/ChainProxy.app/Contents/Resources/`**——开发时改 `chainproxy_qt.py` 不会自动反映到正在跑的 .app，需要 `cp` 一下。
- 仓库 remote 已配好（`origin` → `https://github.com/Laogeyouge/ChainProxy`），`gh` 已认证。可以直接 push / 发 release。
- 有问题先看 `docs/HISTORY.md` 了解为什么某段代码写成那样。
