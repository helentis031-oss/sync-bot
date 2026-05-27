import asyncio
import aiohttp
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== НАСТРОЙКИ ====================
TG_TOKEN = "8850239704:AAE-JYmcwNacirQwYdznCpx8QSODSTArTXE"
MAX_TOKEN = "f9LHodD0cOKFYUeNaCOLwztd4ts_PRktdviwHe9sEoBq7-j3nSXnGnXX3dw1Z-5lF8JnHZcR21OTapc2nfxi"

GROUPS = [
    {"tg": -1003669566686,  "max": -70934133934771},  # Воспитатели
    {"tg": -1003963499983,  "max": -70933950892723},  # Первооткрыватели
    {"tg": -1003934349337,  "max": -70933982612147},  # Познаватели
]

TG_TO_MAX = {g["tg"]: g["max"] for g in GROUPS}
MAX_TO_TG = {g["max"]: g["tg"] for g in GROUPS}

TG_API  = f"https://api.telegram.org/bot{TG_TOKEN}"
MAX_API = "https://platform-api.max.ru"
MAX_HDR = {"Authorization": MAX_TOKEN}
# ====================================================

def get_tg_sender_name(sender: dict) -> str:
    """Только имя и фамилия, без ника"""
    name = sender.get("first_name", "")
    if sender.get("last_name"):
        name += f" {sender['last_name']}"
    return name or "Пользователь"

def get_max_sender_name(sender: dict) -> str:
    """Только имя из MAX, без ника"""
    return sender.get("name") or sender.get("first_name") or "Пользователь"

async def download_file(session, url) -> bytes:
    """Скачать файл по URL"""
    async with session.get(url) as resp:
        return await resp.read()

async def get_tg_file_url(session, file_id) -> str:
    """Получить прямую ссылку на файл в Telegram"""
    async with session.get(f"{TG_API}/getFile", params={"file_id": file_id}) as resp:
        data = await resp.json()
        if data.get("ok"):
            path = data["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{TG_TOKEN}/{path}"
    return None

async def send_to_tg(session, chat_id, text=None, sender_name=None, photo_url=None, file_url=None, file_name=None):
    """Отправить сообщение/фото/файл в Telegram"""
    caption = f"📨 {sender_name} (MAX):\n{text}" if text else f"📨 {sender_name} (MAX):"

    try:
        if photo_url:
            # Скачиваем фото из MAX и отправляем в TG
            file_data = await download_file(session, photo_url)
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field("caption", caption)
            form.add_field("photo", file_data, filename="photo.jpg", content_type="image/jpeg")
            async with session.post(f"{TG_API}/sendPhoto", data=form) as resp:
                data = await resp.json()
                if data.get("ok"):
                    logger.info(f"✅ MAX→TG фото: {chat_id} | {sender_name}")
                else:
                    logger.error(f"❌ MAX→TG фото error: {data}")
        elif file_url:
            # Скачиваем файл из MAX и отправляем в TG
            file_data = await download_file(session, file_url)
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field("caption", caption)
            form.add_field("document", file_data, filename=file_name or "file", content_type="application/octet-stream")
            async with session.post(f"{TG_API}/sendDocument", data=form) as resp:
                data = await resp.json()
                if data.get("ok"):
                    logger.info(f"✅ MAX→TG файл: {chat_id} | {sender_name}")
                else:
                    logger.error(f"❌ MAX→TG файл error: {data}")
        else:
            # Текстовое сообщение
            async with session.post(f"{TG_API}/sendMessage",
                                    json={"chat_id": chat_id, "text": caption}) as resp:
                data = await resp.json()
                if data.get("ok"):
                    logger.info(f"✅ MAX→TG текст: {chat_id} | {sender_name}")
                else:
                    logger.error(f"❌ MAX→TG текст error: {data}")
    except Exception as e:
        logger.error(f"❌ MAX→TG exception: {e}")

async def send_to_max(session, chat_id, text=None, sender_name=None, photo_data=None, photo_name=None, file_data=None, file_name=None):
    """Отправить сообщение/фото/файл в MAX"""
    caption = f"📨 {sender_name} (TG):\n{text}" if text else f"📨 {sender_name} (TG):"

    try:
        if photo_data:
            # Загружаем фото в MAX
            form = aiohttp.FormData()
            form.add_field("photo", photo_data, filename=photo_name or "photo.jpg", content_type="image/jpeg")
            async with session.post(f"{MAX_API}/uploads?type=photo",
                                    headers={"Authorization": MAX_TOKEN}, data=form) as resp:
                upload = await resp.json()

            token = upload.get("token")
            if token:
                payload = {
                    "text": caption,
                    "attachments": [{"type": "image", "payload": {"token": token}}]
                }
                async with session.post(f"{MAX_API}/messages", headers=MAX_HDR,
                                        params={"chat_id": chat_id}, json=payload) as resp:
                    data = await resp.json()
                    logger.info(f"✅ TG→MAX фото: {chat_id} | {sender_name}")
            else:
                # Если загрузка не удалась — отправляем текст
                async with session.post(f"{MAX_API}/messages", headers=MAX_HDR,
                                        params={"chat_id": chat_id},
                                        json={"text": caption + "\n[фото]"}) as resp:
                    pass

        elif file_data:
            # Загружаем файл в MAX
            form = aiohttp.FormData()
            form.add_field("file", file_data, filename=file_name or "file")
            async with session.post(f"{MAX_API}/uploads?type=file",
                                    headers={"Authorization": MAX_TOKEN}, data=form) as resp:
                upload = await resp.json()

            token = upload.get("token")
            if token:
                payload = {
                    "text": caption,
                    "attachments": [{"type": "file", "payload": {"token": token}}]
                }
                async with session.post(f"{MAX_API}/messages", headers=MAX_HDR,
                                        params={"chat_id": chat_id}, json=payload) as resp:
                    data = await resp.json()
                    logger.info(f"✅ TG→MAX файл: {chat_id} | {sender_name}")
            else:
                async with session.post(f"{MAX_API}/messages", headers=MAX_HDR,
                                        params={"chat_id": chat_id},
                                        json={"text": caption + f"\n[файл: {file_name}]"}) as resp:
                    pass
        else:
            # Текст
            async with session.post(f"{MAX_API}/messages", headers=MAX_HDR,
                                    params={"chat_id": chat_id}, json={"text": caption}) as resp:
                data = await resp.json()
                logger.info(f"✅ TG→MAX текст: {chat_id} | {sender_name}")
    except Exception as e:
        logger.error(f"❌ TG→MAX exception: {e}")

async def poll_telegram(session):
    offset = 0
    logger.info("🔄 Запущен polling Telegram...")
    while True:
        try:
            params = {"offset": offset, "timeout": 30, "allowed_updates": ["message"]}
            async with session.get(f"{TG_API}/getUpdates", params=params,
                                   timeout=aiohttp.ClientTimeout(total=35)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(5)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    if not msg:
                        continue
                    if msg.get("from", {}).get("is_bot"):
                        continue

                    chat_id = msg["chat"]["id"]
                    if chat_id not in TG_TO_MAX:
                        continue

                    max_chat_id = TG_TO_MAX[chat_id]
                    sender = msg.get("from", {})
                    name = get_tg_sender_name(sender)
                    text = msg.get("text") or msg.get("caption") or ""

                    # Фото
                    if msg.get("photo"):
                        largest = msg["photo"][-1]
                        file_url = await get_tg_file_url(session, largest["file_id"])
                        if file_url:
                            photo_data = await download_file(session, file_url)
                            await send_to_max(session, max_chat_id, text=text, sender_name=name,
                                             photo_data=photo_data, photo_name="photo.jpg")
                        continue

                    # Документ/файл
                    if msg.get("document"):
                        doc = msg["document"]
                        file_url = await get_tg_file_url(session, doc["file_id"])
                        if file_url:
                            file_data = await download_file(session, file_url)
                            await send_to_max(session, max_chat_id, text=text, sender_name=name,
                                             file_data=file_data, file_name=doc.get("file_name", "file"))
                        continue

                    # Стикер
                    if msg.get("sticker"):
                        sticker = msg["sticker"]
                        file_url = await get_tg_file_url(session, sticker["file_id"])
                        if file_url:
                            file_data = await download_file(session, file_url)
                            await send_to_max(session, max_chat_id, text="", sender_name=name,
                                             file_data=file_data, file_name="sticker.webp")
                        continue

                    # Видео
                    if msg.get("video"):
                        video = msg["video"]
                        file_url = await get_tg_file_url(session, video["file_id"])
                        if file_url:
                            file_data = await download_file(session, file_url)
                            await send_to_max(session, max_chat_id, text=text, sender_name=name,
                                             file_data=file_data, file_name="video.mp4")
                        continue

                    # Голосовое
                    if msg.get("voice"):
                        voice = msg["voice"]
                        file_url = await get_tg_file_url(session, voice["file_id"])
                        if file_url:
                            file_data = await download_file(session, file_url)
                            await send_to_max(session, max_chat_id, text="", sender_name=name,
                                             file_data=file_data, file_name="voice.ogg")
                        continue

                    # Текст
                    if text:
                        await send_to_max(session, max_chat_id, text=text, sender_name=name)

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"TG polling error: {e}")
            await asyncio.sleep(5)

async def poll_max(session):
    marker = None
    logger.info("🔄 Запущен polling MAX...")
    while True:
        try:
            params = {"timeout": 30}
            if marker:
                params["marker"] = marker
            async with session.get(f"{MAX_API}/updates", headers=MAX_HDR,
                                   params=params, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                data = await resp.json()
                marker = data.get("marker", marker)

                for update in data.get("updates", []):
                    if update.get("update_type") != "message_created":
                        continue
                    msg = update.get("message", {})
                    chat_id = msg.get("recipient", {}).get("chat_id")
                    if not chat_id or chat_id not in MAX_TO_TG:
                        continue

                    sender = msg.get("sender", {})
                    if sender.get("is_bot"):
                        continue

                    tg_chat_id = MAX_TO_TG[chat_id]
                    name = get_max_sender_name(sender)
                    text = msg.get("body", {}).get("text", "")

                    # Вложения (фото, файлы)
                    attachments = msg.get("body", {}).get("attachments", [])
                    handled = False

                    for att in attachments:
                        att_type = att.get("type")

                        if att_type == "image":
                            photo_url = att.get("payload", {}).get("url") or att.get("payload", {}).get("photo_url")
                            if photo_url:
                                await send_to_tg(session, tg_chat_id, text=text, sender_name=name,
                                                photo_url=photo_url)
                                handled = True

                        elif att_type in ("file", "video", "audio"):
                            file_url = att.get("payload", {}).get("url")
                            fname = att.get("payload", {}).get("filename", "file")
                            if file_url:
                                await send_to_tg(session, tg_chat_id, text=text, sender_name=name,
                                                file_url=file_url, file_name=fname)
                                handled = True

                    if not handled and text:
                        await send_to_tg(session, tg_chat_id, text=text, sender_name=name)

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"MAX polling error: {e}")
            await asyncio.sleep(5)

async def main():
    logger.info("🚀 Бот синхронизации запущен!")
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            poll_telegram(session),
            poll_max(session),
        )

if __name__ == "__main__":
    asyncio.run(main())

