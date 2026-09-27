import asyncio
import os
import re
import subprocess
from collections import deque

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8428642578:AAEyjynyLzfXn6p6vK6F2L61aZrqEF32EgE")
API_ID = int(os.getenv("API_ID", "38447920"))
API_HASH = os.getenv("API_HASH", "1b23d14cd6ab87a6e9b8ffb4d3c4d6eb")

# The bot uses a Pyrogram bot session + PyTgCalls.
# Some Telegram VC setups may require a user session depending on PyTgCalls version.
app = Client(
    "music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

call = PyTgCalls(app)

queues = {}
current = {}
downloads = {}

YOUTUBE_RE = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+",
    re.I,
)


def get_queue(chat_id):
    return queues.setdefault(chat_id, deque())


async def search_youtube(query: str):
    """Return the first YouTube result URL/title using yt-dlp."""
    if YOUTUBE_RE.match(query):
        return query, query

    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "--flat-playlist",
        "--print",
        "%(webpage_url)s\t%(title)s",
        f"ytsearch1:{query}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0 or not stdout:
        return None, None

    line = stdout.decode("utf-8", errors="ignore").strip().splitlines()[0]
    parts = line.split("\t", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], parts[0]


async def get_audio_url(url: str):
    """Resolve a direct audio URL. No file is stored."""
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "-f", "bestaudio/best",
        "-g",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return None

    return stdout.decode("utf-8", errors="ignore").strip().splitlines()[0]


async def play_next(chat_id):
    q = get_queue(chat_id)

    if not q:
        current.pop(chat_id, None)
        return

    item = q.popleft()
    current[chat_id] = item

    try:
        audio_url = await get_audio_url(item["url"])
        if not audio_url:
            await play_next(chat_id)
            return

        await call.play(
            chat_id,
            MediaStream(
                audio_url,
                video_flags=MediaStream.Flags.IGNORE,
            ),
        )

        try:
            await app.send_message(
                chat_id,
                f"🎵 **Now Playing**\n\n"
                f"🎧 {item['title']}\n"
                f"👤 Requested by: {item['user']}",
            )
        except Exception:
            pass

    except Exception as e:
        await app.send_message(chat_id, f"❌ Play error: `{e}`")
        await play_next(chat_id)


@app.on_message(filters.command("start"))
async def start(_, message):
    await message.reply_text(
        "🎵 **VC Music Bot**\n\n"
        "Add me to a group, start a Voice Chat, then use:\n\n"
        "▶️ `/play song name`\n"
        "▶️ `/play YouTube_URL`\n"
        "⏸ `/pause`\n"
        "▶️ `/resume`\n"
        "⏭ `/skip`\n"
        "⏹ `/stop`\n"
        "📜 `/queue`\n"
        "🚪 `/leave`\n\n"
        "⚠️ The bot needs permission to manage the Voice Chat."
    )


@app.on_message(filters.command("play") & filters.group)
async def play_command(_, message):
    if len(message.command) < 2:
        await message.reply_text("🎵 Usage: `/play song name or YouTube URL`")
        return

    query = " ".join(message.command[1:]).strip()
    status = await message.reply_text("🔎 Searching...")

    url, title = await search_youtube(query)
    if not url:
        await status.edit_text("❌ Song not found.")
        return

    item = {
        "url": url,
        "title": title,
        "user": message.from_user.mention if message.from_user else "Unknown",
    }

    q = get_queue(message.chat.id)

    # If nothing is playing, start immediately.
    if message.chat.id not in current:
        q.append(item)
        await status.edit_text("⏳ Starting Voice Chat...")
        try:
            await play_next(message.chat.id)
        except Exception as e:
            await status.edit_text(
                "❌ Could not start playback.\n"
                "Make sure a Voice Chat is active and the bot has permission."
            )
        return

    q.append(item)
    await status.edit_text(
        f"✅ Added to queue:\n\n🎧 **{title}**\n"
        f"📌 Position: **{len(q)}**"
    )


@app.on_message(filters.command("pause") & filters.group)
async def pause_command(_, message):
    try:
        await call.pause(message.chat.id)
        await message.reply_text("⏸ Paused.")
    except Exception as e:
        await message.reply_text(f"❌ {e}")


@app.on_message(filters.command("resume") & filters.group)
async def resume_command(_, message):
    try:
        await call.resume(message.chat.id)
        await message.reply_text("▶️ Resumed.")
    except Exception as e:
        await message.reply_text(f"❌ {e}")


@app.on_message(filters.command("skip") & filters.group)
async def skip_command(_, message):
    try:
        await call.leave_call(message.chat.id)
    except Exception:
        pass

    current.pop(message.chat.id, None)
    await message.reply_text("⏭ Skipped.")
    await play_next(message.chat.id)


@app.on_message(filters.command("stop") & filters.group)
async def stop_command(_, message):
    queues.pop(message.chat.id, None)
    current.pop(message.chat.id, None)

    try:
        await call.leave_call(message.chat.id)
    except Exception:
        pass

    await message.reply_text("⏹ Stopped and cleared the queue.")


@app.on_message(filters.command("leave") & filters.group)
async def leave_command(_, message):
    queues.pop(message.chat.id, None)
    current.pop(message.chat.id, None)

    try:
        await call.leave_call(message.chat.id)
    except Exception:
        pass

    await message.reply_text("👋 Left the Voice Chat.")


@app.on_message(filters.command("queue") & filters.group)
async def queue_command(_, message):
    q = get_queue(message.chat.id)
    now = current.get(message.chat.id)

    lines = ["📜 **Music Queue**\n"]

    if now:
        lines.append(f"▶️ **Playing:** {now['title']}")

    if q:
        for i, item in enumerate(list(q)[:20], 1):
            lines.append(f"{i}. {item['title']}")
    elif not now:
        lines.append("Queue is empty.")

    await message.reply_text("\n".join(lines))


@call.on_stream_end()
async def stream_end(client, update):
    chat_id = update.chat_id
    current.pop(chat_id, None)
    await play_next(chat_id)


async def main():
    await app.start()
    print("🎵 VC Music Bot is running...")
    await call.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
