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
 ┌─ 本机 ──────┐    ┌───── 第一跳 ─────┐    ┌──── 第二跳 ────┐    ┌─ 目标 ─┐
 │  App 流量    │ ──▶│  机场客户端的     │ ──▶│  你自己的节点    │ ──▶│ 互联网 │
 │             │    │  本地 SOCKS5     │    │  trojan / ss /…  │    │       │
 └─────────────┘    └─────────────────┘    └────────────────┘    └───────┘
                            ▲                       ▲
                            │                       │
                       由 mihomo 用 dialer-proxy 串起来，分流策略由你指定
```

第一跳是**任何在 `127.0.0.1:某端口` 暴露 SOCKS5 的机场客户端**——ClashX、V2RayX、Karing、FastLink、Stash、自建 mihomo 等等都行。第二跳是**你自己的 VPS 或干净节点**，协议支持 socks5 / http / trojan / ss / vmess / hysteria2。

## 主要功能

- **一键启停链式代理**，状态、链路可视化，TUN / 系统代理两种模式可选
- **「识别本机机场客户端进程」按钮**：TUN 模式下自动找出你机场客户端的所有进程名加进白名单，不用手动查任务管理器
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
bash scripts/make_dmg.sh 1.1.9      # 出 .dmg
```

**Windows：**
```powershell
git clone https://github.com/Laogeyouge/ChainProxy.git
cd ChainProxy
py -m pip install --user PyQt6 pywin32 Pillow pyinstaller

py chainproxy_qt.py                 # 直接跑（要求自己装 mihomo）

# 或一键打 installer.exe（含 mihomo 自动下载）
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
# → dist\ChainProxy-Setup-1.1.9.exe
```
打 installer 还需要 [Inno Setup 6](https://jrsoftware.org/isdl.php)（`winget install JRSoftware.InnoSetup`）。如果未装，构建脚本只生成 `dist\ChainProxy\ChainProxy.exe`（绿色版），不打 installer。

---

## 使用说明

> ⚠️ **机场客户端必须保持后台运行**。ChainProxy 不替代你的机场客户端——它只是把第一跳流量丢给那个本地 SOCKS5。所以**只要 ChainProxy 在跑，机场客户端就必须开着**。关闭机场客户端 = 第一跳挂掉 = ChainProxy 整条链路断。开机自启 / 系统托盘里常驻一个就行。

### 第一次使用：5 分钟跑通整条链路

#### 第 0 步：先把第一跳的"原料"准备好

打开你的机场客户端（FastLink / Karing / Clash Verge / V2RayX / 自建 mihomo 等都行），在客户端的设置里：

- **关掉它自己的"系统代理"开关**——这个由 ChainProxy 来管
- **关掉它自己的"TUN 模式"开关**——TUN 也由 ChainProxy 来管
- **打开"本地 SOCKS5"或"混合端口"**，记下端口号（很多客户端默认是 `7891`、`1080`、`6666`，每家不一样，看客户端界面或日志）

最终你需要的是：机场客户端在 `127.0.0.1:<某个端口>` 上提供一个 SOCKS5。**测试一下**：打开浏览器访问任何境外站点（直连访问不到的）通过这个 SOCKS5 能不能上 → 能上才能继续。

如果你的机场客户端**只支持系统代理或 TUN，没有独立 SOCKS5 选项**，ChainProxy 就用不了——必须有 SOCKS5 出口才能当第一跳。

#### 第 1 步：填第一跳

打开 ChainProxy → 左侧切到「**节点**」页 → 顶部「**第一跳**」区域：

- 点「**+**」新建一个第一跳
- **协议**：选 `socks5`
- **服务器**：`127.0.0.1`
- **端口**：填刚才记下来的那个端口号（例如 `7891`）
- **名称**：随便起，建议用机场名字（例如 `FastLink-本地`）

填完点保存。这条会出现在第一跳列表里，左侧蓝点表示当前激活。

#### 第 2 步：填第二跳

切到「**节点**」页下半部分「**第二跳**」区域：

- 点「**+**」新建第二跳，按节点信息填：
  - **协议**：socks5 / http / trojan / ss / vmess / hysteria2 都支持
  - **服务器 / 端口 / 密码 / UUID** 等：你 VPS 提供商或机场订阅给的那一套
- 一个第二跳节点建一条，可以建多条，左侧蓝点决定当前激活哪条

> 如果你**只想用机场（不要二跳）**，可以不填第二跳——把分流规则的"最终匹配目标"设为 `FirstHopOnly` 即可（见下文）。

#### 第 3 步：分流规则

切到「**分流规则**」页。默认状态：

| 规则集 | 默认目标 | 说明 |
|---|---|---|
| `reject` | REJECT | 广告 / 隐私 / 恶意域名（拦截）|
| `private` / `applications` / `direct` / `cncidr` / `lancidr` | DIRECT | 内网、国内网站、国内 IP 段（直连）|
| `proxy` / `gfw` / `google` / `tld-not-cn` / `telegramcidr` | FirstHopOnly | 需要科学上网的（默认走第一跳）|

**就这样不动也能用** —— 默认行为是国内直连、境外走机场（第一跳）。

如果想让某些域名**走完整链路（机场 → 二跳 → 目标）**：

- 滚到页面底部「**自定义规则前置**」
- 加一行，例如要让 OpenAI 走二跳：

  ```
  DOMAIN-SUFFIX,openai.com,Chain
  DOMAIN-SUFFIX,anthropic.com,Chain
  DOMAIN-SUFFIX,googleai.com,Chain
  ```

- 改完会自动保存，1.5 秒去抖后自动重启 mihomo

> 「前置」 vs 「后置」：前置规则在所有 Loyalsoldier 规则集**之前**匹配（优先级最高），后置在**之后**匹配（兜底）。一般想让某个域名"必走某条链路"用前置；想做"其他都直连，唯独这些走代理"用后置。

#### 第 4 步：启动

回「**概览**」页 → 点 **启动** → 顶部状态变成绿色「运行中」即成功。

ChainProxy 默认会同时把系统代理设为 `127.0.0.1:7890`，所以浏览器、curl 等读系统代理的程序就被自动接管了。

#### 第 5 步：验证

切到「**测试**」页，输入一个网址（例如 `https://www.google.com` 或 `https://chat.openai.com`），点测试 → 会显示：

- 命中了哪条规则
- 最终从哪个节点出去
- HTTP 状态码、响应时间

如果显示走了 `Chain`（链式）或 `FirstHopOnly`（一跳）且 200 OK——成功。

---

### 五个页面分别在做什么

#### 1. 概览（⌘1）

- **启动 / 停止**按钮（也可用 ⌘R）
- **当前模式**：系统代理 / TUN
- **链路状态**：当前激活的第一跳 / 第二跳节点信息
- **mihomo 日志**：实时刷新，方便看连接情况
- **网络急救按钮**：见下文「网络急救」段
- 顶部右侧的「**系统代理**」 / 「**TUN 模式**」开关——可以热切换，无需重启

#### 2. 节点（⌘2）

第一跳和第二跳分两个区域，每个区域：

- **新增 / 复制 / 改名 / 删除** 节点
- 左边小蓝点表示当前激活的节点（点击切换）
- 双击节点名进入编辑

第一跳区域**多一个**「**识别本机机场客户端进程**」按钮——见下文「TUN 模式」段。

#### 3. 分流规则（⌘3）

从上到下：

- **规则集列表**：13 个默认 Loyalsoldier 规则集，每行可点开关、切换目标
- **自定义规则前置**：放在所有规则集之前，最高优先级
- **自定义规则后置**：放在所有规则集之后，兜底优先级
- **最终匹配目标（MATCH）**：上面所有规则都没命中时走哪——`FirstHopOnly`（推荐）/ `Chain` / `DIRECT`
- **规则总开关**：关掉等于把所有流量都丢给"最终匹配目标"，相当于全局走那一条

#### 4. 设置（⌘4）

- **本地端口**：mihomo 在本机监听的 SOCKS5/HTTP 端口（默认 `7890`）
- **控制器端口 / 密钥**：mihomo HTTP API 监听口，规则测试要用，一般不用改
- **启动时自动开系统代理**：勾上后，每次「启动」自动写系统代理；停止时自动清掉
- **TUN 模式**：见下文专门一节

#### 5. 测试（⌘5）

- 输入 URL → 发一个 GET 请求 → 显示命中规则、出口节点、状态码、耗时
- 用来快速验证：某个域名是否走了你期望的那条链路
- 快捷键 ⌘T 直接聚焦输入框

---

### TUN 模式详解

**默认是系统代理模式**（macOS `networksetup` / Windows 写注册表）。优点：启停干净、Windows 上无 UAC；缺点：只能接管"会读系统代理"的应用——很多游戏、Telegram 桌面、各种命令行工具不读系统代理。

**TUN 模式**会建一个虚拟网卡接管所有 IPv4 流量，不管程序读不读系统代理：

| 平台 | TUN 启动行为 |
|---|---|
| macOS | 第一次开启输一次管理员密码，装一个 sudoers 免密助手；之后再开 TUN 永不弹密码 |
| Windows | 每次启动 ChainProxy（仅当 `tun_mode=true`）弹一次 UAC，会话内启停 mihomo 不再弹 |

#### 第一跳进程白名单（TUN 模式必填）

TUN 模式有个绕不过的坑：**机场客户端自己的拨号也会被你的 TUN 抓回来**——它想发包到 `1.2.3.4:443` → 你的 TUN 截住了 → 转给 mihomo → mihomo 又把它送回机场客户端 → 死循环 → 最终 `context deadline exceeded`。

解决办法：把机场客户端的所有进程名告诉 mihomo，让它**直连不走 TUN**。这就是 `first_hop_process_names` 字段的作用。

**1.1.9 起：「识别本机机场客户端进程」按钮自动填**

在「节点」页第一跳右上角点这个按钮：

- ChainProxy 用 `netstat` 找出**你第一跳端口（如 `127.0.0.1:6666`）正在监听的进程**
- 走到那个进程所在的 `.app` bundle（macOS）或安装目录（Windows）
- 把这个 bundle 里**所有的进程名**自动加进白名单

例如你第一跳填了 `127.0.0.1:6666`，FastLink 在监听这个端口：
- 检测到 PID 26725 = `/Applications/FastLink机场.app/Contents/Resources/AtlasCore_arm64`
- 走到 `/Applications/FastLink机场.app`
- 自动把 `FastLink机场` 和 `AtlasCore_arm64` 都加进白名单

机器上**同时跑着 Karing、Clash Verge、猫猫云**也不影响——按钮只识别你**当前第一跳那个端口**对应的客户端，其他不会被错误拉进来。

#### 手动填（备用）

如果按钮识别不准，去 `config.json` 直接编辑 `first_hop_process_names`：

```json
"first_hop_process_names": ["FastLink机场", "AtlasCore_arm64"]
```

常见示例：

| 平台 | 客户端 | 推荐填 |
|---|---|---|
| macOS | ClashX | `["ClashX", "ClashX Pro"]` |
| macOS | Karing | `["Karing"]` |
| macOS | FastLink | `["FastLink机场", "AtlasCore_arm64", "AtlasCore_amd64"]` |
| macOS | Clash Verge | `["clash-verge", "verge-mihomo", "clash-verge-service"]` |
| macOS | 猫猫云 | `["猫猫云", "CatCore"]` |
| macOS | 自建 mihomo | `["mihomo"]` |
| Windows | FastLink | `["flclient.exe", "AtlasCore_amd64.exe"]` |
| Windows | Clash Verge | `["clash-verge.exe", "verge-mihomo.exe"]` |
| Windows | 自建 mihomo | `["mihomo.exe"]` |

> Windows 进程名**必须带 `.exe`**。不确定就在任务管理器里看你客户端在跑哪些进程。

#### TUN 冲突

⚠️ **务必关闭机场客户端自带的 TUN**——两个 TUN 同时开会路由打架，断网且很难恢复。

ChainProxy 在启动 TUN 时会**预检**，如果检测到另一个 TUN 占用了 `198.18.0.1` 网关会直接拒绝启动。看到这种错误就关掉机场客户端的 TUN，再启动 ChainProxy。

---

### 分流规则编辑

#### 目标取值

| 目标 | 含义 |
|---|---|
| `Chain` | 走完整链路：本机 → 第一跳 → 第二跳 → 目标 |
| `FirstHopOnly` | 只走第一跳：本机 → 第一跳 → 目标（适合大部分代理流量）|
| `DIRECT` | 直连，不走任何代理 |
| `REJECT` | 拒绝（用于广告 / 隐私域名）|

#### 自定义规则语法

完全是 mihomo / clash 标准语法，每行：`<TYPE>,<匹配值>,<目标>`

| 类型 | 例子 | 说明 |
|---|---|---|
| `DOMAIN` | `DOMAIN,openai.com,Chain` | 精确域名 |
| `DOMAIN-SUFFIX` | `DOMAIN-SUFFIX,openai.com,Chain` | 域名及所有子域 |
| `DOMAIN-KEYWORD` | `DOMAIN-KEYWORD,openai,Chain` | 域名包含关键字 |
| `IP-CIDR` | `IP-CIDR,1.1.1.1/32,DIRECT` | IPv4 网段 |
| `IP-CIDR6` | `IP-CIDR6,2606:4700::/32,DIRECT` | IPv6 网段 |
| `GEOIP` | `GEOIP,CN,DIRECT` | 国家代码 |
| `PROCESS-NAME` | `PROCESS-NAME,Telegram,Chain` | 进程名（仅 TUN 模式有效）|
| `DST-PORT` | `DST-PORT,80,DIRECT` | 目标端口 |
| `MATCH` | （由"最终匹配目标"控制，自定义里别写）| 兜底 |

#### 实战例子

**例 1：让 AI 服务走二跳干净 IP**

自定义规则前置：
```
DOMAIN-SUFFIX,openai.com,Chain
DOMAIN-SUFFIX,anthropic.com,Chain
DOMAIN-SUFFIX,claude.ai,Chain
DOMAIN-SUFFIX,googleai.com,Chain
DOMAIN-SUFFIX,bard.google.com,Chain
```

**例 2：某网站直连不走代理（即使匹配了机场规则也直连）**

自定义规则前置：
```
DOMAIN-SUFFIX,bilibili.com,DIRECT
DOMAIN-SUFFIX,zhihu.com,DIRECT
```

**例 3：某 App 的所有流量强制走二跳（TUN 模式）**

自定义规则前置：
```
PROCESS-NAME,Telegram,Chain
PROCESS-NAME,Telegram.exe,Chain
```

---

### 网络急救

代理出问题、断网、TUN 残留、改了配置后某些 App 出不去网……回**概览**页点「**网络急救**」按钮，会一键执行：

- 关闭系统代理（macOS `networksetup -setwebproxystate ... off` / Windows 改注册表）
- 杀掉所有残留 mihomo 进程
- 删除 TUN 路由（`198.18.0.1` 网关相关）
- 关闭 utun 接口
- 刷 DNS 缓存

执行完通常网络立刻恢复。如果还不行，重启网卡或重启电脑。

---

### 配置文件

GUI 的所有改动都写在 `config.json`。你也可以直接编辑，停掉再启动 ChainProxy 就生效。

| 平台 | 路径 |
|---|---|
| macOS | `~/Library/Application Support/ChainProxy/config.json` |
| Windows | `%APPDATA%\ChainProxy\config.json` |

仓库根目录的 [`config.example.json`](config.example.json) 是模板，节点字段是占位符，必须填进自己的真实值。

---

## 常见问题（FAQ）

### Q: 启动后浏览器还是连不上外网

按这个顺序排查：

1. **机场客户端开着吗** → 没开就开
2. **机场客户端的本地 SOCKS5 端口能用吗** → 浏览器配代理 `socks5://127.0.0.1:<端口>` 直接试，不能用就是机场客户端的问题
3. **节点页第一跳填的端口对吗** → 拼写、协议、端口号都对一遍
4. **概览页状态是否绿色「运行中」** → 不是的话看日志报什么错
5. **是否漏开了系统代理** → 设置页或顶部右上的「系统代理」开关
6. **试浏览器开无痕窗口或换浏览器** → 排除浏览器扩展干扰

### Q: 日志一直打 `context deadline exceeded`

99% 是 **TUN 模式没填进程白名单**导致的回环。点「识别本机机场客户端进程」按钮自动填，或参考上文「TUN 模式」段手动填 `first_hop_process_names`。

### Q: 启动 TUN 报「检测到另一个 TUN 占用 198.18.0.1」

机场客户端自己的 TUN 还开着，关掉它。或者上次 mihomo 异常退出留了残留路由——点「网络急救」清一下，再重启 ChainProxy。

### Q: 二跳节点配上去测试不通

- 在「测试」页填一个明确走二跳的域名（自定义前置加 `DOMAIN-SUFFIX,httpbin.org,Chain`，再测 `https://httpbin.org/ip`），看返回的 IP 是不是你二跳 VPS 的
- 如果连接超时：检查二跳节点的协议、端口、密码、UUID 一个个再核对。trojan/vmess 还要看 SNI / WS path / TLS 这些
- 直接用其他客户端（V2RayN / Stash）测一下你的二跳节点能不能单独工作；不能就是节点本身的问题

### Q: 改了规则没生效

正常情况下改完 1.5 秒会自动重启 mihomo。如果显示状态还是老的：

- 等 5 秒看看（去抖 + 重启需要时间）
- 还不行去概览页停止 → 启动一次
- 极端情况：网络急救 → 启动

### Q: 第一跳端口填错了想改名字 / 端口

直接在节点列表上双击改，自动保存自动重启 mihomo。**1.1.8 之前**改完节点名字 URL 测试可能还显示老名字——那是 bug，1.1.8+ 已修。

### Q: 「识别本机机场客户端进程」按钮报"端口 XXX 没有监听进程"

- 你的机场客户端没开
- 或者机场客户端的 SOCKS5 端口不是你以为的那个——去客户端界面再确认
- 或者机场客户端只暴露了 HTTP 代理没有 SOCKS5——按钮只能识 SOCKS5 listener

### Q: macOS 上每次启动都要输管理员密码

1.1.8+ 已修了 helper 版本号死循环 bug。如果你从更老版本升级上来一直有这问题，手动清一下：

```bash
sudo rm -f /usr/local/bin/chainproxy-helper.sh /etc/sudoers.d/chainproxy
```

下次启动 TUN 会重新装，输一次密码后永远免密。

### Q: 想让 TUN 模式排除某个 App（让它直连不走代理）

`config.json` 的 `first_hop_process_names` 不仅给机场客户端用，**任何想绕开 TUN 的进程都可以加进去**。例如让微信不被 TUN 接管：

```json
"first_hop_process_names": ["FastLink机场", "AtlasCore_arm64", "WeChat"]
```

### Q: 想用 ChainProxy 但不想要二跳，直接当机场客户端的"系统代理外壳"

完全可以。
- 第二跳留空（不填或不激活任何节点）
- 分流规则页 → 「最终匹配目标」选 `FirstHopOnly`
- 启动后所有需要代理的流量都只走第一跳

这样能享受到 ChainProxy 的：分流规则、TUN、网络急救、规则测试，但不做二跳。

---

## 数据 / 文件位置

**macOS：**
- 配置：`~/Library/Application Support/ChainProxy/config.json`
- 运行时：`~/Library/Application Support/ChainProxy/runtime/`（含 mihomo.yaml、mihomo.log、规则集）
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
