# Release 流程

ChainProxy 的发版包含两个产物：

- **macOS**：`ChainProxy-x.y.z.dmg` （用户自备 mihomo + PyQt6）
- **Windows**：`ChainProxy-Setup-x.y.z.exe` （installer 自带 mihomo + PyQt6 运行时）

每次发布走以下流程。Windows 和 macOS 的构建可以在各自机器上**并行**做，但都需要在打 release 前推到 main。

## 1. 准备版本号

```bash
# 在 main 分支上
VERSION=1.1.0
```

需要改的地方：
- `scripts/installer.iss` 的 `#define MyAppVersion "1.1.0"`
- `chainproxy_qt.py` 里 sidebar 的 `version = QLabel(f"v1.1  ·  ...")` （只显示 major.minor）
- `README.md` 里的 `make_dmg.sh 1.1.0` 示例（可选）

提交一个 chore commit：`bump: 1.1.0` —— 不要包含其他改动。

## 2. macOS 构建（在 mac 上）

```bash
git pull
# 装运行时依赖
brew install mihomo
pip3 install PyQt6

# 跑测试
python3 -c "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; \
            import chainproxy_qt; print('import OK')"

# 打 .app + .dmg
bash scripts/build.sh
bash scripts/make_dmg.sh 1.1.0
# 产物：dist/ChainProxy-1.1.0.dmg
```

## 3. Windows 构建（在 Windows 上）

```powershell
git pull

# 一次性装的依赖（之后只需重跑构建脚本）
py -m pip install --user PyQt6 pywin32 Pillow pyinstaller
# 装 Inno Setup（出 installer 必需）
winget install JRSoftware.InnoSetup

# 跑测试（mihomo 必须先在 %APPDATA%\ChainProxy\ 下，或者构建会自动下）
py tests\smoke_test.py
py tests\test_yaml_parity.py
py tests\test_proxy_lifecycle.py

# 一键打 installer
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
# 产物：dist\ChainProxy-Setup-1.1.0.exe
```

构建脚本会自动：
- 下载 mihomo amd64-compatible 版到 `scripts\mihomo.exe`（如果不存在）
- 重新生成 `icon.ico`
- 跑 PyInstaller 打 `dist\ChainProxy\ChainProxy.exe`
- 跑 Inno Setup 打 `dist\ChainProxy-Setup-x.y.z.exe`

## 4. 烟雾测试 installer / dmg

**Windows：**
- 双击 installer，在选路径界面选个临时目录（如 `C:\TestInstall\ChainProxy`）
- 勾选"创建桌面快捷方式"
- 安装完成后点桌面图标启动，验证：
  - 启动不报"找不到 mihomo"（自带的应该能找到）
  - 系统代理模式能启动 + 测试 baidu.com
  - TUN 模式（如果你测）能启动 + 测试 chatgpt.com
- 控制面板卸载，确认 `%APPDATA%\ChainProxy\` 保留，安装目录干净

**macOS：**
- 双击 .dmg，把 .app 拖到 Applications
- 启动，验证启动 + 测试

## 5. 创建 release

```bash
# tag + push
git tag v1.1.0
git push origin v1.1.0

# 上传产物（在 mac 或 win 哪边的 gh 装好都行）
gh release create v1.1.0 \
    dist/ChainProxy-1.1.0.dmg \
    dist/ChainProxy-Setup-1.1.0.exe \
    --title "ChainProxy 1.1.0" \
    --notes-file release-notes-1.1.0.md
```

`release-notes-1.1.0.md` 模板：

```markdown
## 1.1.0 — Windows 移植

### 新增
- ✨ Windows 版完整支持：系统代理模式 / TUN 模式 / 链式代理 / 规则分流
- ✨ Windows installer 自带 mihomo 内核，免下载第三方
- ✨ Windows TUN 模式：每次开机一次 UAC，启停 mihomo 不再弹

### 修复
- 浅色主题的"最近日志"区域改成浅色背景
- 任务栏 / Dock 显示 ChainProxy 自己的图标
- 快速测试结果显示真实规则名 + 节点名

### 下载
- macOS: ChainProxy-1.1.0.dmg
- Windows: ChainProxy-Setup-1.1.0.exe（推荐）
```

## 6. 验证 release

发完 release 在干净环境（同事的电脑 / VM / 没装过 ChainProxy 的设备）下：
- macOS：从 .dmg 装，跑通完整流程
- Windows：从 installer 装，确认 mihomo 自动识别 + 桌面快捷方式工作

如果哪条不通，立即 yank release（`gh release delete v1.1.0 --yes && git push origin :refs/tags/v1.1.0`），修了再发。
