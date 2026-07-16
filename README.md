# 短视频收件箱桥接

把抖音和微信视频号内容转成文字，并写回 Obsidian 收件箱。插件面向 Windows 桌面版 Obsidian，支持豆包录音文件识别和本地 Whisper 备用转写。

> 本项目由小楼开发。Share to Save 是可选的上游采集插件，不属于本项目，也不包含在本仓库中。

## 工作方式

1. Share to Save（或生成相同队列文件的工具）把分享链接写入收件箱 Markdown 笔记。
2. 插件识别抖音或微信视频号链接，并调用仅监听 `127.0.0.1` 的本地 Python 后端。
3. 后端提取音轨，通过豆包 API 或本地 Whisper 转写。
4. 插件把标题、作者、来源、音频附件和转写正文写回原笔记。

微信视频号还支持两种桌面采集方式：读取 PC 微信本地缓存中的当前页面线索，或安装可选的本地安全采集助手后按热键确认保存。

## 环境要求

- Windows 10/11
- Obsidian 1.8.7 或更高版本
- Node.js 18+（仅从源码构建时需要）
- Python 3.10+
- FFmpeg，并已加入 `PATH`
- 豆包语音 API Key；如果只使用本地 Whisper，可以不配置

## 从源码安装

```powershell
git clone https://github.com/TC-TCQKJJ/obsidian-short-video-inbox.git
cd obsidian-short-video-inbox
npm ci
npm run build
.\scripts\install-backend.ps1
```

把根目录中的 `main.js`、`manifest.json` 和 `styles.css` 复制到：

```text
<你的 Vault>/.obsidian/plugins/douyin-inbox-bridge/
```

然后在 Obsidian 的“第三方插件”设置中启用“短视频收件箱桥接”。插件需要时会自动启动本地后端。

### 可选：安装微信视频号安全采集助手

这个步骤会为当前 Windows 用户创建并安装一张本地根证书，用于解密指定的微信视频号域名流量。请先阅读 [`backend/wechat_capture/README.md`](backend/wechat_capture/README.md)，确认理解安全边界后再运行：

```powershell
& "$env:LOCALAPPDATA\Xiaolou\DouyinCapture\backend\wechat_capture\install.ps1"
```

卸载证书：

```powershell
& "$env:LOCALAPPDATA\Xiaolou\DouyinCapture\backend\wechat_capture\uninstall-certificate.ps1"
```

## 设置

- 收件箱目录：监听 Share to Save 队列写入的 Markdown 笔记。
- 附件目录：保存音频和图文附件。
- 本地后端 URL：默认 `http://127.0.0.1:5050`。
- 转写引擎：豆包或本地 Whisper。
- 豆包 API Key：只保存在当前 Vault 的插件配置 `data.json` 中。
- 录音设备：用于无法直接取得微信视频号音轨时的本地播放录音。

## 隐私与网络披露

- 插件不含遥测、广告或用户追踪。
- 插件会读取和修改所配置收件箱中的笔记，并写入所配置的附件目录。
- 如果把本地后端 URL 改为远程地址，音频和豆包 API Key 会发送到该服务器；除非完全信任服务器，否则请保持默认本机地址。
- 豆包转写会把音频发送至 `openspeech.bytedance.com`；使用本地 Whisper 时不会把音频发送给语音 API。
- 抖音解析会访问抖音分享页和其媒体 CDN；微信视频号采集会访问微信分享页和媒体 CDN。
- 首次使用本地 Whisper 时，依赖可能从 Hugging Face 下载模型。
- API Key、抓取令牌、签名媒体 URL、证书私钥、日志和运行输出均不提交到本仓库。
- 请只保存你有权访问和留存的内容，并遵守所在地区法律和内容平台规则。

## 开发与测试

```powershell
npm ci
npm run check
npm test
npm run build
python -m unittest discover -s backend/tests -p "test_*.py"
```

发布时，GitHub Release 的标签必须与 `manifest.json` 中的版本完全一致，并附带 `main.js`、`manifest.json` 和 `styles.css`。

## 许可

原创代码采用 MIT License。`backend/script/wechat_media_decrypt.py` 含受 MIT + Commons Clause 约束的第三方衍生代码，不能视为由本项目重新以纯 MIT 许可。详见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
