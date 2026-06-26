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
BOT_TOKEN = "8723169693:AAEWKexNLVCuumgA5php5aSY_sUqSBCP8qg"
DONATION_LINK = "https://www.donationalerts.com/r/mYFIVEBOT"

YANDEX_GPT_API_KEY = "AQVNxq1LRjBAk8lQ8wWkxi4OMHjAd3HSLqyw-j6o"
YANDEX_FOLDER_ID = "b1gomdro48eoehuesbdn"

ADMIN_ID = 1029055491

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Общий HTTP клиент (для скорости)
http_session = None


async def get_http_session():
    global http_session
    if http_session is None or http_session.closed:
        http_session = aiohttp.ClientSession()
    return http_session


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
# === РАСПОЗНАВАНИЕ + РЕШЕНИЕ ОДНИМ ЗАПРОСОМ ===
# ============================================
async def recognize_and_solve(photo_file_id):
    """
    ОДИН запрос к YandexGPT Vision:
    - Распознаёт текст на фото
    - Сразу решает задачу
    - Быстро и бесплатно
    """
    try:
        logger.info(" Скачиваю фото...")
        
        file = await bot.get_file(photo_file_id)
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        
        session = await get_http_session()
        async with session.get(photo_url) as resp:
            if resp.status != 200:
                return None, "❌ Не удалось скачать фото"
            photo_bytes = await resp.read()
        
        logger.info(f"📥 Фото: {len(photo_bytes)} байт")
        
        encoded_image = base64.b64encode(photo_bytes).decode('utf-8')
        
        # === ОДИН ЗАПРОС К YANDEXGPT VISION ===
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
            "Content-Type": "application/json"
        }
        
        system_prompt = """Ты — эксперт по решению задач. На изображении может быть задача по математике, физике, химии или геометрии.

ТВОЯ ЗАДАЧА:
1. Распознай текст/формулы на изображении
2. Реши задачу пошагово
3. Дай чёткий ответ

ФОРМАТ ОТВЕТА:
📝 Задача: [распознанный текст]

🧠 Решение:
• Шаг 1: ...
• Шаг 2: ...
• Шаг 3: ...

✅ Ответ: [результат]

ПРАВИЛА:
- Отвечай на русском
- Показывай все вычисления
- Для геометрии используй теоремы
- НЕ пиши "я не уверен" — решай уверенно
- Если на фото нет задачи — напиши "На фото не видно задачи. Попробуй другое фото."

ПРИМЕР:
📝 Задача: Реши уравнение 2x - 6 = 0

🧠 Решение:
• 2x = 6
• x = 6 / 2
• x = 3

✅ Ответ: x = 3"""
        
        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-vision",
            "completionOptions": {
                "stream": False,
                "temperature": 0.1,
                "maxTokens": "2000"
            },
            "messages": [
                {
                    "role": "system",
                    "text": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{encoded_image}"
                            }
                        },
                        {
                            "type": "text",
                            "text": "Распознай текст на фото и реши задачу."
                        }
                    ]
                }
            ]
        }
        
        logger.info(" Отправляю в YandexGPT Vision...")
        
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status == 401:
                return None, " Ошибка API ключа"
            elif resp.status == 400:
                error_text = await resp.text()
                logger.error(f"❌ Vision ошибка 400: {error_text}")
                # Если vision модель не доступна — пробуем обычный GPT
                return None, None  # Сигнал для fallback
            elif resp.status != 200:
                error_text = await resp.text()
                logger.error(f"❌ Ошибка {resp.status}: {error_text}")
                return None, f"❌ Ошибка сервера: {resp.status}"
            
            result = await resp.json()
        
        try:
            answer = result["result"]["alternatives"][0]["message"]["text"]
            logger.info(f"✅ Решение получено ({len(answer)} симв.)")
            return answer, None  # answer, error=None
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга: {e}")
            return None, "❌ Не удалось обработать ответ"
            
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None, "❌ Произошла ошибка"


# ============================================
# === РЕШЕНИЕ ТЕКСТОВОЙ ЗАДАЧИ ===
# ============================================
async def solve_problem(problem_text):
    try:
        logger.info(f"🧠 Решаю: {problem_text[:100]}...")
        
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
            "Content-Type": "application/json"
        }
        
        system_prompt = """Ты решаешь задачи по математике, физике, химии, геометрии.

ФОРМАТ:
 Задача: [кратко]

🧠 Решение:
• Шаг 1: ...
• Шаг 2: ...

✅ Ответ: [результат]

БЕЗ лишних слов. Только решение."""
        
        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
            "completionOptions": {
                "stream": False,
                "temperature": 0.1,
                "maxTokens": "1500"
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": problem_text}
            ]
        }
        
        session = await get_http_session()
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status != 200:
                return "❌ Не удалось решить."
            result = await resp.json()
        
        try:
            return result["result"]["alternatives"][0]["message"]["text"]
        except:
            return "❌ Ошибка обработки."
            
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return "❌ Произошла ошибка."


# ============================================
# === ОБРАБОТЧИКИ ===
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
        f" Пришли <b>ФОТО</b> задачи — решу мгновенно!\n"
        f"⚡ Или напиши <b>ТЕКСТОМ</b>.\n\n"
        f"🎁 Бесплатных: <b>{free_requests}</b>\n"
        f"💳 Безлимит: 100₽/мес\n\n"
        f"/status — подписка\n"
        f"/buy — купить",
        parse_mode="HTML"
    )


@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    user_id = message.from_user.id
    
    await message.answer(
        f"💳 <b>Безлимит — 100₽/мес</b>\n\n"
        f"1️⃣ Перейди: {DONATION_LINK}\n"
        f"2️⃣ Сумма: <b>100₽</b>\n"
        f"3️⃣ В «Сообщение»: <code>{user_id}</code>\n"
        f"4️⃣ Оплати\n\n"
        f"После оплаты: /activate_paid\n"
        f"🔗 <a href='{DONATION_LINK}'>Оплатить</a>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@dp.message(Command("activate_paid"))
async def cmd_activate_paid(message: types.Message):
    user_id = message.from_user.id
    
    await message.answer("📝 Заявка принята! Админ проверит.")
    
    try:
        await bot.send_message(
            ADMIN_ID,
            f"💰 <b>Заявка!</b>\n\n"
            f"👤 {message.from_user.full_name}\n"
            f"🆔 <code>{user_id}</code>\n\n"
            f"/approve_{user_id} — ОК\n"
            f"/reject_{user_id} — Отмена",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")


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
        await message.answer("Напиши /start")
        return
    
    free_requests, subscription_end, total_solved = result
    
    if free_requests > 0:
        status = f"🎁 Бесплатных: <b>{free_requests}</b>"
    elif subscription_end:
        try:
            end_date = datetime.fromisoformat(subscription_end)
            if end_date > datetime.now():
                days = (end_date - datetime.now()).days
                status = f"✅ Безлимит (дней: <b>{days}</b>)"
            else:
                status = " Истёк"
        except:
            status = "❌ Ошибка"
    else:
        status = "❌ Нет подписки"
    
    await message.answer(
        f" <b>Статус:</b>\n\n"
        f"{status}\n"
        f"📝 Решено: <b>{total_solved or 0}</b>",
        parse_mode="HTML"
    )


@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET free_requests = 3 WHERE user_id = ?", (message.from_user.id,))
    conn.commit()
    conn.close()
    
    await message.answer("✅ Сброшено!")


@dp.message(Command("activate"))
async def cmd_activate(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    activate_subscription(message.from_user.id, days=30)
    await message.answer("✅ Активировано на 30 дней!")


@dp.message(F.text.startswith("/approve_"))
async def cmd_approve(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        user_id = int(message.text.replace("/approve_", ""))
        activate_subscription(user_id, days=30)
        await message.answer(f"✅ Активировано для {user_id}")
        
        try:
            await bot.send_message(user_id, "✅ Оплата подтверждена! Безлимит на 30 дней.")
        except:
            pass
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.text.startswith("/reject_"))
async def cmd_reject(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        user_id = int(message.text.replace("/reject_", ""))
        await message.answer(f"❌ Отклонено для {user_id}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# ============================================
# === ОБРАБОТКА ФОТО (БЫСТРАЯ) ===
# ============================================
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    get_user(user_id, message.from_user.username)
    
    if not has_active_subscription(user_id):
        await message.answer(
            "❌ Бесплатные решения закончились.\n\n💳 /buy",
            parse_mode="HTML"
        )
        return
    
    # Отправляем сообщение "Думаю..." и запоминаем его
    thinking_msg = await message.answer("⏳ Распознаю и решаю...")
    
    # Распознаём и решаем ОДНИМ запросом
    answer, error = await recognize_and_solve(message.photo[-1].file_id)
    
    if error:
        await thinking_msg.edit_text(error)
        return
    
    if not answer:
        # Fallback: просим ввести текст
        await thinking_msg.edit_text(
            "❌ Не удалось распознать фото.\n\n"
            "📝 <b>Напиши задачу текстом</b> — я решу!",
            parse_mode="HTML"
        )
        return
    
    # Отправляем решение
    await thinking_msg.edit_text(answer)
    
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
            "❌ Бесплатные решения закончились.\n\n💳 /buy",
            parse_mode="HTML"
        )
        return
    
    thinking_msg = await message.answer(" Решаю...")
    
    solution = await solve_problem(message.text)
    
    await thinking_msg.edit_text(solution)
    
    decrement_free_requests(user_id)


# ============================================
# === ВЕБ-СЕРВЕР ===
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


# ============================================
# === ЗАПУСК ===
# ============================================
async def main():
    logger.info("🤖 Запуск...")
    await run_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
