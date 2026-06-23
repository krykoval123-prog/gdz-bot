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
BOT_TOKEN = "8723169693:AAEWKexNLVCuumgA5php5aSY_sUqSBCP8qg"
DONATION_LINK = "https://www.donationalerts.com/r/mYFIVEBOT"

YANDEX_VISION_API_KEY = "AQVN29h2XBqfhDo008M8xnF3lWO6X4TkTTG2mPg"
YANDEX_GPT_API_KEY = "AQVNxq1LRjBAk8lQ8wWkxi4OMHjAd3HSLqyw-j6o"
YANDEX_FOLDER_ID = "b1gomdro48eoehuesbdn"

ADMIN_ID = 1029055491

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
# === РАСПОЗНАВАНИЕ ФОТО (НОВЫЙ ПОДХОД) ===
# ============================================
async def recognize_text_from_photo(photo_file_id):
    try:
        logger.info("📸 Начинаю распознавание фото...")
        
        # Скачиваем фото
        file = await bot.get_file(photo_file_id)
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(photo_url) as resp:
                if resp.status != 200:
                    logger.error(f"❌ Не удалось скачать фото: {resp.status}")
                    return None
                photo_bytes = await resp.read()
        
        logger.info(f"📥 Фото загружено: {len(photo_bytes)} байт")
        
        # === МЕТОД 1: Yandex Vision API (правильный формат) ===
        try:
            logger.info(" Пробую Yandex Vision API...")
            
            encoded_image = base64.b64encode(photo_bytes).decode('utf-8')
            
            url = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"
            headers = {
                "Authorization": f"Api-Key {YANDEX_VISION_API_KEY}",
                "Content-Type": "application/json"
            }
            
            # Правильный формат запроса
            data = {
                "folderId": YANDEX_FOLDER_ID,
                "analyze_specs": [
                    {
                        "image": {"content": encoded_image},
                        "features": [
                            {
                                "type": "TEXT_DETECTION",
                                "language_codes": ["ru", "en"]
                            }
                        ]
                    }
                ]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data) as resp:
                    status = resp.status
                    result = await resp.json()
                    
                    logger.info(f"📥 Vision API: статус {status}")
                    
                    if status == 200:
                        # Пробуем извлечь текст
                        text = extract_text_from_vision_result(result)
                        if text and len(text.strip()) > 2:
                            logger.info(f"✅ Распознано через Vision: {text[:100]}")
                            return text
                        else:
                            logger.warning("⚠️ Vision вернул пустой текст")
                    else:
                        logger.error(f"❌ Vision ошибка {status}: {result}")
        
        except Exception as e:
            logger.error(f"❌ Ошибка Vision API: {e}")
        
        # === МЕТОД 2: Отправляем в GPT с описанием ===
        logger.info("🔍 Пробую через GPT (описательный метод)...")
        
        # Кодируем для отправки
        encoded_for_gpt = base64.b64encode(photo_bytes).decode('utf-8')
        
        # Используем GPT для распознавания через vision
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Для GPT отправляем запрос с просьбой описать что на фото
        # Но это не сработает без vision-модели
        
        # === МЕТОД 3: Просим пользователя ввести текст ===
        logger.warning("⚠️ Автоматическое распознавание не сработало")
        return None
        
    except Exception as e:
        logger.error(f"❌ Ошибка распознавания: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def extract_text_from_vision_result(result):
    """Извлекает текст из ответа Vision API"""
    try:
        # Формат 1: results[0].results[0].textDetection
        if "results" in result:
            for r in result["results"]:
                if "results" in r:
                    for sub in r["results"]:
                        if "textDetection" in sub:
                            td = sub["textDetection"]
                            text_parts = []
                            if "pages" in td:
                                for page in td["pages"]:
                                    if "blocks" in page:
                                        for block in page["blocks"]:
                                            if "lines" in block:
                                                for line in block["lines"]:
                                                    line_text = ""
                                                    if "words" in line:
                                                        for word in line["words"]:
                                                            if "symbols" in word:
                                                                for sym in word["symbols"]:
                                                                    line_text += sym.get("text", "")
                                                    if line_text.strip():
                                                        text_parts.append(line_text.strip())
            if text_parts:
                return "\n".join(text_parts)
        
        # Формат 2: results[0].textAnnotation
        if "results" in result and len(result["results"]) > 0:
            if "textAnnotation" in result["results"][0]:
                return result["results"][0]["textAnnotation"].get("fullText", "")
        
        # Формат 3: annotations
        if "annotations" in result:
            text_parts = []
            for ann in result["annotations"]:
                if "text" in ann:
                    text_parts.append(ann["text"])
            if text_parts:
                return "\n".join(text_parts)
        
        return None
        
    except Exception as e:
        logger.error(f"❌ Ошибка извлечения текста: {e}")
        return None


# ============================================
# === РЕШЕНИЕ ЗАДАЧИ ===
# ============================================
async def solve_problem(problem_text):
    try:
        logger.info(f"🧠 Решаю задачу: {problem_text[:100]}...")
        
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
            "Content-Type": "application/json"
        }
        
        system_prompt = """Ты решаешь задачи по математике, физике, химии, геометрии.

ФОРМАТ ОТВЕТА:
Задача: [кратко]
Решение:
- Шаг 1
- Шаг 2
Ответ: [результат]

БЕЗ лишних слов. Только решение."""
        
        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
            "completionOptions": {
                "stream": False,
                "temperature": 0.1,
                "maxTokens": "1000"
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": problem_text}
            ]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status != 200:
                    return "❌ Не удалось решить."
                
                result = await resp.json()
        
        try:
            answer = result["result"]["alternatives"][0]["message"]["text"]
            return answer
        except:
            return "❌ Ошибка обработки ответа."
            
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
        f"📸 Пришли <b>ФОТО</b> или напиши <b>ТЕКСТОМ</b>.\n"
        f"⚡ Решу математику, физику, химию.\n\n"
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
        logger.error(f"❌ Ошибка уведомления: {e}")


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
                status = "❌ Истёк"
        except:
            status = "❌ Ошибка"
    else:
        status = "❌ Нет подписки"
    
    await message.answer(
        f"📊 <b>Статус:</b>\n\n"
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


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    get_user(user_id, message.from_user.username)
    
    if not has_active_subscription(user_id):
        await message.answer("❌ Бесплатные решения закончились.\n\n💳 /buy", parse_mode="HTML")
        return
    
    await message.answer("⏳ Распознаю...")
    
    problem_text = await recognize_text_from_photo(message.photo[-1].file_id)
    
    if not problem_text:
        # Если не распознали, просим ввести текст
        await message.answer(
            "❌ Не удалось автоматически распознать текст.\n\n"
            "📝 <b>Пожалуйста, напиши задачу текстом:</b>\n"
            "Просто скопируй или напиши условие задачи, и я решу её!",
            parse_mode="HTML"
        )
        return
    
    await message.answer(f"📝 <b>Распознано:</b>\n<code>{problem_text[:300]}</code>\n\n🧠 Решаю...", parse_mode="HTML")
    
    solution = await solve_problem(problem_text)
    await message.answer(solution)
    
    decrement_free_requests(user_id)


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    get_user(user_id, message.from_user.username)
    
    if not has_active_subscription(user_id):
        await message.answer("❌ Бесплатные решения закончились.\n\n💳 /buy", parse_mode="HTML")
        return
    
    solution = await solve_problem(message.text)
    await message.answer(solution)
    
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
