# ChainProxy

> macOS 上的链式代理 GUI：本机 → 第一跳 → 第二跳 → 目标。
> 基于 [mihomo](https://github.com/MetaCubeX/mihomo) 内核，PyQt6 写的原生外观。

<p align="center">
  <img src="icon.png" width="120" alt="ChainProxy 图标">
</p>

## 这是什么

很多机场客户端只能让你出一跳。ChainProxy 让你在**已经能用的第一跳**基础上，把指定流量再串到**第二跳**节点上去——常见用途：

- 已有的机场出口 IP 在某些境外服务（OpenAI / Anthropic / Google AI 等）那里被风控，但你还有一个干净的二跳 VPS 想接力使用
- 想让大部分流量走机场（速度快），少数关键域名再多绕一跳（IP 干净）
- 公司机场只能走 SOCKS5，但你想给系统打开 TUN，让所有 App 都被接管

工作原理：

```
 ┌─ macOS 本机 ─┐    ┌───── 第一跳 ─────┐    ┌──── 第二跳 ────┐    ┌─ 目标 ─┐
 │   App 流量    │ ──▶│  机场客户端的       │ ──▶│  你自己的节点      │ ──▶│  互联网 │
 │              │    │  本地 SOCKS5         │    │  trojan / ss / …  │    │        │
 └──────────────┘    └───────────────────┘    └─────────────────┘    └────────┘
                            ▲                          ▲
                            │                          │
                       由 mihomo 用 dialer-proxy 串起来，分流策略由你指定
```

第一跳是**任何在 `127.0.0.1:某端口` 暴露 SOCKS5 的机场客户端**——ClashX、V2RayX、Karing、FastLink、Stash、自建 mihomo 等等都行。第二跳是**你自己的 VPS 或干净节点**，协议支持 socks5 / http / trojan / ss / vmess / hysteria2。

## 主要功能

- **一键启停链式代理**，状态、链路可视化，TUN / 系统代理两种模式可选
- **直接编辑分流规则**：内置 13 个 [Loyalsoldier clash-rules](https://github.com/Loyalsoldier/clash-rules) 规则集，每行可直接点开关 / 切换"命中后走哪一跳"
- **自定义规则前/后置**：支持 `DOMAIN-SUFFIX` / `DOMAIN-KEYWORD` / `IP-CIDR` / `PROCESS-NAME` 等所有 mihomo 规则语法
- **规则测试页**：发一个 HTTP 请求，看它命中了哪条规则、最终从哪个节点出去
- **网络急救按钮**：一键清系统代理 / 杀残留 mihomo / 删 TUN 路由 / 关 utun，断网时救命用
- **TUN 模式免密**：第一次开启时输一次管理员密码，会装一个 sudoers 助手脚本，之后再也不弹密码
- **跟随系统的浅 / 深色主题**，Apple System Settings 风格的 UI
- **快捷键**：⌘1–⌘5 切页面 · ⌘R 启停 · ⌘T 聚焦快速测试

## 系统要求

- macOS 11 (Big Sur) 或更新
- Python 3.9+（macOS 自带的 `/usr/bin/python3` 也行，但建议 `brew install python`）
- [`mihomo`](https://github.com/MetaCubeX/mihomo)：`brew install mihomo`
- [`PyQt6`](https://pypi.org/project/PyQt6/)：`pip3 install PyQt6`

## 安装

### A. 从 Releases 下载 .dmg（推荐）

1. 去本仓库的 [Releases](../../releases) 页下载最新版 `ChainProxy-x.y.z.dmg`
2. 双击挂载，把 ChainProxy.app 拖到 Applications
3. 提前装好 mihomo + PyQt6：

   ```bash
   brew install mihomo
   pip3 install PyQt6
   ```

4. Spotlight 搜 ChainProxy 打开。第一次启动如果 Gatekeeper 拦住，在 Finder 里右键 → 打开。

### B. 从源码运行

```bash
git clone https://github.com/Laogeyouge/ChainProxy.git
cd ChainProxy

brew install mihomo
pip3 install PyQt6

# 直接跑（开发用，没有图标 / Dock 集成）
python3 chainproxy_qt.py

# 或者打包成 .app 装进 /Applications
bash scripts/build.sh
cp -R ChainProxy.app /Applications/

# 想自己打 .dmg：
bash scripts/make_dmg.sh 1.0.0
# → dist/ChainProxy-1.0.0.dmg
```

## 使用

1. **打开你的机场客户端**（任何能输出 SOCKS5 的都行：ClashX / V2RayX / Karing / Stash / FastLink / 自建 mihomo …），关掉它自己的"系统代理"和"TUN"开关，只让它在 `127.0.0.1:某端口` 暴露一个 SOCKS5
2. 打开 ChainProxy，去**节点**页：
   - **第一跳**：填机场客户端那个本地 SOCKS5 端口（比如 `127.0.0.1:7891`）
   - **第二跳**：填你的二跳节点（trojan / ss / vmess / hysteria2 / socks5 都行）
3. 去**分流规则**页：
   - 默认规则集里 `proxy` / `gfw` / `google` / `tld-not-cn` 等都走 **FirstHopOnly**（只走第一跳）
   - **自定义规则前置**里写你想强制走第二跳的域名，目标填 `Chain`
   - 改完任何规则会自动保存，1.5 秒去抖后自动重启 mihomo
4. 回**概览**页点 **启动**，搞定

### 分流目标取值

| 目标 | 含义 |
|:--|:--|
| `Chain` | 走完整链路：本机 → 第一跳 → 第二跳 → 目标 |
| `FirstHopOnly` | 只走第一跳：本机 → 第一跳 → 目标（适合大部分代理流量） |
| `DIRECT` | 直连，不走任何代理 |
| `REJECT` | 拒绝（用于广告 / 隐私域名） |

### TUN 模式

- 默认是**系统代理模式**（设置 macOS 的 SOCKS5 / HTTP 代理）。优点：不用密码、卸载干净；缺点：只能接管"会读系统代理"的应用
- **TUN 模式**会在系统里建一个虚拟网卡接管所有 IPv4 流量，包括 Telegram / 游戏 / 任何不读系统代理的程序。第一次开启会要管理员密码（装一个 sudoers 免密助手），之后再开 TUN 不需要密码
- ⚠️ **务必关闭机场客户端自带的 TUN**——两个 TUN 同时开会路由打架
- ⚠️ TUN 模式下要在 `config.json` 里把你机场客户端的所有进程名加到 `first_hop_process_names`，否则会出现回环（机场自己的拨号被 TUN 抓回来 → 转给自己 → 又被抓 → 超时）。每种机场客户端的进程名不一样，常见的：
  - ClashX：`["ClashX", "ClashX Pro"]`
  - Karing：`["Karing", "sing-box"]`
  - FastLink：`["FastLink机场", "AtlasCore_arm64", "AtlasCore_amd64"]`
  - 自建 mihomo：`["mihomo"]`
  - 不确定就在 Activity Monitor 里看你的客户端在跑哪些进程
- 如果断网或者出问题，回**概览**页点**网络急救**：一键清系统代理 + 杀 mihomo + 删 TUN 路由

## 配置文件

GUI 的所有改动都写在 `~/Library/Application Support/ChainProxy/config.json`。你也可以直接编辑它，停掉再启动 ChainProxy 就生效。

仓库根目录的 [`config.example.json`](config.example.json) 是模板（节点字段是占位符，必须填进自己的真实值）。复制过去再改：

```bash
mkdir -p ~/Library/Application\ Support/ChainProxy
cp config.example.json ~/Library/Application\ Support/ChainProxy/config.json
# 然后用 GUI 修改节点信息
```

## 文件 / 目录结构

```
ChainProxy/
├── chainproxy_qt.py            ← GUI（PyQt6）
├── chainproxy_core.py          ← 后端：mihomo runner / config / 系统代理 / TUN 助手
├── config.example.json         ← 示例配置（节点字段是占位符，规则示例）
├── icon.png                    ← README 用的 PNG（GitHub 不能渲染 .icns）
├── ChainProxy.app/             ← .app 骨架（Info.plist + 启动器 + 图标）
│   └── Contents/{Info.plist,MacOS/ChainProxy,Resources/ChainProxy.icns}
└── scripts/
    ├── build.sh                ← 把 .py 拷进 .app 打包成自包含 bundle
    ├── make_dmg.sh             ← 打成可拖装的 .dmg
    └── make_icon.py            ← 重新生成 .icns + icon.png
```

## 数据 / 文件位置

- 配置：`~/Library/Application Support/ChainProxy/config.json`
- mihomo 运行时：`~/Library/Application Support/ChainProxy/runtime/`
- 规则集缓存：`~/Library/Application Support/ChainProxy/runtime/ruleset/`
- 日志：`~/Library/Application Support/ChainProxy/runtime/{app.log,mihomo.log}`
- TUN 模式的 sudoers 免密脚本（系统级，安装一次后留下）：
  - `/usr/local/bin/chainproxy-helper.sh`
  - `/etc/sudoers.d/chainproxy`

## 卸载

```bash
rm -rf /Applications/ChainProxy.app
rm -rf ~/Library/Application\ Support/ChainProxy
sudo rm -f /usr/local/bin/chainproxy-helper.sh /etc/sudoers.d/chainproxy
```

## 协议

[MIT](LICENSE)

## 致谢

- [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo) — 提供链式代理的内核
- [Loyalsoldier/clash-rules](https://github.com/Loyalsoldier/clash-rules) — 默认内置的中国大陆分流规则集
- PyQt6 / Qt
