import asyncio
import aiohttp
import sqlite3
import base64
import logging
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === НАСТРОЙКИ ===
BOT_TOKEN = "8763511259:AAHUONAkgzSzQgt3jZmru90io_p5rVCLW6k"
DONATION_LINK = "https://www.donationalerts.com/r/mYFIVEBOT"

YANDEX_API_KEY = "AQVNxq1LRjBAk8lQ8wWkxi4OMHjAd3HSLqyw-j6o"
YANDEX_FOLDER_ID = "b1gomdro48eoehuesbdn"

ADMIN_ID = 1029055491

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

http_session = None


async def get_http_session():
    global http_session
    if http_session is None or http_session.closed:
        timeout = aiohttp.ClientTimeout(total=60)
        http_session = aiohttp.ClientSession(timeout=timeout)
    return http_session


async def close_http_session():
    global http_session
    if http_session and not http_session.closed:
        await http_session.close()
        logger.info("✅ HTTP сессия закрыта")


# ============================================
# === БАЗА ДАННЫХ ===
# ============================================
def init_db():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            free_requests INTEGER DEFAULT 3,
            subscription_end TIMESTAMP NULL,
            username TEXT,
            total_solved INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

init_db()

def get_user(user_id, username):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        conn.commit()
        logger.info(f"👤 Новый пользователь: {user_id}")
    conn.close()

def has_active_subscription(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT free_requests, subscription_end FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if not result: return False
    free_requests, subscription_end = result
    if free_requests > 0: return True
    if subscription_end:
        try:
            if datetime.fromisoformat(subscription_end) > datetime.now(): return True
        except: pass
    return False

def decrement_free_requests(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT free_requests FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if not result:
        conn.close()
        return
    free_requests = result[0]
    if free_requests > 0:
        cursor.execute("UPDATE users SET free_requests = free_requests - 1, total_solved = total_solved + 1 WHERE user_id = ?", (user_id,))
    else:
        cursor.execute("UPDATE users SET total_solved = total_solved + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def activate_subscription(user_id, days=30):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    end_date = datetime.now() + timedelta(days=days)
    cursor.execute("UPDATE users SET subscription_end = ? WHERE user_id = ?", (end_date.isoformat(), user_id))
    conn.commit()
    conn.close()
    logger.info(f"✅ Подписка активирована для {user_id} на {days} дней")


# ============================================
# === YANDEX GPT VISION (ФОТО) ===
# ============================================
async def recognize_and_solve_photo(photo_file_id):
    try:
        file = await bot.get_file(photo_file_id)
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        
        session = await get_http_session()
        async with session.get(photo_url) as resp:
            if resp.status != 200: return None, "❌ Не удалось скачать фото"
            photo_bytes = await resp.read()
        
        encoded_image = base64.b64encode(photo_bytes).decode('utf-8')
        
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
        
        # Пробуем rc версию модели (стабильная)
        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-vision/rc",
            "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": "1500"},
            "messages": [
                {"role": "system", "text": "Реши задачу с фото. Формат:\n📝 Задача: [текст]\n🧠 Решение: [шаги]\n✅ Ответ: [результат]\nКратко, русский язык."},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}},
                    {"type": "text", "text": "Реши задачу."}
                ]}
            ]
        }
        
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status == 404:
                logger.warning("⚠️ yandexgpt-vision/rc не найден, пробуем без версии...")
                # Fallback: пробуем без указания версии
                data["modelUri"] = f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-vision"
                async with session.post(url, headers=headers, json=data) as resp2:
                    if resp2.status != 200:
                        error_text = await resp2.text()
                        logger.error(f"Vision fallback error: {error_text[:300]}")
                        return None, "❌ Модель Vision недоступна. Напиши задачу текстом."
                    result = await resp2.json()
            elif resp.status == 403:
                return None, "⚠️ Нет доступа. Проверь роль ai.vision.user и баланс"
            elif resp.status != 200:
                error_text = await resp.text()
                logger.error(f"Vision error: {error_text[:300]}")
                return None, f"❌ Ошибка Vision API ({resp.status})"
            else:
                result = await resp.json()
        
        answer = result["result"]["alternatives"][0]["message"]["text"]
        return answer, None
        
    except Exception as e:
        logger.error(f"Vision error: {e}")
        return None, "❌ Ошибка распознавания. Напиши задачу текстом."


# ============================================
# === YANDEX GPT LITE (ТЕКСТ) ===
# ============================================
async def solve_problem(problem_text):
    try:
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
        
        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite/latest",
            "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": "1000"},
            "messages": [
                {"role": "system", "text": "Реши задачу. Формат:\n📝 Задача: [текст]\n🧠 Решение: [шаги]\n✅ Ответ: [результат]\nКратко, русский язык."},
                {"role": "user", "text": problem_text}
            ]
        }
        
        session = await get_http_session()
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status == 403: return "⚠️ Ошибка доступа. Проверь баланс Yandex Cloud"
            if resp.status != 200: return "❌ Не удалось решить задачу"
            result = await resp.json()
        
        return result["result"]["alternatives"][0]["message"]["text"]
        
    except Exception as e:
        logger.error(f"GPT error: {e}")
        return "❌ Произошла ошибка при решении"


# ============================================
# === ХЕНДЛЕРЫ ===
# ============================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    get_user(message.from_user.id, message.from_user.username)
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT free_requests FROM users WHERE user_id = ?", (message.from_user.id,))
    free_requests = cursor.fetchone()[0]
    conn.close()
    
    await message.answer(
        f"👋 <b>Привет!</b> Я бот-решатель задач.\n\n"
        f"📸 Пришли <b>ФОТО</b> или напиши <b>ТЕКСТОМ</b>.\n\n"
        f"🎁 Бесплатных: <b>{free_requests}</b>\n"
        f"💳 Безлимит: 100₽/мес\n\n"
        f"/status — подписка | /buy — купить",
        parse_mode="HTML"
    )

@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    user_id = message.from_user.id
    await message.answer(
        f"💳 <b>Безлимит — 100₽/мес</b>\n\n"
        f"1️⃣ Перейди: {DONATION_LINK}\n"
        f"2️⃣ Сумма: <b>100₽</b>\n"
        f"3️⃣ Сообщение: <code>{user_id}</code>\n"
        f"4️⃣ После оплаты: /activate_paid\n\n"
        f"🔗 <a href='{DONATION_LINK}'>Оплатить</a>",
        parse_mode="HTML", disable_web_page_preview=True
    )

@dp.message(Command("activate_paid"))
async def cmd_activate_paid(message: types.Message):
    user_id = message.from_user.id
    await message.answer("📝 Заявка принята! Админ проверит.")
    try:
        await bot.send_message(ADMIN_ID, 
            f"💰 <b>Заявка!</b>\n👤 {message.from_user.full_name}\n🆔 <code>{user_id}</code>\n\n"
            f"/approve_{user_id} — ОК\n/reject_{user_id} — Отмена", parse_mode="HTML")
    except: pass

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT free_requests, subscription_end, total_solved FROM users WHERE user_id = ?", (message.from_user.id,))
    result = cursor.fetchone()
    conn.close()
    if not result:
        await message.answer("Напиши /start"); return
    
    free_requests, subscription_end, total_solved = result
    if free_requests > 0: status = f"🎁 Бесплатных: <b>{free_requests}</b>"
    elif subscription_end:
        try:
            days = (datetime.fromisoformat(subscription_end) - datetime.now()).days
            status = f"✅ Безлимит (дней: <b>{days}</b>)" if days > 0 else "❌ Истёк"
        except: status = "❌ Ошибка"
    else: status = "❌ Нет подписки"
    
    await message.answer(f"📊 <b>Статус:</b>\n{status}\n📝 Решено: <b>{total_solved or 0}</b>", parse_mode="HTML")

@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect("users.db"); cursor = conn.cursor()
    cursor.execute("UPDATE users SET free_requests = 3 WHERE user_id = ?", (message.from_user.id,))
    conn.commit(); conn.close()
    await message.answer("✅ Сброшено!")

@dp.message(Command("activate"))
async def cmd_activate(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    activate_subscription(message.from_user.id, days=30)
    await message.answer("✅ Активировано на 30 дней!")

@dp.message(F.text.startswith("/approve_"))
async def cmd_approve(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        user_id = int(message.text.replace("/approve_", ""))
        activate_subscription(user_id, days=30)
        await message.answer(f"✅ Активировано для {user_id}")
        try: await bot.send_message(user_id, "✅ Оплата подтверждена! Безлимит на 30 дней.")
        except: pass
    except Exception as e: await message.answer(f"❌ Ошибка: {e}")

@dp.message(F.text.startswith("/reject_"))
async def cmd_reject(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        user_id = int(message.text.replace("/reject_", ""))
        await message.answer(f"❌ Отклонено для {user_id}")
    except Exception as e: await message.answer(f"❌ Ошибка: {e}")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    get_user(user_id, message.from_user.username)
    if not has_active_subscription(user_id):
        await message.answer("❌ Бесплатные решения закончились.\n\n💳 /buy", parse_mode="HTML"); return
    
    thinking_msg = await message.answer("⏳ Распознаю и решаю...")
    answer, error = await recognize_and_solve_photo(message.photo[-1].file_id)
    
    if error: await thinking_msg.edit_text(error); return
    if not answer:
        await thinking_msg.edit_text("❌ Не удалось распознать.\n📝 Напиши задачу текстом!", parse_mode="HTML"); return
    
    await thinking_msg.edit_text(answer)
    decrement_free_requests(user_id)

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    get_user(user_id, message.from_user.username)
    if not has_active_subscription(user_id):
        await message.answer("❌ Бесплатные решения закончились.\n\n💳 /buy", parse_mode="HTML"); return
    
    thinking_msg = await message.answer("🧠 Решаю...")
    solution = await solve_problem(message.text)
    await thinking_msg.edit_text(solution)
    decrement_free_requests(user_id)


# ============================================
# === ВЕБ-СЕРВЕР + ЗАПУСК ===
# ============================================
async def health_check(request):
    return web.Response(text="OK")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"✅ Web server on port {port}")

async def main():
    logger.info("🤖 Запуск...")
    
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Вебхук сброшен")
    
    await run_web_server()
    
    try:
        await dp.start_polling(bot)
    finally:
        await close_http_session()

if __name__ == "__main__":
    asyncio.run(main())
