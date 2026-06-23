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
# === РАСПОЗНАВАНИЕ ФОТО (ИСПРАВЛЕНО) ===
# ============================================
async def recognize_text_from_photo(photo_file_id):
    try:
        logger.info("📸 Начинаю распознавание фото...")
        
        # Скачиваем фото из Telegram
        file = await bot.get_file(photo_file_id)
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(photo_url) as resp:
                if resp.status != 200:
                    logger.error(f"❌ Не удалось скачать фото: {resp.status}")
                    return None
                photo_bytes = await resp.read()
        
        logger.info(f"📥 Фото загружено: {len(photo_bytes)} байт")
        
        # Кодируем в base64
        encoded_image = base64.b64encode(photo_bytes).decode('utf-8')
        
        # Отправляем в Yandex Vision
        url = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"
        headers = {
            "Authorization": f"Api-Key {YANDEX_VISION_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "folderId": YANDEX_FOLDER_ID,
            "analyze_specs": [
                {
                    "image": {"content": encoded_image},
                    "features": [{"type": "TEXT_DETECTION"}]
                }
            ]
        }
        
        logger.info(f"📤 Отправляю в Vision (folderId: {YANDEX_FOLDER_ID})...")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                status = resp.status
                result = await resp.json()
                
                logger.info(f"📥 Ответ Vision: статус {status}")
                
                if status != 200:
                    logger.error(f"❌ Vision ошибка: {result}")
                    return None
        
        # === ИЗВЛЕЧЕНИЕ ТЕКСТА (пробуем все форматы) ===
        text = None
        
        # Формат 1: Через symbols (самый надёжный)
        try:
            pages = result["results"][0]["results"][0]["textDetection"]["pages"]
            text_parts = []
            for page in pages:
                for block in page.get("blocks", []):
                    for line in block.get("lines", []):
                        words = []
                        for word in line.get("words", []):
                            symbols = word.get("symbols", [])
                            word_text = "".join(s.get("text", "") for s in symbols)
                            if word_text:
                                words.append(word_text)
                        if words:
                            text_parts.append(" ".join(words))
            text = "\n".join(text_parts)
            if text and len(text.strip()) > 0:
                logger.info(f"✅ Текст извлечён (формат symbols): {len(text)} симв.")
        except Exception as e:
            logger.warning(f"⚠️ Формат symbols не сработал: {e}")
        
        # Формат 2: textAnnotation.fullText
        if not text:
            try:
                text = result["results"][0]["textAnnotation"]["fullText"]
                if text and len(text.strip()) > 0:
                    logger.info(f"✅ Текст извлечён (формат textAnnotation): {len(text)} симв.")
            except Exception as e:
                logger.warning(f"⚠️ Формат textAnnotation не сработал: {e}")
        
        # Формат 3: Перебор всех возможных структур
        if not text:
            try:
                text_parts = []
                for r in result.get("results", []):
                    for sub in r.get("results", []):
                        td = sub.get("textDetection", {})
                        for page in td.get("pages", []):
                            for block in page.get("blocks", []):
                                for line in block.get("lines", []):
                                    line_text = ""
                                    for word in line.get("words", []):
                                        for sym in word.get("symbols", []):
                                            line_text += sym.get("text", "")
                                    if line_text.strip():
                                        text_parts.append(line_text.strip())
                text = "\n".join(text_parts)
                if text:
                    logger.info(f"✅ Текст извлечён (формат перебор): {len(text)} симв.")
            except Exception as e:
                logger.warning(f"⚠️ Формат перебор не сработал: {e}")
        
        # Логируем полный ответ, если ничего не извлекли
        if not text or len(text.strip()) < 3:
            logger.error(f"❌ Текст не найден или слишком короткий")
            logger.error(f"📋 Полный ответ Vision API:")
            logger.error(result)
            return None
        
        logger.info(f"✅ Распознано: {text[:200]}...")
        return text.strip()
        
    except Exception as e:
        logger.error(f"❌ Ошибка распознавания: {e}")
        import traceback
        logger.error(traceback.format_exc())
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
        
        system_prompt = """Ты — эксперт по решению школьных и вузовских задач (математика, физика, химия, геометрия, алгебра).

ПРАВИЛА ОТВЕТА:
1. Кратко переформулируй задачу
2. Пошагово реши с вычислениями
3. Дай чёткий ответ в конце

ФОРМАТ:
Задача: [переформулировка]
Решение:
- Шаг 1: ...
- Шаг 2: ...
- Шаг 3: ...
Ответ: [итоговый результат]

ПРИМЕРЫ:

Задача: Реши уравнение 2x - 6 = 0
Решение:
- 2x = 6
- x = 6 / 2
- x = 3
Ответ: x = 3

Задача: В треугольнике ABC угол A = 90°, AB = 3, AC = 4. Найди BC.
Решение:
- По теореме Пифагора: BC² = AB² + AC²
- BC² = 9 + 16 = 25
- BC = √25 = 5
Ответ: BC = 5

ОСОБЫЕ УКАЗАНИЯ:
- Для геометрии: используй теоремы, формулы
- Для уравнений: показывай все шаги
- Для задач с параметрами: рассматривай все случаи
- Отвечай на русском языке
- НЕ добавляй вводные слова ("Итак", "Давайте решим")
- НЕ пиши лишних объяснений
- Для изображений с формулами — распознай и реши"""
        
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
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status == 401:
                    logger.error("❌ GPT: токен недействителен")
                    return "❌ Внутренняя ошибка. Сообщи администратору."
                elif resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"❌ GPT ошибка {resp.status}: {error_text}")
                    return "❌ Не удалось решить задачу. Попробуй ещё раз."
                
                result = await resp.json()
        
        try:
            answer = result["result"]["alternatives"][0]["message"]["text"]
            logger.info(f"✅ Решение получено ({len(answer)} симв.)")
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
        f"⚡ Решу математику, физику, химию, геометрию.\n\n"
        f"🎁 Бесплатных решений: <b>{free_requests}</b>\n"
        f"💳 Безлимит: 100₽/мес\n\n"
        f"/status — подписка\n"
        f"/buy — купить безлимит",
        parse_mode="HTML"
    )


@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    user_id = message.from_user.id
    
    await message.answer(
        f"💳 <b>Безлимит на 30 дней — 100₽</b>\n\n"
        f"<b>Как оплатить:</b>\n"
        f"1️⃣ Перейди: {DONATION_LINK}\n"
        f"2️⃣ Сумма: <b>100₽</b>\n"
        f"3️⃣ В «Сообщение» напиши:\n<code>{user_id}</code>\n"
        f"4️⃣ Оплати\n\n"
        f"⏳ После оплаты напиши: /activate_paid\n"
        f"🔗 <a href='{DONATION_LINK}'>Перейти к оплате</a>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@dp.message(Command("activate_paid"))
async def cmd_activate_paid(message: types.Message):
    user_id = message.from_user.id
    
    await message.answer(
        f"📝 <b>Заявка принята!</b>\n\n"
        f"Админ проверит оплату в течение 10 минут и активирует подписку.",
        parse_mode="HTML"
    )
    
    try:
        await bot.send_message(
            ADMIN_ID,
            f"💰 <b>Новая заявка на оплату!</b>\n\n"
            f"👤 {message.from_user.full_name}\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"📧 @{message.from_user.username or 'нет'}\n\n"
            f"Проверь Donation Alerts и нажми:\n"
            f"/approve_{user_id} — активировать\n"
            f"/reject_{user_id} — отклонить",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Не удалось уведомить админа: {e}")


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
        await message.answer("❌ Напиши /start")
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
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Только для админа")
        return
    
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET free_requests = 3 WHERE user_id = ?",
        (message.from_user.id,)
    )
    conn.commit()
    conn.close()
    
    await message.answer("✅ Счётчик сброшен! 3 бесплатных решения.")


# === КОМАНДА /ACTIVATE (ДЛЯ АДМИНА) ===
@dp.message(Command("activate"))
async def cmd_activate(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Только для админа")
        return
    
    activate_subscription(message.from_user.id, days=30)
    await message.answer("✅ Подписка активирована на 30 дней!")


# === ОДОБРЕНИЕ ОПЛАТЫ АДМИНОМ ===
@dp.message(F.text.startswith("/approve_"))
async def cmd_approve(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Только для админа")
        return
    
    try:
        user_id = int(message.text.replace("/approve_", ""))
        activate_subscription(user_id, days=30)
        
        await message.answer(f"✅ Подписка активирована для пользователя {user_id}")
        
        try:
            await bot.send_message(
                user_id,
                "✅ <b>Оплата подтверждена!</b>\n\n"
                "🎉 Безлимит активирован на 30 дней.\n"
                "Решай сколько хочешь задач!",
                parse_mode="HTML"
            )
        except:
            pass
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# === ОТКЛОНЕНИЕ ОПЛАТЫ АДМИНОМ ===
@dp.message(F.text.startswith("/reject_"))
async def cmd_reject(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Только для админа")
        return
    
    try:
        user_id = int(message.text.replace("/reject_", ""))
        await message.answer(f"❌ Заявка от пользователя {user_id} отклонена")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


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
            "💳 Подключи безлимит: /buy",
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
            "• Поднести камеру ближе\n"
            "• Или напиши задачу текстом"
        )
        return
    
    await message.answer(
        f"📝 <b>Распознано:</b>\n<code>{problem_text[:500]}</code>\n\n🧠 Решаю...",
        parse_mode="HTML"
    )
    
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
            "💳 Подключи безлимит: /buy",
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
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
