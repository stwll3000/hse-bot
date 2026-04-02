“””
HSE Olympiad Results Monitor Bot
Отслеживает изменения в PDF-таблице результатов ВШЭ олимпиады.
“””

import os
import re
import json
import logging
import hashlib
import tempfile
from datetime import datetime
from pathlib import Path

import requests
import pdfplumber
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
Application,
CommandHandler,
MessageHandler,
ConversationHandler,
filters,
ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─────────────────────────────────────────────

# CONFIG — заменить на свои значения

import os
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")────────────────────────────────────────────

       # токен от @BotFather
PDF_URL   = “https://olymp50.hse.ru/OLYMPREPORTS/MMO/SecondStage/Results/35752712464.pdf”
CHECK_INTERVAL_MINUTES = 15           # как часто проверять PDF
DATA_FILE  = “bot_data.json”          # файл для хранения состояния
LOG_LEVEL  = logging.INFO

# ─────────────────────────────────────────────

logging.basicConfig(
format=”%(asctime)s | %(levelname)s | %(name)s — %(message)s”,
level=LOG_LEVEL,
)
logger = logging.getLogger(**name**)

# Состояния ConversationHandler

ASK_CODE = 0

# ══════════════════════════════════════════════

# Персистентное хранилище (JSON)

# ══════════════════════════════════════════════

def load_data() -> dict:
“”“Загрузить данные из JSON-файла.”””
if Path(DATA_FILE).exists():
try:
with open(DATA_FILE, encoding=“utf-8”) as f:
return json.load(f)
except Exception:
pass
return {“users”: {}, “last_hash”: None, “last_results”: {}}

def save_data(data: dict) -> None:
“”“Сохранить данные в JSON-файл.”””
with open(DATA_FILE, “w”, encoding=“utf-8”) as f:
json.dump(data, f, ensure_ascii=False, indent=2)

# ══════════════════════════════════════════════

# Работа с PDF

# ══════════════════════════════════════════════

def download_pdf(url: str) -> bytes | None:
“”“Скачать PDF по URL.”””
try:
resp = requests.get(url, timeout=30, headers={
“User-Agent”: “Mozilla/5.0 (compatible; HSE-bot/1.0)”
})
resp.raise_for_status()
return resp.content
except Exception as e:
logger.error(f”Не удалось скачать PDF: {e}”)
return None

def pdf_hash(content: bytes) -> str:
“”“SHA-256 хэш содержимого PDF.”””
return hashlib.sha256(content).hexdigest()

def parse_results(pdf_bytes: bytes) -> dict[str, dict]:
“””
Распарсить PDF-таблицу результатов.

```
Ожидаемый формат строки (типичный для ВШЭ олимпиады):
  № | Код работы | Балл | ... (доп. колонки)

Возвращает: {код_работы: {"rank": int, "score": str, "row": str}}
"""
results = {}
with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
    tmp.write(pdf_bytes)
    tmp_path = tmp.name

try:
    with pdfplumber.open(tmp_path) as pdf:
        rank_counter = 0  # сквозной счётчик строк с данными

        for page in pdf.pages:
            # Попытка 1: табличная структура
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        if not row:
                            continue
                        # Очищаем ячейки
                        cells = [str(c).strip() if c else "" for c in row]
                        # Пропускаем заголовки (не содержат цифровой код)
                        if not any(c.isdigit() for c in cells):
                            continue
                        # Ищем код работы и балл
                        code, score = _extract_code_score(cells)
                        if code:
                            rank_counter += 1
                            results[code] = {
                                "rank": rank_counter,
                                "score": score,
                                "row": " | ".join(cells),
                            }
            else:
                # Попытка 2: сырой текст (layout)
                text = page.extract_text(layout=True) or ""
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = re.split(r"\s{2,}|\t", line)
                    if len(parts) < 2:
                        parts = line.split()
                    if not any(p.isdigit() for p in parts):
                        continue
                    code, score = _extract_code_score(parts)
                    if code:
                        rank_counter += 1
                        results[code] = {
                            "rank": rank_counter,
                            "score": score,
                            "row": line,
                        }
finally:
    os.unlink(tmp_path)

logger.info(f"Распознано записей: {len(results)}")
return results
```

def _extract_code_score(cells: list[str]) -> tuple[str | None, str]:
“””
Извлечь код работы и балл из строки ячеек.

```
Форматы кода в ВШЭ олимпиадах:
  - Чисто числовой: 12345
  - Буквенно-цифровой: AB-12345, MMO-2024-001 и т.д.

Логика:
  - Код работы — обычно уникальная строка; балл — число (возможно дробное).
  - Ищем столбцы эвристически: первый нечисловой токен с цифрами = код,
    последний числовой токен = балл.
"""
code = None
score = "—"

# Паттерны для кода: минимум 4 символа, содержит цифры
code_pattern = re.compile(r"^[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-_]{3,}$")
# Паттерн балла: целое или дробное число
score_pattern = re.compile(r"^\d+([.,]\d+)?$")

candidates_code = []
candidates_score = []

for cell in cells:
    cell = cell.strip()
    if not cell:
        continue
    if score_pattern.match(cell):
        candidates_score.append(cell)
    elif code_pattern.match(cell) and any(c.isdigit() for c in cell):
        candidates_code.append(cell)

if candidates_code:
    # Выбираем самый длинный кандидат на код
    code = max(candidates_code, key=len)
if candidates_score:
    # Балл — обычно последнее значимое число
    score = candidates_score[-1].replace(",", ".")

return code, score
```

# ══════════════════════════════════════════════

# Telegram handlers

# ══════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
“”“Начало диалога — приветствие и запрос кода работы.”””
data = load_data()
user_id = str(update.effective_user.id)
name = update.effective_user.first_name or “участник”

```
existing_code = data["users"].get(user_id, {}).get("code")
if existing_code:
    await update.message.reply_text(
        f"👋 С возвращением, {name}!\n"
        f"Ваш сохранённый код работы: <b>{existing_code}</b>\n\n"
        "Чтобы сменить код — введите /setcode\n"
        "Проверить статус прямо сейчас — /status",
        parse_mode="HTML",
    )
    return ConversationHandler.END

await update.message.reply_text(
    f"👋 Привет, {name}! Я буду отслеживать результаты олимпиады ВШЭ "
    f"и уведомлять тебя об изменениях.\n\n"
    f"📄 <b>Введи свой код работы</b> (например: <code>AB-12345</code>):",
    parse_mode="HTML",
    reply_markup=ReplyKeyboardRemove(),
)
return ASK_CODE
```

async def receive_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
“”“Принять и сохранить код работы.”””
code = update.message.text.strip()
user_id = str(update.effective_user.id)

```
if not code or len(code) < 2:
    await update.message.reply_text("❌ Некорректный код. Попробуй ещё раз:")
    return ASK_CODE

data = load_data()
data["users"][user_id] = {
    "code": code,
    "chat_id": update.effective_chat.id,
    "registered_at": datetime.now().isoformat(),
}
save_data(data)

await update.message.reply_text(
    f"✅ Код <b>{code}</b> сохранён!\n\n"
    f"Я буду проверять таблицу каждые {CHECK_INTERVAL_MINUTES} мин. "
    f"и сразу сообщу, если появится твой результат или что-то изменится.\n\n"
    f"Команды:\n"
    f"  /status — проверить твой статус прямо сейчас\n"
    f"  /setcode — изменить код работы\n"
    f"  /stop — отписаться от уведомлений",
    parse_mode="HTML",
    reply_markup=ReplyKeyboardRemove(),
)

# Сразу проверяем текущий статус
await _send_user_status(update.effective_chat.id, code, data)
return ConversationHandler.END
```

async def cmd_setcode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
“”“Сменить код работы.”””
await update.message.reply_text(
“🔄 Введи новый код работы:”,
reply_markup=ReplyKeyboardRemove(),
)
return ASK_CODE

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
“”“Показать текущий статус пользователя.”””
data = load_data()
user_id = str(update.effective_user.id)
user = data[“users”].get(user_id)

```
if not user:
    await update.message.reply_text(
        "Сначала зарегистрируйся: /start"
    )
    return

code = user["code"]
await _send_user_status(update.effective_chat.id, code, data)
```

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
“”“Отписаться от уведомлений.”””
data = load_data()
user_id = str(update.effective_user.id)
if user_id in data[“users”]:
del data[“users”][user_id]
save_data(data)
await update.message.reply_text(
“🚫 Вы отписаны от уведомлений. /start — чтобы подписаться снова.”
)

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
await update.message.reply_text(“Отменено.”, reply_markup=ReplyKeyboardRemove())
return ConversationHandler.END

# ══════════════════════════════════════════════

# Вспомогательные функции

# ══════════════════════════════════════════════

async def _send_user_status(
chat_id: int, code: str, data: dict, prefix: str = “”
) -> None:
“”“Отправить пользователю его текущий статус из последних результатов.”””
from telegram import Bot
bot = Bot(token=BOT_TOKEN)

```
results = data.get("last_results", {})
updated_at = data.get("last_checked", "ещё не проверялось")

if not results:
    msg = (
        f"⏳ Таблица результатов ещё не загружена или пуста.\n"
        f"Следующая проверка — через {CHECK_INTERVAL_MINUTES} мин."
    )
elif code in results:
    entry = results[code]
    msg = (
        f"{prefix}"
        f"📊 <b>Результат по коду {code}</b>\n\n"
        f"🏅 Место в рейтинге: <b>#{entry['rank']}</b> из {len(results)}\n"
        f"💯 Балл: <b>{entry['score']}</b>\n\n"
        f"🕒 Данные на: {updated_at}"
    )
else:
    msg = (
        f"{prefix}"
        f"🔍 Код <b>{code}</b> пока не найден в таблице.\n"
        f"Всего записей: {len(results)}\n"
        f"🕒 Данные на: {updated_at}\n\n"
        f"Я уведомлю тебя, как только код появится или таблица обновится."
    )

await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
```

# ══════════════════════════════════════════════

# Планировщик: проверка PDF

# ══════════════════════════════════════════════

async def check_pdf_updates(app) -> None:
“””
Скачать PDF, сравнить хэш с предыдущим.
При изменении — распарсить и уведомить всех подписчиков.
“””
logger.info(“Проверка обновлений PDF…”)
data = load_data()

```
pdf_bytes = download_pdf(PDF_URL)
if pdf_bytes is None:
    logger.warning("PDF недоступен, пропускаем итерацию.")
    return

new_hash = pdf_hash(pdf_bytes)
data["last_checked"] = datetime.now().strftime("%d.%m.%Y %H:%M")

if new_hash == data.get("last_hash"):
    logger.info("PDF не изменился.")
    save_data(data)
    return

logger.info(f"PDF изменился! Старый хэш: {data.get('last_hash')[:8] if data.get('last_hash') else 'нет'}, новый: {new_hash[:8]}")

# Парсим новые результаты
new_results = parse_results(pdf_bytes)
old_results = data.get("last_results", {})

data["last_hash"] = new_hash
data["last_results"] = new_results
save_data(data)

# Уведомляем подписчиков
bot = app.bot
for user_id, user in data["users"].items():
    code = user["code"]
    chat_id = user["chat_id"]

    try:
        # Определяем, что изменилось для данного пользователя
        old_entry = old_results.get(code)
        new_entry = new_results.get(code)

        if new_entry and not old_entry:
            # Код появился впервые!
            msg = (
                f"🎉 <b>Твой результат опубликован!</b>\n\n"
                f"Код работы: <b>{code}</b>\n"
                f"🏅 Место: <b>#{new_entry['rank']}</b> из {len(new_results)}\n"
                f"💯 Балл: <b>{new_entry['score']}</b>\n\n"
                f"🕒 {data['last_checked']}"
            )
        elif new_entry and old_entry:
            # Смотрим, изменились ли данные
            if new_entry["score"] != old_entry["score"] or new_entry["rank"] != old_entry["rank"]:
                rank_diff = old_entry["rank"] - new_entry["rank"]
                rank_str = (
                    f"(↑ +{rank_diff})" if rank_diff > 0
                    else f"(↓ {rank_diff})" if rank_diff < 0
                    else "(без изменений)"
                )
                msg = (
                    f"🔔 <b>Таблица результатов обновилась!</b>\n\n"
                    f"Код работы: <b>{code}</b>\n"
                    f"🏅 Место: <b>#{new_entry['rank']}</b> {rank_str}\n"
                    f"💯 Балл: <b>{new_entry['score']}</b>\n\n"
                    f"🕒 {data['last_checked']}"
                )
            else:
                # Таблица обновилась, но данные этого участника не изменились
                msg = (
                    f"🔔 Таблица обновилась, твои данные без изменений.\n"
                    f"Код: <b>{code}</b> | Место: #{new_entry['rank']} | Балл: {new_entry['score']}\n"
                    f"🕒 {data['last_checked']}"
                )
        else:
            # Код всё ещё не найден в обновлённой таблице
            msg = (
                f"🔔 Таблица обновилась ({len(new_results)} записей), "
                f"но код <b>{code}</b> ещё не найден.\n"
                f"🕒 {data['last_checked']}"
            )

        await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
```

# ══════════════════════════════════════════════

# Точка входа

# ══════════════════════════════════════════════

def main() -> None:
“”“Запуск бота.”””
if BOT_TOKEN == “YOUR_BOT_TOKEN”:
print(“❌ Укажи BOT_TOKEN в начале файла bot.py!”)
return

```
app = Application.builder().token(BOT_TOKEN).build()

# ConversationHandler для регистрации кода
conv = ConversationHandler(
    entry_points=[
        CommandHandler("start", cmd_start),
        CommandHandler("setcode", cmd_setcode),
    ],
    states={
        ASK_CODE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_code)
        ],
    },
    fallbacks=[CommandHandler("cancel", cmd_cancel)],
)

app.add_handler(conv)
app.add_handler(CommandHandler("status", cmd_status))
app.add_handler(CommandHandler("stop", cmd_stop))

# Планировщик проверки PDF
scheduler = AsyncIOScheduler()
scheduler.add_job(
    check_pdf_updates,
    trigger="interval",
    minutes=CHECK_INTERVAL_MINUTES,
    args=[app],
    next_run_time=datetime.now(),  # первая проверка сразу при старте
)
scheduler.start()

logger.info(f"Бот запущен. Проверка каждые {CHECK_INTERVAL_MINUTES} мин.")
app.run_polling(allowed_updates=Update.ALL_TYPES)
```

if **name** == “**main**”:
main()
