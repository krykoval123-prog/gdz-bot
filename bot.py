import asyncio
import aiohttp
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage

# === НАСТРОЙКИ (ЗАМЕНИ НА СВОИ) ===
BOT_TOKEN = "8723169693:AAEk69a40-PlC1kWVgd-2F1MhKniitSmLn0"
DA_TOKEN = "hu4ML8HVyzRIHFbYnZdq"
DONATION_LINK = "https://www.donationalerts.com/r/mYFIVEBOT"

YANDEX_VISION_API_KEY = "AQVN29h2XBqfhDoO008M8xnF3lWO6X4TkTTG2mPgGPT"
YANDEX_GPT_API_KEY = "AQVNwatuUPOykH-T62W2hxgRKFwnlf8iY897pAno"
YANDEX_FOLDER_ID = "b1gomdro48eoehuesbdn"

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
        end_date = datetime.fromisoformat(subscription_end)
        if end_date > datetime.now():
            return True
    
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


# === ПРОВЕРКА НОВЫХ ДОНАТОВ ===
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
                                        except:
                                            pass
                                    
                                    conn.close()
                                except ValueError:
                                    pass
        
        except Exception as e:
            print(f"Ошибка проверки донатов: {e}")
        
        await asyncio.sleep(30)


# === РАСПОЗНАВАНИЕ ТЕКСТА ===
async def recognize_text_from_photo(photo_file_id):
    file = await bot.get_file(photo_file_id)
    photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(photo_url) as resp:
            photo_bytes = await resp.read()
    
    url = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"
    headers = {"Authorization": f"Api-Key {YANDEX_VISION_API_KEY}"}
    
    data = {
        "analyzeSpecs": [
            {
                "mime": "image/jpeg",
                "content": photo_bytes.hex(),
                "features": [{"type": "TEXT_DETECTION", "textDetection": {"languageCodes": ["ru", "en"]}}]
            }
        ]
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            result = await resp.json()
    
    try:
        text = result["results"][0]["results"][0]["textAnnotations"][0]["fullText"]
        return text
    except:
        return None


# === РЕШЕНИЕ ЗАДАЧИ (СТРОГИЙ ФОРМАТ) ===
async def solve_problem(problem_text):
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # СТРОГИЙ ПРОМПТ: только вопрос, 3 строки решения, ответ
    system_prompt = """Ты решаешь задачи. Отвечай СТРОГО в этом формате, без исключений:

[первая строка] - повтори вопрос/задачу кратко
[вторая строка] - первый шаг решения
[третья строка] - второй шаг решения
[четвертая строка] - третий шаг решения
[пятая строка] - Ответ: [число/значение]

ПРИМЕР:
Реши уравнение: x² - 5x + 6 = 0
D = 25 - 24 = 1
x₁ = (5 + 1) / 2 = 3
x₂ = (5 - 1) / 2 = 2
Ответ: x₁ = 3, x₂ = 2

ЗАПРЕЩЕНО:
- Объяснения
- Вводные слова
- Лишние строки
- Формулировки типа "Решим", "Применим"
- Что-либо кроме 5 строк в указанном формате"""
    
    data = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.1,
            "maxTokens": "500"
        },
        "messages": [
            {
                "role": "system",
                "text": system_prompt
            },
            {
                "role": "user",
                "text": problem_text
            }
        ]
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            result = await resp.json()
    
    try:
        answer = result["result"]["alternatives"][0]["message"]["text"]
        return answer
    except:
        return "❌ Не удалось решить задачу. Попробуй переформулировать."


# === СТАРТ ===
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
        f"📸 Пришли мне ФОТО задачи или напиши ТЕКСТОМ.\n"
        f"⚡ Решу мгновенно, кратко, без воды.\n\n"
        f"🎁 У тебя 3 бесплатных решения.\n"
        f"💳 Дальше — безлимит за 100₽/мес.\n\n"
        f"Осталось бесплатных решений: <b>{free_requests}</b>\n\n"
        f"Команды:\n"
        f"/status — проверить подписку\n"
        f"/buy — купить безлимит",
        parse_mode="HTML"
    )


# === ОБРАБОТКА ФОТО ===
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    get_user(user_id, username)
    
    if not has_active_subscription(user_id):
        await message.answer(
            "❌ Бесплатные решения закончились.\n\n"
            "💳 Подключи безлимит за 100₽/мес: /buy\n"
            "Решай сколько хочешь задач!"
        )
        return
    
    await message.answer("⏳ Распознаю текст с фото...")
    
    photo_file_id = message.photo[-1].file_id
    problem_text = await recognize_text_from_photo(photo_file_id)
    
    if not problem_text:
        await message.answer("❌ Не удалось распознать текст. Попробуй четче фото или напиши текстом.")
        return
    
    solution = await solve_problem(problem_text)
    
    await message.answer(solution)
    
    decrement_free_requests(user_id)


# === ОБРАБОТКА ТЕКСТА ===
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    get_user(user_id, username)
    
    if not has_active_subscription(user_id):
        await message.answer(
            "❌ Бесплатные решения закончились.\n\n"
            "💳 Подключи безлимит за 100₽/мес: /buy\n"
            "Решай сколько хочешь задач!"
        )
        return
    
    problem_text = message.text
    
    solution = await solve_problem(problem_text)
    
    await message.answer(solution)
    
    decrement_free_requests(user_id)


# === ПОКУПКА ===
@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    user_id = message.from_user.id
    
    await message.answer(
        f"💳 Безлимит на 30 дней — 100₽\n\n"
        f"Решай сколько хочешь задач, без ограничений.\n\n"
        f"<b>Как оплатить:</b>\n"
        f"1️⃣ Перейди по ссылке: {DONATION_LINK}\n"
        f"2️⃣ Введи сумму: <b>100₽</b>\n"
        f"3️⃣ В поле 'Сообщение' напиши свой Telegram ID:\n"
        f"   <code>{user_id}</code>\n"
        f"4️⃣ Нажми 'Отправить'\n\n"
        f"⏳ Подписка активируется автоматически в течение 1 минуты.\n\n"
        f"🔗 <a href='{DONATION_LINK}'>Перейти к оплате</a>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# === СТАТУС ===
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
        status = f"🎁 Осталось бесплатных решений: {free_requests}"
    elif subscription_end:
        end_date = datetime.fromisoformat(subscription_end)
        days_left = (end_date - datetime.now()).days
        if days_left > 0:
            status = f"✅ Безлимит активен. Осталось дней: {days_left}"
        else:
            status = "❌ Подписка истекла. Продли: /buy"
    else:
        status = "❌ Подписки нет. Купи: /buy"
    
    await message.answer(f"📊 Твой статус:\n\n{status}")


# === ЗАПУСК ===
async def main():
    print("🤖 Бот запущен!")
    asyncio.create_task(check_donations())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())