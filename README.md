# Seedance 2.0 视频生成

一个 Streamlit 版的 [Seedance 2.0](https://www.volcengine.com/product/seedance) 视频生成 UI。拖图、填 prompt、拿视频，无需任何对象存储或公网 IP。

本地上传的图片/视频/音频会通过 [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) 自动暴露为带一次性 token 的 HTTPS URL，喂给 Seedance API。生成好的视频自动存档到 `./videos/{task_id}.mp4`。

---

## 启动步骤

```bash
git clone <this-repo> && cd seedance-oss
python3 -m venv .venv && source .venv/bin/activate    # 强烈建议：隔离依赖
pip install -r requirements.txt
echo 'ark-你的key' > APIKEY           # 或 export ARK_API_KEY=ark-xxxx
./run_app.sh
```

API Key 在 [火山方舟控制台](https://console.volcengine.com/ark) 领取。

> **为什么第二步要新建 venv？** `run_app.sh` 会自动 `source .venv/bin/activate`。
> 如果你跳过这一步直接 `pip install`，依赖会装进系统 python / conda env / 别的项目
> 的 venv，启动时可能出现 `ModuleNotFoundError: No module named 'av'` 之类的报错
> ——原因是 `streamlit` 命令解析到的 python 解释器跟 `pip` 用的不是同一个。

> `pip install -r requirements.txt` 会拉取 PyAV（~30–60 MB），其中已包含 ffmpeg，
> 用于本地解码参考视频元数据（时长/分辨率/帧率）。无需单独安装 ffmpeg。

启动后浏览器访问 <http://localhost:8501>。侧边栏会显示
`✅ Tunnel: https://xxxxx.trycloudflare.com`，就能拖图使用了。

---

## 运行时用户需要做的事 & 不需要做的事

**不需要**：
- 公网 IP、端口映射、防火墙配置
- Cloudflare 账号、火山云 TOS 桶、任何其他对象存储
- 单独下载 `cloudflared`（macOS + Linux 二进制已在 `bin/` 里）

**需要**：一个能访问 `*.cloudflare.com` 的网络。国内家用/运营商网络大部分能直连；企业/校园网有封锁的话走代理即可。

---

## 平台支持

| 平台 | 状态 |
|---|---|
| macOS (Apple Silicon, Intel) | ✅ 仓库自带 |
| Linux x86_64 | ✅ 仓库自带 |
| Windows x64 | ⚠️ 见 [WINDOWS.md](./WINDOWS.md) |

---

## 目录结构

```
.
├── app.py                         # Streamlit 主 UI
├── tunnel.py                      # Cloudflare Tunnel + 本地 HTTP 服务器
├── run_app.sh                     # 启动脚本
├── requirements.txt
├── bin/
│   ├── cloudflared-darwin-arm64
│   ├── cloudflared-darwin-amd64
│   └── cloudflared-linux-amd64
├── upload_cache/                  # 运行时：上传图片的本地缓存（gitignored）
├── videos/                        # 运行时：生成视频的存档（gitignored）
└── logs/                          # 运行时：cloudflared 日志（gitignored）
```

---

## 安全说明

- 本地 HTTP server 只监听 `127.0.0.1:随机端口`，外部唯一入口是 Cloudflare 隧道
- 只服务 `upload_cache/` 下以 sha256 命名的文件；禁目录列表，禁路径穿越
- 每次启动生成新的 `session_token` 放进 URL query，历史外泄 URL 随重启自动失效
- 仍然请避免上传敏感内容——理论攻击面虽然小，但 trycloudflare.com 子域本身是 Cloudflare 控制面

---

## 常见问题

**Tunnel 启动失败 / 超时？**
说明本机到 `*.cloudflare.com` 不通。翻墙代理或换网络。日志在 `logs/cloudflared.log`。

**视频 24h 后打不开？**
Ark 服务方 URL 默认 24h TTL。本 app 启动时自动把 succeeded 任务的 mp4 拉到 `./videos/` 存档，之后永远能播。历史任务若没来得及存档就过期了，只能重新生成。

**我重启应用后旧的参考图 URL 还能用吗？**
不能——`session_token` 每次启动换新，旧 URL 会返回 403。但 Seedance 只在任务创建时读一次参考图，已提交的任务不受影响。
