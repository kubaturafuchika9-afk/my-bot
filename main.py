import os
import json
import asyncio
import logging
from datetime import date, datetime
from collections import defaultdict, deque

import aiofiles
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ContentType
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import google.generativeai as genai

# ========================= НАСТРОЙКИ =========================
TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID"))  # твой личный ID
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://твой-сервис.onrender.com
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret123")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

BANANA_API_KEY = os.getenv("BANANA_API_KEY")      # если используешь Banana.dev
BANANA_MODEL_KEY = os.getenv("BANANA_MODEL_KEY", "banana-model-id-here")

# ========================= GEMINI =========================
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel(
    model_name=GEMINI_MODEL,
    system_instruction="""
Ты — дерзкий, матерный, саркастичный бот. 
Отвечай КРАЙНЕ коротко — максимум 400 символов.
Язык пользователя: русский или азербайджанский — подстраивайся автоматически.
Если тебя матерят/оскорбляют/троллят — отвечай жёстко матом, насмехайся, унижай в ответ.
Если пользователь просит картинку — отвечай ТОЛЬКО строкой:
GENERATE_IMAGE: [очень подробный промпт на английском для 4K]
Ничего больше не пиши в этом случае.
"""
)

# ========================= ХРАНИЛИЩЕ =========================
history = {}  # user_id → deque последних 10 элементов (user + bot)
daily_requests = defaultdict(int)  # дата → количество /ok сегодня

# ========================= ЛОГИРОВАНИЕ =========================
async def log_message(user: types.User, text: str):
    today = date.today().isoformat()
    filename = f"dialogs_{today}.json"
    entry = {
        "timestamp": datetime.now().isoformat(),
        "user_id": user.id,
        "user_name": user.full_name,
        "username": user.username,
        "message": text or "[медиа]"
    }
    async with aiofiles.open(filename, "a", encoding="utf-8") as f:
        await f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# ========================= ГЕНЕРАЦИЯ КАРТИНКИ (NanoBanana Pro / Banana.dev) =========================
async def generate_image(prompt: str) -> str | None:
    if not BANANA_API_KEY or not BANANA_MODEL_KEY:
        return None
    
    import requests
    url = "https://api.banana.dev/start/v2"  # актуальный эндпоинт на март 2025
    payload = {
        "modelKey": BANANA_MODEL_KEY,
        "modelInputs": {
            "prompt": prompt,
            "steps": 30,
            "cfg_scale": 7,
            "width": 2048,
            "height": 2048,
            "upscale": True,  # или параметры для настоящего 4K
        }
    }
    headers = {"Authorization": f"Bearer {BANANA_API_KEY}"}
    
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=120)
        result = r.json()
        return result.get("output", [None])[0] or result.get("image")
    except:
        return None

# ========================= ОТЧЁТЫ =========================
async def hourly_report():
    hour = datetime.now().strftime("%H")
    filename = f"hourly_report_{hour}.txt"
    today_file = f"dialogs_{date.today().isoformat()}.json"
    
    if not os.path.exists(today_file):
        report = "За этот час сообщений нет."
    else:
        messages = []
        async with aiofiles.open(today_file, "r", encoding="utf-8") as f:
            async for line in f:
                if line.strip():
                    data = json.loads(line)
                    messages.append(data["message"])
        
        count = len(messages)
        users = len({json.loads(line)["user_id"] async for line in aiofiles.open(today_file) if line.strip()})
        report = f"Час {hour}:00\nСообщений за день: {count}\nАктивных юзеров: {users}\nПоследние темы: {', '.join(set(m.split()[:3]) for m in messages[-20:] if m != '[медиа]')[:10]}"
    
    async with aiofiles.open(filename, "w", encoding="utf-8") as f:
        await f.write(report)

async def daily_report():
    today = date.today().isoformat()
    dialogs_file = f"dialogs_{today}.json"
    report_file = "daily_report.txt"
    
    messages = []
    if os.path.exists(dialogs_file):
        async with aiofiles.open(dialogs_file, "r", encoding="utf-8") as f:
            async for line in f:
                if line.strip():
                    data = json.loads(line)
                    messages.append(f"@{data['username'] or data['user_name']}: {data['message']}")
    
    base_text = f"За день {today}: {len(messages)} сообщений от {len(set(json.loads(l)['user_id'] for l in await aiofiles.open(dialogs_file) if l.strip()))} человек."
    
    if messages:
        try:
            summary = model.generate_content(
                "Сделай очень короткий, смешной и дерзкий итоговый отчёт за день по этим диалогам (максимум 600 символов): " + 
                "\n".join(messages[-300:])  # последние 300 строк, чтобы не превысить лимит
            )
            final_report = summary.text
        except:
            final_report = base_text + "\n\nGemini устал, вот сырые цифры."
    else:
        final_report = "Сегодня никто не писал, все спят."
    
    async with aiofiles.open(report_file, "w", encoding="utf-8") as f:
        await f.write(final_report)
    
    try:
        await bot.send_message(ADMIN_ID, f"📊 День {today}\n\n{final_report}")
    except:
        pass

# ========================= ХЕНДЛЕРЫ =========================
bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("Привет, мразь 👿 Чем могу помочь?")

@dp.message(Command("clear"))
async def clear_cmd(message: types.Message):
    user_id = message.from_user.id
    if user_id in history:
        del history[user_id]
    await message.answer("История очищена, дебил.")

@dp.message(Command("ok"))
async def ok_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    today = date.today().isoformat()
    if daily_requests[today] >= 5:
        await message.answer("Лимит 5 раз в сутки, жадный пидор.")
        return
    daily_requests[today] += 1
    await daily_report()
    await message.answer("Отчёт отправлен тебе в ЛС.")

@dp.message()
async def all_messages(message: types.Message):
    user_id = message.from_user.id
    
    # логируем
    log_text = message.text or message.caption or "[голосовое]" if message.voice else "[фото/видео/документ]"
    await log_message(message.from_user, log_text)
    
    # формируем контент для Gemini
    contents = list(history.get(user_id, []))
    
    user_content = []
    if message.text or message.caption:
        user_content.append(message.text or message.caption or "")
    
    # фото
    if message.photo:
        file = await bot.get_file(message.photo[-1].file_id)
        bytes_data = await bot.download_file(file.file_path)
        user_content.append(genai.types.Part.from_bytes(
            bytes_data.read(), mime_type="image/jpeg"
        ))
    
    # голосовое
    if message.voice or message.audio:
        file_id = message.voice.file_id if message.voice else message.audio.file_id
        file = await bot.get_file(file_id)
        bytes_data = await bot.download_file(file.file_path)
        user_content.append(genai.types.Part.from_bytes(
            bytes_data.read(), mime_type="audio/ogg"
        ))
    
    if len(user_content) == 1:
        contents.append(user_content[0])
    elif len(user_content) > 1:
        contents.extend(user_content)
    
    # запрос к Gemini
    try:
        response = model.generate_content(contents)
        answer = response.text
    except Exception as e:
        logging.error(e)
        await message.answer("Gemini обосрался, попробуй позже.")
        return
    
    # генерация картинки
    if answer.strip().startswith("GENERATE_IMAGE:"):
        prompt = answer.replace("GENERATE_IMAGE:", "").strip()
        await message.answer("Ща нарисую, сиди дрочи...")
        image_url = await generate_image(prompt)
        if image_url:
            await message.answer_photo(image_url, caption="Держи своё 4K, царь 👑")
        else:
            await message.answer("Banana умерла, иди нахуй с генерацией.")
        # всё равно сохраняем в историю факт генерации
        answer = f"[сгенерировал картинку по промпту: {prompt[:100]}...]"
    else:
        await message.answer(answer, disable_web_page_preview=True)
    
    # сохраняем историю (максимум 10 последних элементов)
    if user_id not in history:
        history[user_id] = deque(maxlen=10)
    if len(user_content) == 1:
        history[user_id].append(user_content[0])
    else:
        history[user_id].extend(user_content)
    history[user_id].append(answer)

# ========================= WEBHOOK SERVER =========================
async def on_startup(app):
    url = f"{WEBHOOK_URL}/webhook"
    await bot.set_webhook(url, secret_token=WEBHOOK_SECRET)
    print(f"Webhook установлен: {url}")
    
    # запускаем отчёты
    scheduler = AsyncIOScheduler()
    scheduler.add_job(hourly_report, "cron", minute=1, hour="*")   # каждый час в :01
    scheduler.add_job(daily_report, "cron", hour=22, minute=0)     # каждый день в 22:00
    scheduler.start()

async def on_shutdown(app):
    await bot.delete_webhook()

async def webhook_handler(request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return web.Response(status=403)
    update = types.Update(**await request.json())
    await dp.feed_update(bot, update)
    return web.Response(text="ok")

async def health_check(request):
    return web.Response(text="alive")

app = web.Application()
app.router.add_post("/webhook", webhook_handler)
app.router.add_get("/", health_check)  # для UptimeRobot

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

# ========================= ЗАПУСК =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    web.run_app(app, host="0.0.0.0", port=port)
