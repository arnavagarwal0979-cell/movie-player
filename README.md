# Vaultflix Telegram MTProto Stream Proxy

This proxy converts Telegram Channel video files (MP4, MKV, Documents up to 2GB+) into real, seekable HTTP 206 Partial Content video streaming URLs for ExoPlayer & HTML5 `<video>` players.

## Why this works
- **Standard Bot API:** Has a hard limit of 20MB for direct file downloads.
- **MTProto Layer:** Supports file downloads up to **2GB** for bot tokens and **4GB** for Telegram Premium accounts.
- **Range Header Support:** Allows ExoPlayer and HTML5 `<video>` to buffer, scrub, and seek forward/backward with instant response times.

## How to run locally
```bash
cd telegram_proxy
pip install -r requirements.txt

export API_ID="your_api_id"
export API_HASH="your_api_hash"
export BOT_TOKEN="your_bot_token"
export CHANNEL_ID="your_channel_id" # e.g. moviehub2108 or -1001234567890
export PORT=8080

python stream_server.py
```

## How to deploy in 1-Click (Railway / Render / VPS)
1. Push this folder to a GitHub repository or deploy with Dockerfile.
2. Set the 4 environment variables (`API_ID`, `API_HASH`, `BOT_TOKEN`, `CHANNEL_ID`).
3. Set your public proxy URL (e.g. `https://vaultflix-stream.railway.app`) in the Android App & Web Settings.
4. Enjoy smooth 1080p in-app video streaming!
