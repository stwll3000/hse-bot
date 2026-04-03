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
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PDF_URL = "https://olymp50.hse.ru/OLYMPREPORTS/MMO/SecondStage/Results/35752712464.pdf"
CHECK_INTERVAL_MINUTES = 15
DATA_FILE = "bot_data.json"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ASK_CODE = 0


def load_data():
    if Path(DATA_FILE).exists():
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}, "last_hash": None, "last_results": {}}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def download_pdf(url):
    try:
        resp = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HSE-bot/1.0)"},
        )
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.error(f"PDF download error: {e}")
        return None


def pdf_hash(content):
    return hashlib.sha256(content).hexdigest()


def parse_results(pdf_bytes):
    results = {}
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        with pdfplumber.open(tmp_path) as pdf:
            rank_counter = 0
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            if not row:
                                continue
                            cells = [str(c).strip() if c else "" for c in row]
                            if not any(c.isdigit() for c in cells):
                                continue
                            code, score = extract_code_score(cells)
                            if code:
                                rank_counter += 1
                                results[code] = {
                                    "rank": rank_counter,
                                    "score": score,
                                    "row": " | ".join(cells),
                                }
                else:
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
                        code, score = extract_code_score(parts)
                        if code:
                            rank_counter += 1
                            results[code] = {
                                "rank": rank_counter,
                                "score": score,
                                "row": line,
                            }
    finally:
        os.unlink(tmp_path)

    logger.info(f"Parsed {len(results)} records")
    return results


def extract_code_score(cells):
    """
    Извлекает код работы и балл из строки таблицы.
    Формат: № п/п | Позиция в рейтинге | Код работы | Регион | Балл
    Пример: 64 | 63 | 155439 | Новосибирскаяобласть | 43
    """
    code = None
    score = "-"

    # Ищем числа в ячейках
    numbers = []
    for cell in cells:
        cell = str(cell).strip()
        if not cell:
            continue
        # Проверяем, является ли ячейка числом
        if re.match(r"^\d+([.,]\d+)?$", cell):
            numbers.append(cell)

    # Если нашли минимум 3 числа: номер строки, позиция в рейтинге, код работы
    if len(numbers) >= 3:
        # Код работы - это третье число (обычно 6-значное)
        code = numbers[2]
        # Балл - последнее число в строке
        if len(numbers) >= 4:
            score = numbers[-1].replace(",", ".")

    return code, score


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user_id = str(update.effective_user.id)
    name = update.effective_user.first_name or "участник"
    existing_code = data["users"].get(user_id, {}).get("code")

    if existing_code:
        await update.message.reply_text(
            f"Привет, {name}!\n"
            f"Твой код работы: <b>{existing_code}</b>\n\n"
            f"/status - проверить результат\n"
            f"/setcode - сменить код",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"Привет, {name}! Я буду следить за таблицей результатов олимпиады ВШЭ "
        f"и уведомлять тебя об изменениях.\n\n"
        f"Введи свой <b>код работы</b>:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_CODE


async def receive_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    user_id = str(update.effective_user.id)

    if not code or len(code) < 2:
        await update.message.reply_text("Некорректный код. Попробуй ещё раз:")
        return ASK_CODE

    data = load_data()
    data["users"][user_id] = {
        "code": code,
        "chat_id": update.effective_chat.id,
        "registered_at": datetime.now().isoformat(),
    }
    save_data(data)

    await update.message.reply_text(
        f"Код <b>{code}</b> сохранён!\n\n"
        f"Буду проверять таблицу каждые {CHECK_INTERVAL_MINUTES} мин.\n\n"
        f"/status - проверить сейчас\n"
        f"/setcode - изменить код\n"
        f"/stop - отписаться",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )

    await send_user_status(update.effective_chat.id, code, data, ctx.bot)
    return ConversationHandler.END


async def cmd_setcode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введи новый код работы:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_CODE


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user_id = str(update.effective_user.id)
    user = data["users"].get(user_id)

    if not user:
        await update.message.reply_text("Сначала зарегистрируйся: /start")
        return

    await send_user_status(update.effective_chat.id, user["code"], data, ctx.bot)


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user_id = str(update.effective_user.id)
    if user_id in data["users"]:
        del data["users"][user_id]
        save_data(data)
    await update.message.reply_text("Вы отписаны. /start - подписаться снова.")


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def send_user_status(chat_id, code, data, bot):
    results = data.get("last_results", {})
    updated_at = data.get("last_checked", "ещё не проверялось")

    if not results:
        msg = (
            f"Таблица ещё не загружена.\n"
            f"Следующая проверка через {CHECK_INTERVAL_MINUTES} мин."
        )
    elif code in results:
        entry = results[code]
        msg = (
            f"Результат по коду <b>{code}</b>\n\n"
            f"Место в рейтинге: <b>#{entry['rank']}</b> из {len(results)}\n"
            f"Балл: <b>{entry['score']}</b>\n\n"
            f"Данные на: {updated_at}"
        )
    else:
        msg = (
            f"Код <b>{code}</b> пока не найден в таблице.\n"
            f"Всего записей: {len(results)}\n"
            f"Данные на: {updated_at}\n\n"
            f"Уведомлю, как только код появится."
        )

    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")


async def check_pdf_updates(app):
    logger.info("Checking PDF for updates...")
    data = load_data()

    pdf_bytes = download_pdf(PDF_URL)
    if pdf_bytes is None:
        return

    new_hash = pdf_hash(pdf_bytes)
    data["last_checked"] = datetime.now().strftime("%d.%m.%Y %H:%M")

    if new_hash == data.get("last_hash"):
        logger.info("PDF unchanged.")
        save_data(data)
        return

    logger.info("PDF changed! Parsing...")
    new_results = parse_results(pdf_bytes)
    old_results = data.get("last_results", {})

    data["last_hash"] = new_hash
    data["last_results"] = new_results
    save_data(data)

    for user_id, user in data["users"].items():
        code = user["code"]
        chat_id = user["chat_id"]
        try:
            old_entry = old_results.get(code)
            new_entry = new_results.get(code)

            if new_entry and not old_entry:
                msg = (
                    f"Твой результат опубликован!\n\n"
                    f"Код: <b>{code}</b>\n"
                    f"Место: <b>#{new_entry['rank']}</b> из {len(new_results)}\n"
                    f"Балл: <b>{new_entry['score']}</b>\n\n"
                    f"{data['last_checked']}"
                )
            elif new_entry and old_entry:
                rank_diff = old_entry["rank"] - new_entry["rank"]
                if rank_diff > 0:
                    rank_str = f"(+{rank_diff})"
                elif rank_diff < 0:
                    rank_str = f"({rank_diff})"
                else:
                    rank_str = "(без изменений)"

                msg = (
                    f"Таблица обновилась!\n\n"
                    f"Код: <b>{code}</b>\n"
                    f"Место: <b>#{new_entry['rank']}</b> {rank_str}\n"
                    f"Балл: <b>{new_entry['score']}</b>\n\n"
                    f"{data['last_checked']}"
                )
            else:
                msg = (
                    f"Таблица обновилась ({len(new_results)} записей), "
                    f"код <b>{code}</b> ещё не найден.\n"
                    f"{data['last_checked']}"
                )

            await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error notifying user {user_id}: {e}")


async def post_init(app: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_pdf_updates,
        trigger="interval",
        minutes=CHECK_INTERVAL_MINUTES,
        args=[app],
        next_run_time=datetime.now(),
    )
    scheduler.start()
    logger.info(f"Scheduler started. Checking every {CHECK_INTERVAL_MINUTES} min.")


def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN environment variable is not set!")
        return

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("setcode", cmd_setcode),
        ],
        states={
            ASK_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_code)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("stop", cmd_stop))

    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
