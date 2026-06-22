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

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === НАСТРОЙКИ ===
BOT_TOKEN = "8723169693:AAEk69a40-PlC1kWVgd-2F1MhKniitSmLn0"
DA_TOKEN = "hu4ML8HVyzRIHFbYnZdq"
DONATION_LINK = "https://www.donationalerts.com/r/mYFIVEBOT"

YANDEX_VISION_API_KEY = "AQVN29h2XBqfhDo008M8xnF3lWO6X4TkTTG2mPg"
YANDEX_GPT_API_KEY = "AQVNxq1LRjBAk8lQ8wWkxi4OMHjAd3HSLqyw-j6o"
YANDEX_FOLDER_ID = "b1gomdro48eoehuesbdn"

ADMIN_ID = 1029055491  # Твой Telegram ID

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_donations (
            donation_id INTEGER PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            processed_at TIMESTAMP
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
        cursor.execute(
            "INSERT INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        conn.commit()
        logger.info(f"👤 Новый пользователь: {user_id} (@{username})")
    conn.close()


def has_active_subscription(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT free_requests, subscription_end FROM users WHERE user_id = ?",
        (user_id,)
    )
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return False
    
    free_requests, subscription_end = result
    
    if free_requests > 0:
        return True
    
    if subscription_end:
        try:
            end_date = datetime.fromisoformat(subscription_end)
            if end_date > datetime.now():
                return True
        except:
            pass
    
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
        cursor.execute(
            "UPDATE users SET free_requests = free_requests - 1, total_solved = total_solved + 1 WHERE user_id = ?",
            (user_id,)
        )
    else:
        cursor.execute(
            "UPDATE users SET total_solved = total_solved + 1 WHERE user_id = ?",
            (user_id,)
        )
    conn.commit()
    conn.close()
    
    logger.info(f"📊 Пользователь {user_id}: осталось {max(0, free_requests - 1)} бесплатных")


def activate_subscription(user_id, days=30):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    end_date = datetime.now() + timedelta(days=days)
    cursor.execute(
        "UPDATE users SET subscription_end = ? WHERE user_id = ?",
        (end_date.isoformat(), user_id)
    )
    conn.commit()
    conn.close()
    logger.info(f"✅ Подписка активирована для {user_id} на {days} дней")


# ============================================
# === ПРОВЕРКА ДОНАТОВ ===
# ============================================
async def check_donations():
    url = "https://www.donationalerts.com/api/v1/alerts/donations"
    headers = {"Authorization": f"Bearer {DA_TOKEN}"}
    params = {"type": "1"}
    
    await asyncio.sleep(5)  # Ждём запуска бота
    logger.info("💰 Начинаю проверку донатов...")
    
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        donations = data.get("data", [])
                        
                        for donation in donations:
                            donation_id = donation["id"]
                            amount = float(donation.get("amount", 0))
                            message = donation.get("message", "") or ""
                            username = donation.get("username", "Аноним")
                            created_at = donation.get("created_at", "")
                            
                            if amount >= 100 and message.strip():
                                try:
                                    user_id = int(message.strip())
                                    
                                    conn = sqlite3.connect("users.db")
                                    cursor = conn.cursor()
                                    cursor.execute(
                                        "SELECT * FROM processed_donations WHERE donation_id = ?",
                                        (donation_id,)
                                    )
                                    
                                    if not cursor.fetchone():
                                        logger.info(f"💵 Новый донат: ID={donation_id}, {amount}₽ от {username}, msg={message}")
                                        
                                        activate_subscription(user_id, days=30)
                                        
                                        cursor.execute(
                                            "INSERT INTO processed_donations (donation_id, user_id, amount, processed_at) VALUES (?, ?, ?, ?)",
                                            (donation_id, user_id, amount, datetime.now().isoformat())
                                        )
                                        conn.commit()
                                        conn.close()
                                        
                                        try:
                                            await bot.send_message(
                                                user_id,
                                                f"✅ Оплата получена!\n\n"
                                                f"🎉 Безлимит активирован на 30 дней.\n"
                                                f"Решай сколько хочешь задач!"
                                            )
                                            logger.info(f"💰 Подписка активирована для {user_id}!")
                                        except Exception as e:
                                            logger.error(f"❌ Не удалось отправить сообщение: {e}")
                                    else:
                                        conn.close()
                                except ValueError as e:
                                    logger.warning(f"⚠️ Не удалось распарсить ID из '{message}': {e}")
                    elif resp.status == 401:
                        logger.error(f"❌ DA_TOKEN неправильный! Статус 401")
                    else:
                        error_text = await resp.text()
                        logger.error(f"❌ Ошибка DA API: {resp.status} - {error_text}")
        
        except Exception as e:
            logger.error(f"❌ Ошибка проверки донатов: {e}")
        
        await asyncio.sleep(30)


# ============================================
# === РАСПОЗНАВАНИЕ ФОТО (YANDEX VISION) ===
# ============================================
async def recognize_text_from_photo(photo_file_id):
    try:
        logger.info("📸 Начинаю распознавание фото...")
        
        file = await bot.get_file(photo_file_id)
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(photo_url) as resp:
                if resp.status != 200:
                    logger.error(f"❌ Не удалось скачать фото: {resp.status}")
                    return None
                photo_bytes = await resp.read()
        
        logger.info(f"📥 Фото загружено, размер: {len(photo_bytes)} байт")
        
        encoded_image = base64.b64encode(photo_bytes).decode('utf-8')
        
        url = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"
        headers = {
            "Authorization": f"Api-Key {YANDEX_VISION_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # ПРАВИЛЬНЫЙ формат запроса с folderId
        data = {
            "folderId": YANDEX_FOLDER_ID,
            "analyze_specs": [
                {
                    "image": {
                        "content": encoded_image
                    },
                    "features": [
                        {
                            "type": "TEXT_DETECTION"
                        }
                    ]
                }
            ]
        }
        
        logger.info("📤 Отправляю в Yandex Vision...")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                result = await resp.json()
                logger.info(f"📥 Ответ Vision: статус {resp.status}")
                
                if resp.status != 200:
                    logger.error(f"❌ Vision ошибка: {result}")
                    return None
        
        # ПРАВИЛЬНЫЙ формат извлечения текста
        try:
            text = result["results"][0]["textAnnotation"]["fullText"]
            logger.info(f"✅ Распознан текст: {text[:100]}...")
            return text
        except (KeyError, IndexError) as e:
            logger.error(f"❌ Не удалось извлечь текст: {e}")
            logger.error(f"Полный ответ: {result}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Ошибка распознавания: {e}")
        return None


# ============================================
# === РЕШЕНИЕ ЗАДАЧИ (YANDEX GPT) ===
# ============================================
async def solve_problem(problem_text):
    try:
        logger.info(f"🧠 Решаю задачу: {problem_text[:100]}...")
        
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
            "Content-Type": "application/json"
        }
        
        system_prompt = """Ты решаешь задачи строго в этом формате:

[строка 1] - условие задачи кратко
[строка 2] - шаг 1 решения
[строка 3] - шаг 2 решения
[строка 4] - шаг 3 решения (если нужно)
[строка 5] - Ответ: [результат]

ПРИМЕР:
Реши уравнение: 2x - 6 = 0
2x = 6
x = 6 / 2
Ответ: x = 3

ЗАПРЕЩЕНО:
- Объяснения и вводные слова
- "Решим", "Применим", "Итак"
- Лишние строки
- Текст вне указанного формата

Отвечай ТОЛЬКО решением."""
        
        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
            "completionOptions": {
                "stream": False,
                "temperature": 0.1,
                "maxTokens": "500"
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": problem_text}
            ]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status == 401:
                    logger.error(f"❌ GPT: токен недействителен (401)")
                    return "❌ Внутренняя ошибка. Сообщи администратору."
                elif resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"❌ GPT ошибка {resp.status}: {error_text}")
                    return "❌ Не удалось решить задачу. Попробуй ещё раз."
                
                result = await resp.json()
        
        try:
            answer = result["result"]["alternatives"][0]["message"]["text"]
            logger.info(f"✅ Решение получено: {len(answer)} символов")
            return answer
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга GPT: {e}")
            logger.error(f"Ответ: {result}")
            return "❌ Не удалось обработать ответ."
            
    except Exception as e:
        logger.error(f"❌ Ошибка решения задачи: {e}")
        return "❌ Произошла ошибка. Попробуй ещё раз."


# ============================================
# === ОБРАБОТЧИКИ КОМАНД ===
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
        f"📸 Пришли <b>ФОТО</b> задачи или напиши <b>ТЕКСТОМ</b>.\n"
        f"⚡ Решу мгновенно, кратко, без воды.\n\n"
        f"🎁 Бесплатных решений: <b>{free_requests}</b>\n"
        f"💳 Безлимит: 100₽/мес\n\n"
        f"/status — проверить подписку\n"
        f"/buy — купить безлимит",
        parse_mode="HTML"
    )


@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    user_id = message.from_user.id
    
    await message.answer(
        f"💳 <b>Безлимит на 30 дней — 100₽</b>\n\n"
        f"Решай сколько хочешь задач, без ограничений.\n\n"
        f"<b>Как оплатить:</b>\n"
        f"1️⃣ Перейди по ссылке ниже\n"
        f"2️⃣ Введи сумму: <b>100₽</b>\n"
        f"3️⃣ В поле «Сообщение» напиши свой ID:\n"
        f"<code>{user_id}</code>\n"
        f"4️⃣ Нажми «Отправить» и оплати\n\n"
        f"⏳ Подписка активируется за 1 минуту.\n\n"
        f"🔗 <a href='{DONATION_LINK}'>Перейти к оплате</a>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT free_requests, subscription_end, total_solved FROM users WHERE user_id = ?",
        (message.from_user.id,)
    )
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await message.answer("❌ Ты ещё не пользовался ботом. Напиши /start")
        return
    
    free_requests, subscription_end, total_solved = result
    
    if free_requests > 0:
        status = f"🎁 Бесплатных решений: <b>{free_requests}</b>"
    elif subscription_end:
        try:
            end_date = datetime.fromisoformat(subscription_end)
            if end_date > datetime.now():
                days_left = (end_date - datetime.now()).days
                status = f"✅ <b>Безлимит активен</b>\nОсталось дней: <b>{days_left}</b>"
            else:
                status = "❌ Подписка истекла. Продли: /buy"
        except:
            status = "❌ Ошибка проверки"
    else:
        status = "❌ Подписки нет. Купи: /buy"
    
    await message.answer(
        f"📊 <b>Твой статус:</b>\n\n"
        f"{status}\n\n"
        f"📝 Решено задач: <b>{total_solved or 0}</b>",
        parse_mode="HTML"
    )


# === КОМАНДА /RESET (ДЛЯ АДМИНА) ===
@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("❌ Эта команда только для админа")
        return
    
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET free_requests = 3 WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()
    
    await message.answer("✅ Счётчик сброшен! У тебя снова 3 бесплатных решения.")


# === КОМАНДА /ACTIVATE (ДЛЯ АДМИНА) ===
@dp.message(Command("activate"))
async def cmd_activate(message: types.Message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("❌ Эта команда только для админа")
        return
    
    activate_subscription(user_id, days=30)
    await message.answer(
        "✅ Подписка активирована на 30 дней!\n"
        "Теперь решай сколько хочешь задач!"
    )


# ============================================
# === ОБРАБОТКА ФОТО ===
# ============================================
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    get_user(user_id, message.from_user.username)
    
    if not has_active_subscription(user_id):
        await message.answer(
            "❌ <b>Бесплатные решения закончились.</b>\n\n"
            "💳 Подключи безлимит: /buy\n"
            "Решай сколько хочешь задач!",
            parse_mode="HTML"
        )
        return
    
    await message.answer("⏳ Распознаю текст с фото...")
    
    problem_text = await recognize_text_from_photo(message.photo[-1].file_id)
    
    if not problem_text:
        await message.answer(
            "❌ Не удалось распознать текст.\n\n"
            "📸 Попробуй:\n"
            "• Сделать фото чётче\n"
            "• Улучшить освещение\n"
            "• Или напиши задачу текстом"
        )
        return
    
    await message.answer(f"📝 Распознано:\n<code>{problem_text[:200]}</code>\n\n🧠 Решаю...", parse_mode="HTML")
    
    solution = await solve_problem(problem_text)
    await message.answer(solution)
    
    decrement_free_requests(user_id)


# ============================================
# === ОБРАБОТКА ТЕКСТА ===
# ============================================
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    get_user(user_id, message.from_user.username)
    
    if not has_active_subscription(user_id):
        await message.answer(
            "❌ <b>Бесплатные решения закончились.</b>\n\n"
            "💳 Подключи безлимит: /buy\n"
            "Решай сколько хочешь задач!",
            parse_mode="HTML"
        )
        return
    
    solution = await solve_problem(message.text)
    await message.answer(solution)
    
    decrement_free_requests(user_id)


# ============================================
# === ВЕБ-СЕРВЕР (ДЛЯ RENDER) ===
# ============================================
async def health_check(request):
    return web.Response(text="OK - Bot is running")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"✅ Web server started on port {port}")


# ============================================
# === ЗАПУСК ===
# ============================================
async def main():
    logger.info("🤖 Бот запускается...")
    
    await run_web_server()
    
    asyncio.create_task(check_donations())
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
