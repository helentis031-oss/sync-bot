import asyncio
import aiohttp
import logging
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== НАСТРОЙКИ ====================
TG_TOKEN = "8850239704:AAE-JYmcwNacirQwYdznCpx8QSODSTArTXE"
MAX_TOKEN = "f9LHodD0cOKFYUeNaCOLwztd4ts_PRktdviwHe9sEoBq7-j3nSXnGnXX3dw1Z-5lF8JnHZcR21OTapc2nfxi"

# Пары групп: TG chat_id -> MAX chat_id и обратно
GROUPS = [
    {"tg": -1003669566686, "max": -70934133934771},  # Группа 1
    {"tg": -1003963499983, "max": -70933950892723},  # Группа 2
    {"tg": -1003934349337, "max": -70933982612147},  # Группа 3
]

TG_TO_MAX = {g["tg"]: g["max"] for g in GROUPS}
MAX_TO_TG = {g["max"]: g["tg"] for g in GROUPS}

TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"
MAX_API = "https://platform-api.max.ru"
MAX_HEADERS = {"Authorization": MAX_TOKEN}
# ====================================================

async def send_to_tg(session, chat_id, text, sender_name):
    """Отправить сообщение в Telegram"""
    full_text = f"📨 {sender_name} (MAX):\n{text}"
    url = f"{TG_API}/sendMessage"
    payload = {"chat_id": chat_id, "text": full_text}
    try:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if not data.get("ok"):
                logger.error(f"TG send error: {data}")
            else:
                logger.info(f"✅ MAX→TG: {chat_id} | {sender_name}: {text[:50]}")
    except Exception as e:
        logger.error(f"TG send exception: {e}")

async def send_to_max(session, chat_id, text, sender_name):
    """Отправить сообщение в MAX"""
    full_text = f"📨 {sender_name} (TG):\n{text}"
    url = f"{MAX_API}/messages"
    payload = {"chat_id": chat_id, "text": full_text}
    try:
        async with session.post(url, headers=MAX_HEADERS, json=payload) as resp:
            data = await resp.json()
            if "error" in data:
                logger.error(f"MAX send error: {data}")
            else:
                logger.info(f"✅ TG→MAX: {chat_id} | {sender_name}: {text[:50]}")
    except Exception as e:
        logger.error(f"MAX send exception: {e}")

async def poll_telegram(session):
    """Получать обновления из Telegram"""
    offset = 0
    logger.info("🔄 Запущен polling Telegram...")
    while True:
        try:
            url = f"{TG_API}/getUpdates"
            params = {"offset": offset, "timeout": 30, "allowed_updates": ["message"]}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.error(f"TG getUpdates error: {data}")
                    await asyncio.sleep(5)
                    continue
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    if not msg:
                        continue
                    chat_id = msg["chat"]["id"]
                    text = msg.get("text", "")
                    if not text:
                        continue
                    # Игнорируем сообщения от ботов
                    if msg.get("from", {}).get("is_bot"):
                        continue
                    sender = msg.get("from", {})
                    sender_name = sender.get("first_name", "")
                    if sender.get("last_name"):
                        sender_name += f" {sender['last_name']}"
                    if sender.get("username"):
                        sender_name += f" (@{sender['username']})"
                    # Пересылаем в MAX если группа в списке
                    if chat_id in TG_TO_MAX:
                        max_chat_id = TG_TO_MAX[chat_id]
                        await send_to_max(session, max_chat_id, text, sender_name)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"TG polling error: {e}")
            await asyncio.sleep(5)

async def poll_max(session):
    """Получать обновления из MAX"""
    marker = None
    logger.info("🔄 Запущен polling MAX...")
    while True:
        try:
            url = f"{MAX_API}/updates"
            params = {"timeout": 30}
            if marker:
                params["marker"] = marker
            async with session.get(url, headers=MAX_HEADERS, params=params,
                                   timeout=aiohttp.ClientTimeout(total=35)) as resp:
                data = await resp.json()
                marker = data.get("marker", marker)
                for update in data.get("updates", []):
                    if update.get("update_type") != "message_created":
                        continue
                    msg = update.get("message", {})
                    chat_id = msg.get("recipient", {}).get("chat_id")
                    text = msg.get("body", {}).get("text", "")
                    if not text or not chat_id:
                        continue
                    sender = update.get("user") or msg.get("sender", {})
                    # Игнорируем сообщения от самого бота
                    if sender.get("is_bot"):
                        continue
                    sender_name = sender.get("name") or sender.get("first_name", "Пользователь")
                    # Пересылаем в TG если группа в списке
                    if chat_id in MAX_TO_TG:
                        tg_chat_id = MAX_TO_TG[chat_id]
                        await send_to_tg(session, tg_chat_id, text, sender_name)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"MAX polling error: {e}")
            await asyncio.sleep(5)

async def main():
    logger.info("🚀 Бот синхронизации запущен!")
    logger.info(f"📊 Синхронизирую {len(GROUPS)} пар групп")
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            poll_telegram(session),
            poll_max(session),
        )

if __name__ == "__main__":
    asyncio.run(main())
