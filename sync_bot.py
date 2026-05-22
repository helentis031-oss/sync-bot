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
MAX_HDR = {"Authorization": MAX_TOKEN, "Content-Type": "application/json"}
# ====================================================

async def send_to_tg(session, chat_id, text, sender_name):
    full_text = f"📨 {sender_name} (MAX):\n{text}"
    url = f"{TG_API}/sendMessage"
    try:
        async with session.post(url, json={"chat_id": chat_id, "text": full_text}) as resp:
            data = await resp.json()
            if data.get("ok"):
                logger.info(f"✅ MAX→TG: {chat_id} | {sender_name}: {text[:50]}")
            else:
                logger.error(f"❌ MAX→TG error: {data}")
    except Exception as e:
        logger.error(f"❌ MAX→TG exception: {e}")

async def send_to_max(session, chat_id, text, sender_name):
    full_text = f"📨 {sender_name} (TG):\n{text}"
    url = f"{MAX_API}/messages"
    params = {"chat_id": chat_id}
    payload = {"text": full_text}
    try:
        async with session.post(url, headers=MAX_HDR, params=params, json=payload) as resp:
            data = await resp.json()
            if "error" in str(data):
                logger.error(f"❌ TG→MAX error: {data}")
            else:
                logger.info(f"✅ TG→MAX: {chat_id} | {sender_name}: {text[:50]}")
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
                    chat_id = msg["chat"]["id"]
                    text = msg.get("text", "")
                    if not text:
                        continue
                    if msg.get("from", {}).get("is_bot"):
                        continue
                    sender = msg.get("from", {})
                    name = sender.get("first_name", "")
                    if sender.get("last_name"):
                        name += f" {sender['last_name']}"
                    if sender.get("username"):
                        name += f" (@{sender['username']})"
                    if chat_id in TG_TO_MAX:
                        await send_to_max(session, TG_TO_MAX[chat_id], text, name)
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
                    text = msg.get("body", {}).get("text", "")
                    if not text or not chat_id:
                        continue
                    sender = msg.get("sender", {})
                    if sender.get("is_bot"):
                        continue
                    name = sender.get("name") or sender.get("first_name", "Пользователь")
                    if chat_id in MAX_TO_TG:
                        await send_to_tg(session, MAX_TO_TG[chat_id], text, name)
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
