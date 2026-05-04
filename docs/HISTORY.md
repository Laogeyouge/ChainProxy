# 项目历史与关键决策

> 给"刚接手不知道这个项目怎么走到今天"的 Claude 看。按时间顺序记录大的演化节点和**为什么**这么做。

## 出发点

用户已经有一个机场客户端，但出口 IP 在 OpenAI / Anthropic / Google AI 等那里被风控；同时手上还有一个干净的二跳 VPS。需求：让大部分流量继续走机场（速度），少数关键域名再绕一跳到二跳节点（IP 干净）。

## 演化阶段

### 阶段 0：原始 Tkinter 版（已删除）

最早是 `chainproxy.py`（约 2200 行单文件 Tkinter）。问题：
- Tkinter 在 macOS 上 native look 极差
- 字体、间距、颜色都不像 macOS 应用
- 自定义样式基本不可能

→ 决定全面重写为 PyQt6。

### 阶段 1：拆分 backend/UI

把所有跟系统打交道的逻辑（mihomo runner、配置、TUN 助手、系统代理、URL 测试）抽到 `chainproxy_core.py`。`chainproxy_qt.py` 只管 UI。这一步是后续所有跨平台化（包括 Windows 移植）的前提。

### 阶段 2：PyQt6 重写 + 多次 UI 迭代

用户对 GUI 风格反馈了几轮（"太丑"、"一块黑一块白"、"按钮符号像输入法"）。最终定型：

- **Apple System Settings 风格**：白底、扁平、hairline 分隔线
- **跟随系统主题**：浅色 / 深色 / 跟随系统三档
- **无 emoji 装饰**：所有按钮纯文字标签
- **直接操纵**：规则表的"启用"列是真开关、"命中后"列是真下拉，不要"选中行→点按钮"

### 阶段 3：彻底的 Bug 排查

用户要求"完整检查所有 bug"。发现并修复：

1. **侧边栏选择被弹回**——`QListWidget.setCurrentRow(-1)` 在 processEvents 后会复位。改用单 QListWidget + 不可选 section header。
2. **每秒重 polish**——状态指示灯每 tick 都 unpolish/polish。改成只在状态翻转时 polish。
3. **逐键重启冻结 UI**——每个字段提交触发一次 stop+start，10 秒冻结。改用 1.5s QTimer 去抖。
4. **自动重启弹错误对话框**——给 `start()` 加 silent 参数，自动重启走 silent 路径，错误进 toast 不弹模态。
5. **手动 stop 被自动重启覆盖**——`stop()` 取消待执行的 `_restart_timer`。
6. **focusOutEvent 在 PyQt6 上能 monkey-patch 吗**——验证可以，PyQt 的 Python 实例属性优先于 C++ 默认。

### 阶段 4：装进 /Applications

写了 `ChainProxy.app/` 骨架（Info.plist + 启动器 bash + .icns），让它成为真正的 macOS 应用。启动器探测多个 Python 路径（PATH / Homebrew / `/usr/bin`），找不到 PyQt6 就弹原生 alert。

### 阶段 5：图标

procedurally 用 Pillow 画了一个图标——蓝色渐变圆角矩形 + 三个白节点连成链 + 右边一个箭头表示出口。生成 10 种 macOS 标准尺寸打包成 `.icns`。

### 阶段 6：开源到 GitHub

- 写中文 README
- MIT License
- `config.example.json` 含用户的自定义规则（OpenAI/Anthropic/Google AI 全套）但节点字段写成 `FILL_ME_IN`
- 用 `gh` CLI 建仓库 + push + 打 release（带 .dmg）
- GitHub: https://github.com/Laogeyouge/ChainProxy

### 阶段 7：1.0.1 修补

用户反馈 README 不该突出 FastLink（那只是他的第一跳客户端之一），图标在 GitHub 上不显示。

- 重命名 `FastLinkOnly` → `FirstHopOnly`，加配置自动迁移
- README 改为列举所有可用的机场客户端（ClashX/V2RayX/Karing/Stash/FastLink/自建 mihomo），不偏袒任何一个
- 默认 `first_hop_process_names` 改为空列表，不再硬编码 FastLink 三个进程名
- `.icns` 在 GitHub 不渲染——加输出 `icon.png` 到仓库根，README 引 PNG

### 阶段 8（当前）：考虑 Windows 移植

详见 `docs/WINDOWS_PORT_PLAN.md`。

## 易踩的坑（按出现频率）

### mihomo 1.19+ 没有 relay proxy-group

老教程里串两跳用 `type: relay` 的 proxy-group。**已经废了**。现在用 `dialer-proxy: <first-hop-name>` 字段挂在 second-hop 节点上，由 mihomo 自动串起来。

### TUN 路由清不干净导致开 TUN 失败

mihomo 的 TUN 在退出时不一定能干净地撤掉 `1.0.0.0/8` 等"全互联网" route。下次启动时 mihomo 自己会因为 "add route: 1.0.0.0/8: file exists" 拒绝起。helper.sh 的 `start` 动作里先 `cleanup_routes` 再启动。

### TUN 模式下机场客户端的拨号被回环

mihomo TUN 接管所有 IPv4 → 机场客户端拨它的 VPN 节点 → 被 TUN 抓 → 没匹配规则掉到 MATCH,Chain → 又回到机场客户端 → "context deadline exceeded"。

修复：在 `first_hop_process_names` 里把客户端的所有 binary 列出来（GUI 进程 + 实际代理引擎，比如 FastLink 是 `FastLink机场` + `AtlasCore_arm64` + `AtlasCore_amd64`），加 `PROCESS-NAME,xxx,DIRECT` 优先放行。漏一个就 100% 复现。

### macOS networksetup 服务名匹配

老的代码用 regex 抓 `networksetup -listnetworkserviceorder` 输出，曾经有 bug 误把 "Hardware Port: ..." 行也当成服务名。现在的 `_default_route_iface` + `_service_to_iface_map` 是修过的版本。

### Qt setCurrentRow(-1) 不持久

QListWidget 不允许"无选中"——`setCurrentRow(-1)` 当时生效，processEvents 后被 Qt 复位回旧值。两个 QListWidget 互相 cross-deselect 会被这个坑搞死。解法：单 QListWidget + 把分节标题做成不可选 item（`setFlags(Qt.ItemFlag.NoItemFlags)`）。

### macOS LaunchServices 图标缓存粘性

改完 .app 的图标后，Dock / Finder / Spotlight 不会立刻更新。要 `lsregister -f /path/to/app` 强制重注册，再 `killall Dock Finder` 触发刷新。
