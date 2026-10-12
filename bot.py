import asyncio
import os
from collections import deque
from dataclasses import dataclass

import discord
import yt_dlp
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

IDLE_TIMEOUT = 300  # 佇列空了之後幾秒自動離開語音頻道
MAX_PLAYLIST = 50

YDL_OPTS = {
    "format": "bestaudio/best",  # 只要音訊
    "quiet": True,
    "no_warnings": True,
    "extract_flat": "in_playlist",
    "playlistend": MAX_PLAYLIST,
}
if os.getenv("YTDL_COOKIES"):
    YDL_OPTS["cookiefile"] = os.environ["YTDL_COOKIES"]

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",  # 丟棄影像
}


@dataclass
class Track:
    title: str
    page_url: str


def _extract(query: str, resolve: bool = False) -> dict:
    """在 thread 中執行 yt-dlp。resolve=True 時取得實際音訊串流網址。"""
    is_url = query.startswith(("http://", "https://"))
    opts = dict(YDL_OPTS)
    # 影片網址（含 v=）只播單曲；純播放清單網址才展開
    opts["noplaylist"] = resolve or ("v=" in query or "youtu.be/" in query)
    if resolve:
        opts["extract_flat"] = False
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(query if is_url else f"ytsearch1:{query}", download=False)


async def search(query: str) -> list[Track]:
    info = await asyncio.to_thread(_extract, query)
    entries = info.get("entries") or [info]
    tracks = []
    for e in entries:
        if not e:
            continue
        url = e.get("webpage_url") or e.get("url") or ""
        if not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={e['id']}"
        tracks.append(Track(e.get("title") or "未知標題", url))
    return tracks


async def stream_url(track: Track) -> str:
    info = await asyncio.to_thread(_extract, track.page_url, True)
    return info["url"]


class GuildPlayer:
    def __init__(self, vc: discord.VoiceClient):
        self.vc = vc
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self._wakeup = asyncio.Event()
        self._done = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    def add(self, tracks: list[Track]):
        self.queue.extend(tracks)
        self._wakeup.set()

    async def _run(self):
        try:
            while True:
                if not self.queue:
                    self.current = None
                    self._wakeup.clear()
                    try:
                        await asyncio.wait_for(self._wakeup.wait(), IDLE_TIMEOUT)
                    except asyncio.TimeoutError:
                        break
                    continue

                self.current = self.queue.popleft()
                try:
                    url = await stream_url(self.current)
                except Exception as e:  # 影片不可用、地區限制等
                    print(f"無法播放 {self.current.title}: {e}")
                    continue

                self._done.clear()
                loop = asyncio.get_running_loop()
                self.vc.play(
                    discord.FFmpegPCMAudio(url, **FFMPEG_OPTS),
                    after=lambda _e: loop.call_soon_threadsafe(self._done.set),
                )
                await self._done.wait()
        finally:
            await self.destroy()

    async def destroy(self):
        self.queue.clear()
        if self.vc.is_connected():
            await self.vc.disconnect()
        players.pop(self.vc.guild.id, None)
        if self._task is not asyncio.current_task():
            self._task.cancel()


players: dict[int, GuildPlayer] = {}

intents = discord.Intents.default()  # 含 voice_states，不需要 message_content
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    print(f"已登入：{client.user}")


@client.event
async def setup_hook():
    guild_id = os.getenv("GUILD_ID")
    if guild_id:
        guild = discord.Object(int(guild_id))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()


@client.event
async def on_voice_state_update(member, before, after):
    # 被踢出語音頻道，或頻道裡只剩 bot 時，清理播放器
    player = players.get(member.guild.id)
    if not player:
        return
    channel = player.vc.channel
    if channel is None or all(m.bot for m in channel.members):
        await player.destroy()


def _player_for(interaction: discord.Interaction) -> GuildPlayer | None:
    return players.get(interaction.guild_id)


@tree.command(name="play", description="播放 YouTube 音訊（網址或關鍵字）")
@app_commands.describe(query="YouTube 網址、播放清單網址，或搜尋關鍵字")
@app_commands.guild_only()
async def play(interaction: discord.Interaction, query: str):
    voice = interaction.user.voice
    if not voice or not voice.channel:
        return await interaction.response.send_message("請先加入一個語音頻道。", ephemeral=True)

    await interaction.response.defer()
    try:
        tracks = await search(query)
    except Exception as e:
        return await interaction.followup.send(f"搜尋失敗：{e}")
    if not tracks:
        return await interaction.followup.send("找不到結果。")

    player = _player_for(interaction)
    if player is None:
        vc = await voice.channel.connect()
        player = players[interaction.guild_id] = GuildPlayer(vc)
    elif player.vc.channel != voice.channel:
        return await interaction.followup.send("我已經在別的語音頻道了。")

    player.add(tracks)
    if len(tracks) == 1:
        await interaction.followup.send(f"已加入佇列：**{tracks[0].title}**")
    else:
        await interaction.followup.send(f"已加入 {len(tracks)} 首歌到佇列。")


@tree.command(name="skip", description="跳過目前歌曲")
@app_commands.guild_only()
async def skip(interaction: discord.Interaction):
    player = _player_for(interaction)
    if not player or not player.current:
        return await interaction.response.send_message("目前沒有在播放。", ephemeral=True)
    player.vc.stop()
    await interaction.response.send_message("已跳過。")


@tree.command(name="pause", description="暫停")
@app_commands.guild_only()
async def pause(interaction: discord.Interaction):
    player = _player_for(interaction)
    if not player or not player.vc.is_playing():
        return await interaction.response.send_message("目前沒有在播放。", ephemeral=True)
    player.vc.pause()
    await interaction.response.send_message("已暫停。")


@tree.command(name="resume", description="繼續播放")
@app_commands.guild_only()
async def resume(interaction: discord.Interaction):
    player = _player_for(interaction)
    if not player or not player.vc.is_paused():
        return await interaction.response.send_message("目前沒有暫停的歌曲。", ephemeral=True)
    player.vc.resume()
    await interaction.response.send_message("繼續播放。")


@tree.command(name="queue", description="顯示播放佇列")
@app_commands.guild_only()
async def queue(interaction: discord.Interaction):
    player = _player_for(interaction)
    if not player or not (player.current or player.queue):
        return await interaction.response.send_message("佇列是空的。", ephemeral=True)
    lines = []
    if player.current:
        lines.append(f"▶️ **{player.current.title}**")
    for i, t in enumerate(list(player.queue)[:10], 1):
        lines.append(f"{i}. {t.title}")
    if len(player.queue) > 10:
        lines.append(f"…還有 {len(player.queue) - 10} 首")
    await interaction.response.send_message("\n".join(lines))


@tree.command(name="stop", description="停止播放、清空佇列並離開語音頻道")
@app_commands.guild_only()
async def stop(interaction: discord.Interaction):
    player = _player_for(interaction)
    if not player:
        return await interaction.response.send_message("我不在語音頻道。", ephemeral=True)
    await interaction.response.send_message("已停止並離開。")
    await player.destroy()


if __name__ == "__main__":
    client.run(os.environ["DISCORD_TOKEN"])
