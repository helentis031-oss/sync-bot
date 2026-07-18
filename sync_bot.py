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
# ВАЖНО: с 19 июля 2026 MAX требует ходить через platform-api2.max.ru
# (platform-api.max.ru отключают) и требует, чтобы на сервере был установлен
# сертификат Минцифры (Russian Trusted Root CA), иначе TLS-соединение будет
# падать с ошибкой проверки сертификата. См. инструкцию в конце файла.
MAX_API = "https://platform-api2.max.ru"
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

# ==================== ЗАГРУЗКА ФАЙЛОВ В MAX ====================
# В MAX загрузка файла — это ДВА шага:
#   1) POST /uploads?type=image|video|audio|file -> {"url": "https://..."}
#   2) POST файла (multipart, поле "data") на этот url -> токен для вложения
# Одним запросом (как было раньше) токен не приходит, поэтому файлы/видео
# из ТГ в MAX не показывались.

async def upload_to_max(session, upload_type: str, data: bytes, filename: str, content_type: str):
    """Загружает файл в MAX и возвращает JSON с токеном/photos для вложения, либо None при ошибке"""
    try:
        # Шаг 1: получаем URL для загрузки
        async with session.post(f"{MAX_API}/uploads", headers=MAX_HDR,
                                params={"type": upload_type}) as resp:
            meta = await resp.json()

        upload_url = meta.get("url")
        if not upload_url:
            logger.error(f"❌ MAX upload: нет url в ответе /uploads ({upload_type}): {meta}")
            return None

        # Шаг 2: заливаем сам файл на полученный url
        form = aiohttp.FormData()
        form.add_field("data", data, filename=filename, content_type=content_type)
        async with session.post(upload_url, data=form) as resp:
            result = await resp.json()

        if "token" not in result and "photos" not in result:
            logger.error(f"❌ MAX upload: нет token/photos в ответе загрузки ({upload_type}): {result}")
            return None

        return result
    except Exception as e:
        logger.error(f"❌ MAX upload exception ({upload_type}): {e}")
        return None

async def get_max_video_url(session, video_token: str) -> str:
    """
    Входящее видео-вложение из MAX содержит только token, а не прямую ссылку
    на файл (в отличие от фото). Чтобы скачать сам видеофайл, нужно отдельно
    запросить GET /videos/{videoToken} — он вернёт объект urls с несколькими
    вариантами качества. Берём лучший доступный вариант.
    """
    try:
        async with session.get(f"{MAX_API}/videos/{video_token}", headers=MAX_HDR) as resp:
            data = await resp.json()
    except Exception as e:
        logger.error(f"❌ MAX get video info exception: {e}")
        return None

    urls = data.get("urls") or {}
    if not urls:
        logger.error(f"❌ MAX video: нет urls в ответе для token={video_token}: {data}")
        return None

    # Предпочитаем ключи с явным указанием качества (MP4_1080, MP4_720, ...),
    # сортируя по числу в названии по убыванию; если чисел нет — берём первый попавшийся.
    def quality_key(k):
        digits = "".join(ch for ch in k if ch.isdigit())
        return int(digits) if digits else -1

    best_key = sorted(urls.keys(), key=quality_key, reverse=True)[0]
    return urls[best_key]

def build_max_payload(upload_result: dict):
    """Строит payload вложения из результата upload_to_max (учитывает разный формат для фото)"""
    if "photos" in upload_result:
        return {"photos": upload_result["photos"]}
    if "token" in upload_result:
        return {"token": upload_result["token"]}
    return None
# ==================================================================

async def send_to_tg(session, chat_id, text=None, sender_name=None,
                      media_type=None, media_url=None, file_name=None):
    """
    Отправить сообщение в Telegram.
    media_type: None | 'photo' | 'video' | 'audio' | 'document'
    Фото и видео уходят через sendPhoto/sendVideo, чтобы показывались превью,
    а не просто прикреплённый файл.
    """
    caption = f"📨 {sender_name} (MAX):\n{text}" if text else f"📨 {sender_name} (MAX):"

    method_by_type = {
        "photo": ("sendPhoto", "photo", "photo.jpg", "image/jpeg"),
        "video": ("sendVideo", "video", "video.mp4", "video/mp4"),
        "audio": ("sendAudio", "audio", "audio.mp3", "audio/mpeg"),
        "document": ("sendDocument", "document", "file", "application/octet-stream"),
    }

    try:
        if media_type and media_url:
            method, field, default_name, default_ct = method_by_type[media_type]
            file_data = await download_file(session, media_url)

            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field("caption", caption)
            form.add_field(field, file_data,
                           filename=file_name or default_name,
                           content_type=default_ct)
            async with session.post(f"{TG_API}/{method}", data=form) as resp:
                data = await resp.json()
                if data.get("ok"):
                    logger.info(f"✅ MAX→TG {media_type}: {chat_id} | {sender_name}")
                else:
                    logger.error(f"❌ MAX→TG {media_type} error: {data}")
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

async def send_to_max(session, chat_id, text=None, sender_name=None,
                       photo_data=None, photo_name=None,
                       video_data=None, video_name=None,
                       audio_data=None, audio_name=None,
                       file_data=None, file_name=None):
    """
    Отправить сообщение в MAX.
    Фото и видео уходят как вложения типа image/video, чтобы показывались
    превью, а не как обычный файл.
    """
    caption = f"📨 {sender_name} (TG):\n{text}" if text else f"📨 {sender_name} (TG):"

    # (upload_type, attachment_type, data, filename, content_type)
    media = None
    if photo_data:
        media = ("image", "image", photo_data, photo_name or "photo.jpg", "image/jpeg")
    elif video_data:
        media = ("video", "video", video_data, video_name or "video.mp4", "video/mp4")
    elif audio_data:
        media = ("audio", "audio", audio_data, audio_name or "audio.ogg", "audio/ogg")
    elif file_data:
        media = ("file", "file", file_data, file_name or "file", "application/octet-stream")

    try:
        if media:
            upload_type, att_type, data, fname, content_type = media
            upload_result = await upload_to_max(session, upload_type, data, fname, content_type)
            payload = build_max_payload(upload_result) if upload_result else None

            if payload:
                body = {"text": caption, "attachments": [{"type": att_type, "payload": payload}]}
                async with session.post(f"{MAX_API}/messages", headers=MAX_HDR,
                                        params={"chat_id": chat_id}, json=body) as resp:
                    data = await resp.json()
                    if data.get("error") or data.get("code"):
                        logger.error(f"❌ TG→MAX {att_type} send error: {data}")
                    else:
                        logger.info(f"✅ TG→MAX {att_type}: {chat_id} | {sender_name}")
            else:
                # Если загрузка не удалась — отправляем хотя бы текст с пометкой
                fallback_label = {"image": "фото", "video": "видео", "audio": "аудио", "file": fname}[att_type]
                async with session.post(f"{MAX_API}/messages", headers=MAX_HDR,
                                        params={"chat_id": chat_id},
                                        json={"text": caption + f"\n[не удалось загрузить {fallback_label}]"}) as resp:
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

                    # Стикер (отправляем как изображение, чтобы показывалось превью)
                    if msg.get("sticker"):
                        sticker = msg["sticker"]
                        file_url = await get_tg_file_url(session, sticker["file_id"])
                        if file_url:
                            photo_data = await download_file(session, file_url)
                            await send_to_max(session, max_chat_id, text="", sender_name=name,
                                             photo_data=photo_data, photo_name="sticker.webp")
                        continue

                    # Видео
                    if msg.get("video"):
                        file_url = await get_tg_file_url(session, msg["video"]["file_id"])
                        if file_url:
                            video_data = await download_file(session, file_url)
                            await send_to_max(session, max_chat_id, text=text, sender_name=name,
                                             video_data=video_data, video_name="video.mp4")
                        continue

                    # Видеосообщение (кружок)
                    if msg.get("video_note"):
                        file_url = await get_tg_file_url(session, msg["video_note"]["file_id"])
                        if file_url:
                            video_data = await download_file(session, file_url)
                            await send_to_max(session, max_chat_id, text=text, sender_name=name,
                                             video_data=video_data, video_name="video_note.mp4")
                        continue

                    # Голосовое
                    if msg.get("voice"):
                        file_url = await get_tg_file_url(session, msg["voice"]["file_id"])
                        if file_url:
                            audio_data = await download_file(session, file_url)
                            await send_to_max(session, max_chat_id, text="", sender_name=name,
                                             audio_data=audio_data, audio_name="voice.ogg")
                        continue

                    # Аудио (музыка)
                    if msg.get("audio"):
                        file_url = await get_tg_file_url(session, msg["audio"]["file_id"])
                        if file_url:
                            audio_data = await download_file(session, file_url)
                            await send_to_max(session, max_chat_id, text=text, sender_name=name,
                                             audio_data=audio_data, audio_name="audio.mp3")
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

                    # Вложения (фото, видео, аудио, файлы)
                    attachments = msg.get("body", {}).get("attachments", [])
                    handled = False

                    # Сопоставление типа вложения MAX -> (media_type для TG, ключи с url в payload)
                    for att in attachments:
                        att_type = att.get("type")
                        payload = att.get("payload", {})

                        if att_type == "image":
                            media_url = payload.get("url") or payload.get("photo_url")
                            if media_url:
                                await send_to_tg(session, tg_chat_id, text=text, sender_name=name,
                                                media_type="photo", media_url=media_url)
                                handled = True

                        elif att_type == "video":
                            # У видео из MAX обычно нет прямого url — есть только token,
                            # по которому нужно отдельно запросить ссылку на скачивание.
                            media_url = payload.get("url")
                            if not media_url and payload.get("token"):
                                media_url = await get_max_video_url(session, payload["token"])
                            if media_url:
                                await send_to_tg(session, tg_chat_id, text=text, sender_name=name,
                                                media_type="video", media_url=media_url)
                                handled = True
                            else:
                                logger.error(f"❌ MAX→TG video: не удалось получить ссылку на видео (payload={payload})")

                        elif att_type == "audio":
                            media_url = payload.get("url")
                            if media_url:
                                await send_to_tg(session, tg_chat_id, text=text, sender_name=name,
                                                media_type="audio", media_url=media_url)
                                handled = True

                        elif att_type == "file":
                            media_url = payload.get("url")
                            fname = payload.get("filename", "file")
                            if media_url:
                                await send_to_tg(session, tg_chat_id, text=text, sender_name=name,
                                                media_type="document", media_url=media_url, file_name=fname)
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

# ==================== УСТАНОВКА СЕРТИФИКАТА МИНЦИФРЫ (Ubuntu/Debian) ====================
# Нужно один раз выполнить на сервере, иначе с 19 июля 2026 запросы к
# platform-api2.max.ru будут падать с ошибкой проверки TLS-сертификата:
#
#   sudo wget -O /usr/local/share/ca-certificates/russian_trusted_root_ca.crt \
#       https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer
#   sudo wget -O /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt \
#       https://gu-st.ru/content/Other/doc/russian_trusted_sub_ca.cer
#   sudo update-ca-certificates
#
# После этого системные сертификаты (которые использует aiohttp/Python) будут
# доверять MAX API.
# ==========================================================================================
