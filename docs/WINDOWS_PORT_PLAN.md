# Windows 移植计划

> 用户在考虑做 Windows 版。这是 macOS Claude 给出的评估和实施路线。Windows 上接手的 Claude 应该先和用户确认是否动工。

## TL;DR

- ✅ GUI（PyQt6）100% 可移植，Windows 上长得跟 macOS 一模一样
- ❌ TUN 模式没办法做"输一次密码后免密"——Windows UAC 不允许
- ⚠️ 后端 (`chainproxy_core.py`) 所有跟 OS 交互的代码要全部重写
- 🚫 没有 Windows 环境就无法端到端测试（macOS 上跑不动 mihomo Windows 二进制）

## 必须重写的部分（platform-specific）

| 功能 | macOS 现在 | Windows 替换 |
|---|---|---|
| 系统代理设置 | `networksetup -setsocksfirewallproxy` | 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings\ProxyEnable, ProxyServer` + `netsh winhttp set proxy` |
| 网络服务名 | `networksetup -listnetworkserviceorder` | 不需要——Windows 系统代理是全局的，不分 service |
| 默认网卡探测 | `route -n get default` | `Get-NetRoute -DestinationPrefix 0.0.0.0/0` 或 `route print` |
| TUN 接管 | mihomo 内置 + sudoers 助手 | mihomo + WinTun 驱动（mihomo Windows 版打包了） + UAC 提权 |
| 提权方式 | `osascript do shell script with admin` 一次 + sudoers 免密 | **每次启动都要 UAC 弹窗**——无解，Windows 设计就是这样 |
| 进程组管理 | `os.killpg / setsid / SIGTERM` | `subprocess.CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F /PID ...` |
| 单实例锁 | `fcntl.flock` | `msvcrt.locking` 或 `win32event.CreateMutex` (named mutex) |
| 激活已运行窗口 | `osascript "tell application X to activate"` | `win32gui.FindWindow + SetForegroundWindow` 或 PyQt 的 `QSharedMemory` |
| 配置目录 | `~/Library/Application Support/ChainProxy/` | `%APPDATA%\ChainProxy\` （即 `os.environ['APPDATA']`） |
| 日志/运行时目录 | 同上 | 同上 |
| 应用打包 | `.app` bundle + `.dmg` | PyInstaller `--onefile` 或 `--onedir` → `.exe` + Inno Setup 脚本 → `installer.exe` |
| 图标格式 | `.icns` | `.ico` |
| 启动器 | bash | 不需要——PyInstaller 直接产出 `.exe` |

## GUI 不需要改的部分

PyQt6 的 QSS / 控件 / 信号槽 / QPainter 在 Windows 上行为一致。`chainproxy_qt.py` 里**唯一可能出问题**的几处：

- `Qt.ColorScheme` 检测（Windows 11 可以用，Win10 不一定。需要 fallback 到读 `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\AppsUseLightTheme`）
- 字体 `.AppleSystemUIFont` 在 Windows 上不存在——回落到 `Segoe UI`（Qt 的 fontFamily fallback 一般会自动处理，但显式列出更稳：`".AppleSystemUIFont", "Segoe UI", "Microsoft YaHei UI"`）
- `Menlo` 等宽字体 Windows 没有，加 `Consolas` fallback
- 窗口尺寸/DPI——Windows 高 DPI 缩放不同，可能要测一下
- 系统快捷键 `⌘` 在 Windows 显示为 `Ctrl`——已经用 `QKeySequence("Ctrl+1")` 而不是 `Cmd+1`，跨平台正常

## 实施步骤

### Step 1：拆 core（在 macOS 上做也行）

把 `chainproxy_core.py` 拆成：

```
core/
├── __init__.py             ← 暴露统一 API；按 sys.platform 选择 backend
├── common.py               ← 平台无关：YAML 生成、规则集下载、配置 schema、URL 测试
├── platform_macos.py       ← macOS 专属：set_system_proxy / find_default_iface / helper / mihomo runner / panic_recover / single_instance_lock
└── platform_windows.py     ← Windows 实现，签名跟 macos 那个完全对齐
```

`__init__.py` 大致：

```python
import sys
from .common import (
    APP_NAME, PROTOCOLS, RULE_TARGETS, DEFAULT_CONFIG, DEFAULT_RULE_SETS,
    load_config, save_config, build_mihomo_yaml, test_url_through_proxy,
    download_rule_set, update_all_rule_sets, rule_set_local_path_exists,
    find_mihomo, tcp_reachable,
)
if sys.platform == "darwin":
    from .platform_macos import (
        SUPPORT_DIR, RUNTIME_DIR, MIHOMO_YAML, MIHOMO_LOG, RULESET_DIR,
        CONFIG_PATH, set_system_proxy, panic_recover, MihomoRunner,
        acquire_single_instance_lock, activate_existing_window,
    )
elif sys.platform == "win32":
    from .platform_windows import (
        SUPPORT_DIR, RUNTIME_DIR, MIHOMO_YAML, MIHOMO_LOG, RULESET_DIR,
        CONFIG_PATH, set_system_proxy, panic_recover, MihomoRunner,
        acquire_single_instance_lock, activate_existing_window,
    )
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")
```

GUI 那边 `import chainproxy_core as core` 完全不变，只是 import 进来的实现按平台分发。

### Step 2：写 `platform_windows.py`

参考 `platform_macos.py` 一项一项实现：

#### 系统代理

```python
import winreg

def set_system_proxy(port: int, enable: bool):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as k:
            if enable:
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{port}")
                # 排除本地地址
                winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, "<local>")
            else:
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        # Notify other apps that proxy settings changed
        import ctypes
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        return True, "registry"
    except OSError as e:
        return False, str(e)
```

#### 配置/运行时路径

```python
import os
from pathlib import Path

APP_NAME = "ChainProxy"
SUPPORT_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / APP_NAME
CONFIG_PATH = SUPPORT_DIR / "config.json"
RUNTIME_DIR = SUPPORT_DIR / "runtime"
MIHOMO_YAML = RUNTIME_DIR / "config.yaml"
MIHOMO_LOG = RUNTIME_DIR / "mihomo.log"
RULESET_DIR = RUNTIME_DIR / "ruleset"
```

#### MihomoRunner

```python
import subprocess, signal

class MihomoRunner:
    def __init__(self, log_cb):
        self.proc = None
        self.log_cb = log_cb

    def start(self, mihomo_bin, use_sudo=False):
        # use_sudo on Windows means "elevate via UAC for TUN mode"
        if use_sudo:
            self._start_elevated(mihomo_bin)
        else:
            self.proc = subprocess.Popen(
                [mihomo_bin, "-d", str(RUNTIME_DIR), "-f", str(MIHOMO_YAML)],
                stdout=open(MIHOMO_LOG, "a"), stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                              | subprocess.CREATE_NO_WINDOW,
            )

    def _start_elevated(self, mihomo_bin):
        # Use ShellExecute with "runas" verb to trigger UAC
        import ctypes
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", mihomo_bin,
            f'-d "{RUNTIME_DIR}" -f "{MIHOMO_YAML}"',
            None, 0,  # SW_HIDE
        )
        if ret <= 32:
            raise RuntimeError(f"UAC elevation failed: {ret}")
        # ShellExecuteW doesn't give us the PID — we have to find it
        # by name + recent start time
        # ... (此处需要一些工程细节)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        # Also kill any orphan mihomo with our config dir (taskkill /FI)
        subprocess.run(
            ["taskkill", "/F", "/FI", f"WINDOWTITLE eq mihomo*"],
            capture_output=True,
        )
```

#### 单实例锁

```python
def acquire_single_instance_lock():
    import win32event, win32api, winerror
    mutex = win32event.CreateMutex(None, False, "ChainProxyGUISingleInstance")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return None
    return mutex  # 进程退出时自动释放

def activate_existing_window():
    import win32gui
    hwnd = win32gui.FindWindow(None, "ChainProxy")
    if hwnd:
        win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
        win32gui.SetForegroundWindow(hwnd)
```

#### 网络急救 (`panic_recover`)

```python
def panic_recover(log_cb):
    log_cb("=== 网络急救 ===")
    set_system_proxy(0, enable=False)
    log_cb("  系统代理已清")
    # 杀残留 mihomo
    subprocess.run(["taskkill", "/F", "/IM", "mihomo.exe"], capture_output=True)
    log_cb("  已杀残留 mihomo")
    # TUN 路由 mihomo 自己退出会清；如果残留要手工 route delete
    log_cb("==================")
```

### Step 3：调整 GUI 里的小处

- 把 stylesheet 里的字体加 fallback：`".AppleSystemUIFont", "Segoe UI", "Microsoft YaHei UI"`，等宽 `"Menlo", "Consolas"`
- TUN 模式的提示文案改一下（macOS 是"输一次密码"，Windows 是"每次启动都会弹 UAC"）
- 可能要测试 `Qt.ColorScheme` 在 Windows 10 上的行为；用 `winreg` 读 `AppsUseLightTheme` 做 fallback

### Step 4：打包

`requirements.txt`：

```
PyQt6>=6.6
pywin32>=306
Pillow>=10
```

`pyinstaller` spec：

```python
# chainproxy_windows.spec
a = Analysis(
    ['chainproxy_qt.py'],
    pathex=['.'],
    binaries=[],
    datas=[('config.example.json', '.')],
    hiddenimports=['core.platform_windows'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name='ChainProxy',
    icon='icon.ico',
    console=False,  # GUI app, no console window
    uac_admin=False,  # 默认不要管理员权限——只在开 TUN 时申请
)
```

Inno Setup 脚本（`scripts/installer.iss`）：

```iss
[Setup]
AppName=ChainProxy
AppVersion=1.0.0
DefaultDirName={autopf}\ChainProxy
DefaultGroupName=ChainProxy
OutputDir=dist
OutputBaseFilename=ChainProxy-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes

[Files]
Source: "dist\ChainProxy.exe"; DestDir: "{app}"
Source: "config.example.json"; DestDir: "{app}"
Source: "README.md"; DestDir: "{app}"

[Icons]
Name: "{group}\ChainProxy"; Filename: "{app}\ChainProxy.exe"
Name: "{commondesktop}\ChainProxy"; Filename: "{app}\ChainProxy.exe"

[Run]
Filename: "{app}\ChainProxy.exe"; Flags: nowait postinstall
```

### Step 5：用户测试

PyInstaller 必须在 Windows 上跑（不能 cross-compile）。流程：

```powershell
python -m pip install -r requirements.txt pyinstaller
pyinstaller chainproxy_windows.spec
# → dist\ChainProxy.exe (单文件 exe)

# 用 Inno Setup 打包
iscc scripts\installer.iss
# → dist\ChainProxy-Setup-1.0.0.exe
```

### Step 6：发版

```powershell
gh release create v1.0.0-win dist\ChainProxy-Setup-1.0.0.exe `
    --title "ChainProxy 1.0.0 (Windows)" `
    --notes "..."
```

## 不做的事

- **不做 Linux 版**——用户没要求，core 重写一遍工作量同等
- **不做单文件可移植版**——用 PyInstaller `--onedir` 配 Inno Setup 安装更稳
- **不做 mihomo 自动下载**——和 macOS 版一致，让用户自己装。Windows 有 scoop / winget / 直接下载 release 三种方式

## 风险与限制

1. **TUN 模式 UX 退化**：Windows 上每次启动 TUN 都要点 UAC，不能像 macOS 那样安装一次免密助手
2. **WinTun 驱动**：mihomo 的 Windows 版自带 WinTun.dll，但首次启动可能要安装驱动（管理员权限）
3. **杀软误报**：PyInstaller 打包的 .exe 经常被 Windows Defender 当成可疑文件。需要考虑代码签名（要钱）或在 README 里说明"添加 Defender 例外"
4. **图标缓存**：Windows 也有图标缓存；改 .ico 后可能需要 `ie4uinit.exe -show` 刷新
