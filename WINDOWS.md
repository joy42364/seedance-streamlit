# Windows 启动指南

Windows 10 / 11 专属启动文档。macOS / Linux 用户请看 [README.md](README.md)。

---

## 前置要求

| 项 | 要求 | 备注 |
|---|---|---|
| 操作系统 | Windows 10 1809+ 或 Windows 11 | 为了自带 `winget` |
| Python | **3.10 或更高**,推荐 3.12 | 代码用了 `str \| None` 联合类型语法,3.9 及以下会语法报错 |
| winget | 随系统 | 从 Microsoft Store "应用安装程序" 更新到最新 |
| Git | 可选 | 不用 Git 也可以直接下载仓库 zip |
| 网络 | 能访问 `*.trycloudflare.com` | Cloudflare Quick Tunnel 的必经出口 |

安装 Python 时**务必勾选** "Add python.exe to PATH",否则后续命令会找不到 `python`。

---

## 一次性安装

在 **PowerShell**(Windows Terminal 亦可)里依次执行:

### 1. 拉取仓库

```powershell
git clone <this-repo>
cd seedance-oss
```

没装 Git 就去 GitHub 下载 zip,解压后 `cd` 进目录。

### 2. 建虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果第二行报错 `因为在此系统上禁止运行脚本 … Activate.ps1 无法加载`,说明 PowerShell 默认的 `ExecutionPolicy` 被锁。**仅对当前用户**放开即可,不影响系统安全:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

然后重新执行 `.\.venv\Scripts\Activate.ps1`。激活成功后,提示符前会多一个 `(.venv)` 前缀。

> **为什么一定要 venv?** `streamlit` 命令的 shebang / launcher 会绑定它被安装时的 Python 解释器。如果 venv 没激活,`pip install` 和 `streamlit run` 可能用到**不同的 Python**,启动时出现 `ModuleNotFoundError: No module named 'av'` 之类莫名其妙的报错。

### 3. 装 Python 依赖

```powershell
pip install -r requirements.txt
```

会拉取 `streamlit`、`volcengine-python-sdk`、`av`(PyAV,内置 ffmpeg ~30–60 MB)。**不需要**单独安装 ffmpeg。

### 4. 装 cloudflared

推荐 winget:

```powershell
winget install --id Cloudflare.cloudflared
```

装完后**需要重新开一个 PowerShell 窗口**(或执行 `refreshenv`),让 PATH 生效。验证:

```powershell
cloudflared --version
```

**不想用 winget 的替代方案:**

1. 从 https://github.com/cloudflare/cloudflared/releases 下载 `cloudflared-windows-amd64.exe`
2. 放到项目目录下:`bin\cloudflared-windows-amd64.exe`

[`tunnel.py`](tunnel.py) 会优先在 `bin\` 下找这个文件,找不到才去 PATH 上找 `cloudflared`。

---

## 每次启动

```powershell
cd seedance-oss
.\.venv\Scripts\Activate.ps1
$env:ARK_API_KEY = "ark-你的key"
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

API Key 去 [火山方舟控制台](https://console.volcengine.com/ark) 领取。

> **注意:**`$env:ARK_API_KEY` 只在**当前 PowerShell 会话**内有效 —— 关掉窗口就失效。每次重开终端都要重新设置一次。如果不想每次重设:
> - 把赋值语句写进 PowerShell 配置文件 `$PROFILE`(`notepad $PROFILE`)
> - 或从「开始菜单 → 编辑账户的环境变量」加永久用户变量(改完要重开终端生效)
> - 也可以在启动后,直接在应用侧边栏的 "API Key" 输入框手动粘贴,应用会把它保存到 `api_keys.json`,下次自动读取

启动后浏览器打开 http://localhost:8501。侧边栏底部应该显示绿色圆点 + `Tunnel · xxxxx.trycloudflare.com`,这时才能拖图/视频/音频使用。

---

## 常见错误排查

| 症状 | 原因 | 解决 |
|---|---|---|
| `Activate.ps1 无法加载,因为在此系统上禁止运行脚本` | ExecutionPolicy 默认 Restricted | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` |
| `ModuleNotFoundError: No module named 'av'`(或 streamlit / volcenginesdkarkruntime) | venv 没激活,或 pip 装到别的 Python 上了 | 确认提示符前有 `(.venv)`;`where.exe python` 和 `where.exe pip` 都应指向 `.venv\Scripts\` |
| `'python' 不是内部或外部命令` | 装 Python 时没勾 Add to PATH | 用 `py -3.12` 代替 `python`;或重装 Python 勾上该选项 |
| `TunnelError: cloudflared not found on PATH` | cloudflared 没装 / 没重开终端 / 手放的 exe 路径不对 | 重新执行第 4 步;确认 `cloudflared --version` 能输出版本号 |
| Tunnel 启动 30 秒后超时(应用启动时直接报 `Cloudflare Tunnel 启动失败`) | 出口网络访问不到 `*.trycloudflare.com` | 开代理或换网络;日志见 `logs\cloudflared.log` |
| UI 里出现 `Error code: 401 AuthenticationError` | API Key 没读到或无效 | 在 PowerShell 里 `echo $env:ARK_API_KEY` 看有没有值;如为空就重设;Key 本身失效则去控制台重新生成 |
| 局域网其他设备打不开 `http://<本机IP>:8501` | Windows 防火墙拦了 Python | 第一次启动时弹出的"允许访问"对话框选 "专用网络";或在"Windows Defender 防火墙 → 允许应用" 里加 Python |

---

## 目录结构 & 数据位置

全部在项目根目录下,和 macOS 完全一致:

```
seedance-oss\
├── api_keys.json          ← 保存过的 API Key(明文)
├── tasks_history.json     ← 任务历史 + prompts
├── videos\                ← 归档的 mp4 + 尾帧 png
├── upload_cache\          ← Tunnel 暴露的参考素材缓存
├── logs\cloudflared.log   ← Tunnel 运行日志
└── bin\                   ← cloudflared 二进制(若手动放置)
```

备份只需拷贝 `api_keys.json`、`tasks_history.json`、`videos\`、`upload_cache\` 即可。

---

## 已知限制

- 仓库自带的 `run_app.sh` 是 Bash 脚本,Windows 上**不可用**。请使用本文档第"每次启动"小节的手动命令。
- `bin\` 目录**未预装** `cloudflared-windows-amd64.exe`,需要自己 winget 或手动放置。
- 应用监听 `0.0.0.0:8501`,局域网其他设备可访问;如不需要对外开放,把启动命令里的 `--server.address 0.0.0.0` 改成 `127.0.0.1`。
- Python 内部走 `pathlib`,路径分隔符在 Windows 下自动用反斜杠 `\`,不会踩坑。
