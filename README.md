# YouTube Player Discord Bot

只播放音訊的 Discord YouTube 播放機器人（discord.py + yt-dlp + FFmpeg）。

## 安裝

1. 安裝 [FFmpeg](https://ffmpeg.org/) 並確認 `ffmpeg` 在 PATH 中
2. `pip install -r requirements.txt`
3. 到 [Discord Developer Portal](https://discord.com/developers/applications) 建立 bot，複製 token
4. `cp .env.example .env`，填入 `DISCORD_TOKEN`
5. 用 OAuth2 URL 邀請 bot（scopes：`bot`、`applications.commands`；權限：Connect、Speak）
6. `python bot.py`

## 指令

| 指令 | 說明 |
| --- | --- |
| `/play <網址或關鍵字>` | 播放或加入佇列（支援播放清單，最多 50 首） |
| `/skip` | 跳過目前歌曲 |
| `/pause` / `/resume` | 暫停 / 繼續 |
| `/queue` | 顯示佇列 |
| `/stop` | 清空佇列並離開語音頻道 |

佇列空 5 分鐘、或頻道只剩 bot 時會自動離開。
