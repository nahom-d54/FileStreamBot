import asyncio
import os
import re
import time

from pyrogram import Client, filters
from pyrogram.enums.parse_mode import ParseMode
from pyrogram.errors import FloodWait
from pyrogram.types import Message

from FileStream.bot import FileStream, multi_clients
from FileStream.config import Telegram
from FileStream.utils.bot_utils import gen_link, verify_user
from FileStream.utils.database import Database
from FileStream.utils.file_properties import get_file_ids, get_file_info
from FileStream.utils.human_readable import humanbytes
from FileStream.utils.url_uploader import download_from_url

db = Database(Telegram.DATABASE_URL, Telegram.SESSION_NAME)

DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
URL_PATTERN = re.compile(r"https?://\S+")


async def _progress_message(status_msg, start_time):
    """Update status text with elapsed time."""

    async def callback(downloaded, total):
        now = time.time()
        if now - callback.last_update < 5:
            return
        callback.last_update = now
        elapsed = now - start_time
        speed = downloaded / elapsed if elapsed > 0 else 0
        text = "<b>⬇ Dᴏᴡɴʟᴏᴀᴅɪɴɢ...</b>\n\n"
        text += f"<b>📦 Sɪᴢᴇ :</b> {humanbytes(downloaded)}"
        if total:
            text += f" / {humanbytes(total)}"
            pct = downloaded / total * 100
            text += f"\n<b>📊 Pʀᴏɢʀᴇss :</b> {pct:.1f}%"
        text += f"\n<b>⚡ Sᴘᴇᴇᴅ :</b> {humanbytes(speed)}/s"
        try:
            await status_msg.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    callback.last_update = 0
    return callback


@FileStream.on_message(filters.command("upload") & filters.private, group=5)
async def url_upload_handler(bot: Client, message: Message):
    if not await verify_user(bot, message):
        return

    # Extract URL from command arguments
    if len(message.command) < 2:
        await message.reply_text(
            "<b>⚠ Usᴀɢᴇ:</b> <code>/upload https://example.com/file.mp4</code>",
            parse_mode=ParseMode.HTML,
            quote=True,
        )
        return

    url = message.command[1].strip()
    if not URL_PATTERN.match(url):
        await message.reply_text(
            "<b>⚠ Iɴᴠᴀʟɪᴅ URL.</b> Pʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ HTTP/HTTPS ʟɪɴᴋ.",
            parse_mode=ParseMode.HTML,
            quote=True,
        )
        return

    status_msg = await message.reply_text(
        "<b>🔍 Fᴇᴛᴄʜɪɴɢ ғɪʟᴇ ɪɴғᴏ...</b>", parse_mode=ParseMode.HTML, quote=True
    )

    file_path = None
    try:
        start_time = time.time()
        progress_cb = await _progress_message(status_msg, start_time)

        file_path, file_name, file_size, mime_type = await download_from_url(
            url, DOWNLOAD_DIR, progress_callback=progress_cb
        )

        await status_msg.edit_text(
            f"<b>⬆ Uᴘʟᴏᴀᴅɪɴɢ ᴛᴏ Tᴇʟᴇɢʀᴀᴍ...</b>\n\n"
            f"<b>📂 Fɪʟᴇ :</b> <code>{file_name}</code>\n"
            f"<b>📦 Sɪᴢᴇ :</b> {humanbytes(file_size)}",
            parse_mode=ParseMode.HTML,
        )

        # Upload to Telegram as document
        upload_start = time.time()

        async def upload_progress(current, total):
            now = time.time()
            if now - upload_progress.last_update < 5:
                return
            upload_progress.last_update = now
            pct = current / total * 100
            speed = current / (now - upload_start) if (now - upload_start) > 0 else 0
            try:
                await status_msg.edit_text(
                    f"<b>⬆ Uᴘʟᴏᴀᴅɪɴɢ ᴛᴏ Tᴇʟᴇɢʀᴀᴍ...</b>\n\n"
                    f"<b>📂 Fɪʟᴇ :</b> <code>{file_name}</code>\n"
                    f"<b>📦 Sɪᴢᴇ :</b> {humanbytes(current)} / {humanbytes(total)}\n"
                    f"<b>📊 Pʀᴏɢʀᴇss :</b> {pct:.1f}%\n"
                    f"<b>⚡ Sᴘᴇᴇᴅ :</b> {humanbytes(speed)}/s",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        upload_progress.last_update = 0

        # Send as video if mime indicates video, otherwise as document
        if mime_type and "video" in mime_type:
            sent_msg = await bot.send_video(
                chat_id=message.chat.id,
                video=file_path,
                caption=f"**{file_name}**",
                file_name=file_name,
                progress=upload_progress,
            )
        elif mime_type and "audio" in mime_type:
            sent_msg = await bot.send_audio(
                chat_id=message.chat.id,
                audio=file_path,
                caption=f"**{file_name}**",
                file_name=file_name,
                progress=upload_progress,
            )
        else:
            sent_msg = await bot.send_document(
                chat_id=message.chat.id,
                document=file_path,
                caption=f"**{file_name}**",
                file_name=file_name,
                progress=upload_progress,
                force_document=True,
            )

        # Store in DB and generate stream link (same as regular file handler)
        inserted_id = await db.add_file(get_file_info(sent_msg))
        await get_file_ids(
            False, inserted_id, multi_clients, sent_msg, requester=message
        )
        reply_markup, stream_text = await gen_link(_id=inserted_id)

        await status_msg.edit_text(
            text=stream_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )

    except ValueError as e:
        await status_msg.edit_text(f"<b>❌ Eʀʀᴏʀ:</b> {e}", parse_mode=ParseMode.HTML)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await status_msg.edit_text(
            "<b>⚠ Rᴀᴛᴇ ʟɪᴍɪᴛᴇᴅ. Pʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.</b>", parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await status_msg.edit_text(
            f"<b>❌ Uᴘʟᴏᴀᴅ ғᴀɪʟᴇᴅ:</b> <code>{e}</code>", parse_mode=ParseMode.HTML
        )
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
