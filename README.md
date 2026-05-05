# ChainProxy

> 跨平台（macOS / Windows）的链式代理 GUI：本机 → 第一跳 → 第二跳 → 目标。
> 基于 [mihomo](https://github.com/MetaCubeX/mihomo) 内核，PyQt6 写的原生外观。

<p align="center">
  <img src="icon.png" width="120" alt="ChainProxy 图标">
</p>

## 这是什么

很多机场客户端只能让你出一跳。ChainProxy 让你在**已经能用的第一跳**基础上，把指定流量再串到**第二跳**节点上去——常见用途：

- **机场强制使用自家客户端**：你订阅的机场不开放订阅 URL，或者开放了订阅但只有用它自家客户端连才稳定（自家客户端做了私有协议 / 路由优化）。这种机场没法直接接进 mihomo / clash。**ChainProxy 把它原地用作第一跳**——机场客户端继续在你机器上跑（提供它的本地 SOCKS5），ChainProxy 在外面再叠一层链式代理，把指定域名导到你的二跳干净节点。
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

## 安装

### Windows（推荐：installer）

1. 去 [Releases](../../releases) 下载最新版 `ChainProxy-Setup-x.y.z.exe`
2. 双击运行——可以选安装路径，可勾选"创建桌面快捷方式"
3. 安装完成后从开始菜单或桌面启动 ChainProxy
4. 安装包**自带 mihomo.exe 内核**，无需额外下载

> Windows TUN 模式下首次启动会弹一次 UAC（仅一次），之后启停 mihomo 都不再弹。如果只是给浏览器代理，用默认的"系统代理模式"则**完全不弹 UAC**。

需求：Windows 10 21H2 或更新（x64）。

### macOS（推荐：dmg）

1. 去 [Releases](../../releases) 下载最新版 `ChainProxy-x.y.z.dmg`
2. 双击挂载，把 ChainProxy.app 拖到 Applications
3. 装运行时依赖（**两个都要**：mihomo 是代理内核，PyQt6 是 GUI）：
   ```bash
   brew install mihomo python   # python 没装过的话顺手装上
   pip3 install --break-system-packages PyQt6
   ```
4. **首次启动**：在 Finder 里**右键 ChainProxy.app → 打开**（双击会被 Gatekeeper 拦——dmg 没做苹果代码签名，所以新 App 都得这一步。之后从 Spotlight / Dock 就能直接开）

需求：macOS 11 (Big Sur) 或更新，Python 3.9+。

#### 装 PyQt6 时报 "externally-managed-environment"？

新版 Homebrew Python 默认不让 `pip3 install` 直接装到全局，三选一：

```bash
# 方法 A（最简单，README 上面的命令就是这个）：加 --break-system-packages
pip3 install --break-system-packages PyQt6

# 方法 B：用 pipx（每个包隔离的 venv）
brew install pipx && pipx install PyQt6

# 方法 C：用 conda
conda install -c conda-forge pyqt
```

ChainProxy 的启动器会从 PATH / Homebrew / Miniconda / Anaconda / pyenv / Python.org installer 里**自动挑一个装了 PyQt6 的 Python**——只要任意一个 Python 装了 PyQt6 就能启动。

### 从源码运行 / 自己打包

**macOS：**
```bash
git clone https://github.com/Laogeyouge/ChainProxy.git
cd ChainProxy
brew install mihomo
pip3 install --break-system-packages PyQt6

python3 chainproxy_qt.py            # 直接跑
bash scripts/build.sh               # 打包到 ChainProxy.app
bash scripts/make_dmg.sh 1.1.0      # 出 .dmg
```

**Windows：**
```powershell
git clone https://github.com/Laogeyouge/ChainProxy.git
cd ChainProxy
py -m pip install --user PyQt6 pywin32 Pillow pyinstaller

py chainproxy_qt.py                 # 直接跑（要求自己装 mihomo）

# 或一键打 installer.exe（含 mihomo 自动下载）
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
# → dist\ChainProxy-Setup-1.1.0.exe
```
打 installer 还需要 [Inno Setup 6](https://jrsoftware.org/isdl.php)（`winget install JRSoftware.InnoSetup`）。如果未装，构建脚本只生成 `dist\ChainProxy\ChainProxy.exe`（绿色版），不打 installer。

## 使用

> ⚠️ **机场客户端必须保持后台运行**。ChainProxy 不替代你的机场客户端——它把第一跳的流量丢给那个本地 SOCKS5，所以**只要 ChainProxy 在跑，机场客户端就必须开着**。关闭机场客户端 = 第一跳挂掉 = ChainProxy 整条链路断。开机自启 / 系统托盘里常驻一个就行。

1. **打开你的机场客户端**并保持在后台运行（任何能输出 SOCKS5 的都行：ClashX / V2RayX / Karing / Stash / FastLink / 自建 mihomo …），关掉它自己的"系统代理"和"TUN"开关，只让它在 `127.0.0.1:某端口` 暴露一个 SOCKS5
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

- 默认是**系统代理模式**（macOS 用 networksetup / Windows 写注册表设系统代理）。优点：启停干净、Windows 上无 UAC；缺点：只能接管"会读系统代理"的应用
- **TUN 模式**会建一个虚拟网卡接管所有 IPv4 流量，包括 Telegram / 游戏 / 任何不读系统代理的程序：
  - **macOS**：第一次开启输一次管理员密码，装一个 sudoers 免密助手，之后再开 TUN 永不弹密码
  - **Windows**：每次启动 ChainProxy（仅当 `tun_mode=true`）会弹一次 UAC，**这次会话内**启停 mihomo 不再弹 UAC
- ⚠️ **务必关闭机场客户端自带的 TUN**——两个 TUN 同时开会路由打架
- ⚠️ TUN 模式下必须在 `config.json` 的 `first_hop_process_names` 里填上你机场客户端的所有进程名，否则会回环（机场自己的拨号被 TUN 抓回来 → 转给自己 → 超时）。常见示例：
  - macOS · ClashX：`["ClashX", "ClashX Pro"]`
  - macOS · Karing：`["Karing", "sing-box"]`
  - macOS · FastLink：`["FastLink机场", "AtlasCore_arm64", "AtlasCore_amd64"]`
  - Windows · FastLink：`["flclient.exe", "AtlasCore_amd64.exe"]`（**Windows 进程名必须带 .exe**）
  - 自建 mihomo：`["mihomo"]` / `["mihomo.exe"]`
  - 不确定就在活动监视器（mac）/ 任务管理器（win）里看你客户端在跑哪些进程
- 如果断网或出问题，回**概览**页点**网络急救**：一键清系统代理 + 杀 mihomo + 清 TUN 残留

## 配置文件

GUI 的所有改动都写在 `config.json`。你也可以直接编辑，停掉再启动 ChainProxy 就生效。

| 平台 | 路径 |
|:--|:--|
| macOS | `~/Library/Application Support/ChainProxy/config.json` |
| Windows | `%APPDATA%\ChainProxy\config.json` |

仓库根目录的 [`config.example.json`](config.example.json) 是模板，节点字段是占位符，必须填进自己的真实值。

## 数据 / 文件位置

**macOS：**
- 配置：`~/Library/Application Support/ChainProxy/config.json`
- 运行时：`~/Library/Application Support/ChainProxy/runtime/`
- TUN sudoers 助手（一次安装、长期留下）：`/usr/local/bin/chainproxy-helper.sh` + `/etc/sudoers.d/chainproxy`

**Windows：**
- 配置：`%APPDATA%\ChainProxy\config.json`
- 运行时：`%APPDATA%\ChainProxy\runtime\`
- 内嵌 mihomo.exe：跟 ChainProxy.exe 同目录（installer 放在你选的安装路径，一般是 `C:\Program Files\ChainProxy\` 或 `%LOCALAPPDATA%\Programs\ChainProxy\`）

## 卸载

**macOS：**
```bash
rm -rf /Applications/ChainProxy.app
rm -rf ~/Library/Application\ Support/ChainProxy
sudo rm -f /usr/local/bin/chainproxy-helper.sh /etc/sudoers.d/chainproxy
```

**Windows：** 控制面板 → 应用 → ChainProxy → 卸载。配置文件 `%APPDATA%\ChainProxy\` 默认保留（你的节点和规则不会丢）；如果要彻底清掉，手动删它即可。

## 协议

[MIT](LICENSE)

## 致谢

- [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo) — 提供链式代理的内核
- [Loyalsoldier/clash-rules](https://github.com/Loyalsoldier/clash-rules) — 默认内置的中国大陆分流规则集
- PyQt6 / Qt
