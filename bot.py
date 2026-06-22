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

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===
BOT_TOKEN = os.getenv("8723169693:AAEk69a40-PlC1kWVgd-2F1MhKniitSmLn0")
DA_TOKEN = os.getenv("hu4ML8HVyzRIHFbYnZdq")
DONATION_LINK = os.getenv("https://www.donationalerts.com/r/mYFIVEBOT")
YANDEX_VISION_API_KEY = os.getenv("AQVN29h2XBqfhDoO008M8xnF3lWO6X4TkTTG2mPgGPT")
YANDEX_GPT_API_KEY = os.getenv("AQVNwatuUPOykH-T62W2hxgRKFwnlf8iY897pAno")
YANDEX_FOLDER_ID = os.getenv("b1gomdro48eoehuesbdn")

if not all([BOT_TOKEN, DA_TOKEN, YANDEX_VISION_API_KEY, YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID]):
    logger.error("❌ Не все переменные окружения установлены!")
    logger.error(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
    logger.error(f"DA_TOKEN: {'✅' if DA_TOKEN else '❌'}")
    logger.error(f"YANDEX_VISION_API_KEY: {'✅' if YANDEX_VISION_API_KEY else '❌'}")
    logger.error(f"YANDEX_GPT_API_KEY: {'✅' if YANDEX_GPT_API_KEY else '❌'}")
    logger.error(f"YANDEX_FOLDER_ID: {'✅' if YANDEX_FOLDER_ID else '❌'}")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            free_requests INTEGER DEFAULT 3,
            subscription_end TIMESTAMP NULL,
            username TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_donations (
            donation_id INTEGER PRIMARY KEY
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
        logger.info(f"👤 Новый пользователь: {user_id}")
    conn.close()


def has_active_subscription(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT free_requests, subscription_end FROM users WHERE user_id = ?", (user_id,))
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
    free_requests = cursor.fetchone()[0]
    
    if free_requests > 0:
        cursor.execute(
            "UPDATE users SET free_requests = free_requests - 1 WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()
        logger.info(f"📊 У пользователя {user_id} осталось {free_requests - 1} бесплатных решений")
    
    conn.close()


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
    logger.info(f"✅ Подписка активирована для пользователя {user_id}")


# === ПРОВЕРКА ДОНАТОВ ===
async def check_donations():
    url = "https://www.donationalerts.com/api/v1/alerts/donations"
    headers = {"Authorization": f"Bearer {DA_TOKEN}"}
    params = {"type": "1"}
    
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        donations = data.get("data", [])
                        
                        for donation in donations:
                            donation_id = donation["id"]
                            amount = donation["amount"]
                            message = donation.get("message", "")
                            
                            if amount >= 100 and message:
                                try:
                                    user_id = int(message.strip())
                                    
                                    conn = sqlite3.connect("users.db")
                                    cursor = conn.cursor()
                                    cursor.execute("SELECT * FROM processed_donations WHERE donation_id = ?", (donation_id,))
                                    if not cursor.fetchone():
                                        activate_subscription(user_id, days=30)
                                        
                                        cursor.execute(
                                            "INSERT INTO processed_donations (donation_id) VALUES (?)",
                                            (donation_id,)
                                        )
                                        conn.commit()
                                        
                                        try:
                                            await bot.send_message(
                                                user_id,
                                                "✅ Оплата получена!\n\n"
                                                "🎉 Безлимит активирован на 30 дней.\n"
                                                "Решай сколько хочешь задач!"
                                            )
                                            logger.info(f"💰 Оплата 100₽ от пользователя {user_id}")
                                        except:
                                            pass
                                    
                                    conn.close()
                                except ValueError:
                                    pass
        
        except Exception as e:
            logger.error(f"❌ Ошибка проверки донатов: {e}")
        
        await asyncio.sleep(30)


# === РАСПОЗНАВАНИЕ ТЕКСТА ===
async def recognize_text_from_photo(photo_file_id):
    try:
        logger.info("📸 Начинаю распознавание фото...")
        
        file = await bot.get_file(photo_file_id)
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(photo_url) as resp:
                photo_bytes = await resp.read()
        
        logger.info(f"📥 Фото загружено, размер: {len(photo_bytes)} байт")
        
        encoded_image = base64.b64encode(photo_bytes).decode('utf-8')
        
        url = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"
        headers = {
            "Authorization": f"Api-Key {YANDEX_VISION_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "requests": [
                {
                    "image": {
                        "content": encoded_image
                    },
                    "features": [
                        {
                            "type": "TEXT_DETECTION",
                            "maxResults": 1
                        }
                    ]
                }
            ]
        }
        
        logger.info("📤 Отправляю в Yandex Vision...")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                result = await resp.json()
                logger.info(f"📥 Ответ от Vision API: {resp.status}")
        
        try:
            text = result["responses"][0]["textAnnotations"][0]["description"]
            logger.info(f"✅ Распознан текст: {text[:100]}...")
            return text
        except (KeyError, IndexError) as e:
            logger.error(f"❌ Не удалось извлечь текст: {e}")
            logger.error(f"Полный ответ API: {result}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Ошибка распознавания: {e}")
        return None


# === РЕШЕНИЕ ЗАДАЧИ ===
async def solve_problem(problem_text):
    try:
        logger.info(f"🧠 Решаю задачу: {problem_text[:50]}...")
        
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
            "Content-Type": "application/json"
        }
        
        system_prompt = """Ты решаешь задачи строго в этом формате:

[строка 1] - вопрос/задача
[строка 2] - шаг 1 решения
[строка 3] - шаг 2 решения  
[строка 4] - шаг 3 решения (если нужно)
[строка 5] - Ответ: [результат]

БЕЗ объяснений, БЕЗ вводных слов. Только решение."""
        
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
                result = await resp.json()
                logger.info(f"📥 Ответ от GPT API: {resp.status}")
        
        try:
            answer = result["result"]["alternatives"][0]["message"]["text"]
            logger.info(f"✅ Решение: {answer[:100]}...")
            return answer
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга ответа GPT: {e}")
            return "❌ Не удалось решить задачу."
            
    except Exception as e:
        logger.error(f"❌ Ошибка решения задачи: {e}")
        return "❌ Произошла ошибка при решении."


# === ОБРАБОТЧИКИ ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    get_user(message.from_user.id, message.from_user.username)
    
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT free_requests FROM users WHERE user_id = ?", (message.from_user.id,))
    free_requests = cursor.fetchone()[0]
    conn.close()
    
    await message.answer(
        f"👋 Привет! Я бот-решатель задач.\n\n"
        f"📸 Пришли ФОТО или напиши ТЕКСТОМ.\n"
        f"⚡ Решу мгновенно, кратко, без воды.\n\n"
        f"🎁 Бесплатных решений: <b>{free_requests}</b>\n"
        f"💳 Безлимит: 100₽/мес\n\n"
        f"/status — проверить подписку\n"
        f"/buy — купить безлимит",
        parse_mode="HTML"
    )


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    
    get_user(user_id, message.from_user.username)
    
    if not has_active_subscription(user_id):
        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()
        cursor.execute("SELECT free_requests FROM users WHERE user_id = ?", (user_id,))
        free = cursor.fetchone()[0]
        conn.close()
        
        if free <= 0:
            await message.answer(
                "❌ Бесплатные решения закончились.\n\n"
                "💳 Подключи безлимит: /buy"
            )
            return
    
    await message.answer("⏳ Распознаю текст...")
    
    problem_text = await recognize_text_from_photo(message.photo[-1].file_id)
    
    if not problem_text:
        await message.answer("❌ Не удалось распознать текст. Отправь более четкое фото или напиши текстом.")
        return
    
    solution = await solve_problem(problem_text)
    await message.answer(solution)
    
    decrement_free_requests(user_id)


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    
    get_user(user_id, message.from_user.username)
    
    if not has_active_subscription(user_id):
        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()
        cursor.execute("SELECT free_requests FROM users WHERE user_id = ?", (user_id,))
        free = cursor.fetchone()[0]
        conn.close()
        
        if free <= 0:
            await message.answer(
                "❌ Бесплатные решения закончились.\n\n"
                "💳 Подключи безлимит: /buy"
            )
            return
    
    solution = await solve_problem(message.text)
    await message.answer(solution)
    
    decrement_free_requests(user_id)


@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    user_id = message.from_user.id
    
    await message.answer(
        f"💳 Безлимит на 30 дней — 100₽\n\n"
        f"Как оплатить:\n"
        f"1️⃣ Перейди: {DONATION_LINK}\n"
        f"2️⃣ Введи: 100₽\n"
        f"3️⃣ В сообщение напиши свой ID: <code>{user_id}</code>\n"
        f"4️⃣ Отправь\n\n"
        f"⏳ Активация: 1 минута",
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT free_requests, subscription_end FROM users WHERE user_id = ?",
        (message.from_user.id,)
    )
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await message.answer("❌ Ты еще не пользовался ботом. Напиши /start")
        return
    
    free_requests, subscription_end = result
    
    if free_requests > 0:
        status = f"🎁 Осталось бесплатных: {free_requests}"
    elif subscription_end:
        try:
            end_date = datetime.fromisoformat(subscription_end)
            days_left = (end_date - datetime.now()).days
            if days_left > 0:
                status = f"✅ Безлимит активен. Дней: {days_left}"
            else:
                status = "❌ Подписка истекла. Продли: /buy"
        except:
            status = "❌ Ошибка проверки подписки"
    else:
        status = "❌ Подписки нет. Купи: /buy"
    
    await message.answer(f"📊 Твой статус:\n\n{status}")


# === ВЕБ-СЕРВЕР ДЛЯ RENDER ===
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
    print(f"✅ Web server started on port {port}")


# === ЗАПУСК ===
async def main():
    print("🤖 Бот запускается...")
    
    # Запускаем веб-сервер (для Render)
    await run_web_server()
    
    # Запускаем проверку донатов
    asyncio.create_task(check_donations())
    
    # Запускаем бота
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
