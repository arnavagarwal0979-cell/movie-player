"""
Vaultflix Telegram Stream Proxy
================================
Turns files sitting in a Telegram channel into normal, seekable,
Range-capable HTTP video URLs that ExoPlayer / HTML5 <video> can play
directly — no 20 MB limit, because we never touch api.telegram.org's
HTTP Bot API file-download endpoint. We talk raw MTProto via Pyrogram,
which supports bot file downloads up to ~2000 MB (and ~4000 MB for
premium user accounts).

Run:
    pip install -r requirements.txt
    python stream_server.py

Env vars required:
    API_ID          - from https://my.telegram.org
    API_HASH        - from https://my.telegram.org
    BOT_TOKEN       - from @BotFather (bot must be an ADMIN of the channel)
    CHANNEL_ID      - the channel's numeric id, e.g. -1001234567890 or @channel
    PORT            - default 8080
"""

import math
import os
import re
import mimetypes
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from pyrogram import Client, raw, utils
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid

API_ID = int(os.environ.get("API_ID", "0") or "0")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
raw_channel = os.environ.get("CHANNEL_ID", "0")
try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

PORT = int(os.environ.get("PORT", 8080))

# 1 MiB chunks — Telegram's CDN allows chunk sizes that are powers of two
# between 4 KiB and 1 MiB. 1 MiB gives the best throughput for video.
CHUNK_SIZE = 1024 * 1024

app = FastAPI(title="Vaultflix Telegram Stream Proxy")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten this to your web app's origin in prod
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["Range", "Content-Type", "Authorization", "Accept"],
    expose_headers=["Content-Range", "Content-Length", "Accept-Ranges"],
)

bot = Client(
    "vaultflix_stream_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

# Cache of per-DC media sessions so we don't re-auth on every request
_media_sessions: dict[int, Session] = {}


def get_media_from_message(message):
    for attr in ("video", "document", "audio", "animation", "voice", "video_note"):
        media = getattr(message, attr, None)
        if media:
            return media
    raise HTTPException(404, "No streamable media on this message")


async def get_media_session(client: Client, file_id: FileId) -> Session:
    """Reuse (or create) an authorized MTProto session on the file's data-center."""
    dc_id = file_id.dc_id
    if dc_id in _media_sessions:
        return _media_sessions[dc_id]

    if dc_id != await client.storage.dc_id():
        session = Session(
            client, dc_id, await Auth(client, dc_id, await client.storage.test_mode()).create(),
            await client.storage.test_mode(), is_media=True,
        )
        await session.start()
        for _ in range(6):
            exported_auth = await client.invoke(raw.functions.auth.ExportAuthorization(dc_id=dc_id))
            try:
                await session.invoke(
                    raw.functions.auth.ImportAuthorization(
                        id=exported_auth.id, bytes=exported_auth.bytes
                    )
                )
                break
            except AuthBytesInvalid:
                continue
    else:
        session = Session(
            client, dc_id, await client.storage.auth_key(),
            await client.storage.test_mode(), is_media=True,
        )
        await session.start()

    _media_sessions[dc_id] = session
    return session


def get_location(file_id: FileId):
    if file_id.file_type == FileType.PHOTO:
        return raw.types.InputPhotoFileLocation(
            id=file_id.media_id, access_hash=file_id.access_hash,
            file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size,
        )
    return raw.types.InputDocumentFileLocation(
        id=file_id.media_id, access_hash=file_id.access_hash,
        file_reference=file_id.file_reference, thumb_size="",
    )


async def yield_bytes(client: Client, file_id: FileId, offset: int, first_cut: int,
                       last_cut: int, part_count: int):
    """Streams `part_count` chunks starting at byte `offset`, trimming the
    first and last chunk so the caller gets exactly the requested Range."""
    session = await get_media_session(client, file_id)
    location = get_location(file_id)

    current_part = 1
    result = await session.invoke(
        raw.functions.upload.GetFile(location=location, offset=offset, limit=CHUNK_SIZE)
    )

    if isinstance(result, raw.types.upload.File):
        while True:
            chunk = result.bytes
            if not chunk:
                break
            if part_count == 1:
                yield chunk[first_cut:last_cut]
            elif current_part == 1:
                yield chunk[first_cut:]
            elif current_part == part_count:
                yield chunk[:last_cut]
            else:
                yield chunk

            current_part += 1
            offset += CHUNK_SIZE
            if current_part > part_count:
                break

            result = await session.invoke(
                raw.functions.upload.GetFile(location=location, offset=offset, limit=CHUNK_SIZE)
            )


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


@app.on_event("startup")
async def startup():
    if API_ID and API_HASH and BOT_TOKEN:
        await bot.start()
        print(f"Vaultflix MTProto Proxy connected successfully to Telegram on port {PORT}")
    else:
        print("Warning: API_ID, API_HASH, or BOT_TOKEN missing. Configure environment variables.")


@app.on_event("shutdown")
async def shutdown():
    if bot.is_connected:
        await bot.stop()


@app.get("/")
async def index():
    return {
        "service": "Vaultflix Telegram MTProto Stream Proxy",
        "status": "online",
        "bot_connected": bot.is_connected if hasattr(bot, "is_connected") else False,
        "endpoints": {
            "stream": "/stream/{message_id}",
            "info": "/info/{message_id}"
        }
    }


@app.get("/info/{message_id}")
async def get_info(message_id: int):
    if not bot.is_connected:
        raise HTTPException(503, "Telegram MTProto client is not connected")
    message = await bot.get_messages(CHANNEL_ID, message_id)
    if not message or message.empty:
        raise HTTPException(404, "Message not found")
    media = get_media_from_message(message)
    return {
        "message_id": message_id,
        "file_name": getattr(media, "file_name", "video.mp4"),
        "file_size": getattr(media, "file_size", 0),
        "mime_type": getattr(media, "mime_type", "video/mp4"),
        "duration": getattr(media, "duration", None),
        "stream_url": f"/stream/{message_id}"
    }


@app.head("/stream/{message_id}")
@app.get("/stream/{message_id}")
async def stream(message_id: int, request: Request):
    if not bot.is_connected:
        raise HTTPException(503, "Telegram MTProto client is not connected")

    message = await bot.get_messages(CHANNEL_ID, message_id)
    if not message or message.empty:
        raise HTTPException(404, "Message not found")

    media = get_media_from_message(message)
    file_size = media.file_size
    mime_type = getattr(media, "mime_type", None) or mimetypes.guess_type(
        getattr(media, "file_name", "") or ""
    )[0] or "application/octet-stream"

    file_id = FileId.decode(media.file_id)

    range_header = request.headers.get("range")
    start, end = 0, file_size - 1
    if range_header:
        m = RANGE_RE.match(range_header)
        if not m:
            raise HTTPException(416, "Invalid Range header")
        if m.group(1):
            start = int(m.group(1))
        if m.group(2):
            end = int(m.group(2))
        end = min(end, file_size - 1)
        if start > end:
            raise HTTPException(416, "Invalid Range")

    req_length = end - start + 1

    offset = start - (start % CHUNK_SIZE)
    first_cut = start - offset
    last_cut = (end % CHUNK_SIZE) + 1
    part_count = math.ceil((end + 1 - offset) / CHUNK_SIZE)

    headers = {
        "Content-Type": mime_type,
        "Accept-Ranges": "bytes",
        "Content-Length": str(req_length),
        "Content-Disposition": f'inline; filename="{getattr(media, "file_name", "video.mp4")}"',
    }

    status_code = 200
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        status_code = 206

    return StreamingResponse(
        yield_bytes(bot, file_id, offset, first_cut, last_cut, part_count),
        status_code=status_code,
        headers=headers,
        media_type=mime_type,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
