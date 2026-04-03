import os
import re
import tempfile
import requests
import pdfplumber
from pathlib import Path
from datetime import datetime

# URL PDF файла
PDF_URL = "https://olymp50.hse.ru/OLYMPREPORTS/MMO/SecondStage/Results/35752712464.pdf"
OUTPUT_FILE = "pdf_parsing_log.txt"


def log_and_print(message, log_file):
    """Выводит сообщение в консоль и записывает в файл"""
    try:
        print(message)
    except UnicodeEncodeError:
        # Для Windows консоли убираем эмодзи
        print(message.encode("ascii", "ignore").decode("ascii"))
    log_file.write(message + "\n")


def download_pdf(url, log_file):
    """Скачивает PDF файл"""
    msg = f"📥 Скачиваю PDF с {url}..."
    log_and_print(msg, log_file)
    try:
        resp = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HSE-bot/1.0)"},
        )
        resp.raise_for_status()
        msg = f"✅ PDF скачан, размер: {len(resp.content)} байт"
        log_and_print(msg, log_file)
        return resp.content
    except Exception as e:
        msg = f"❌ Ошибка скачивания: {e}"
        log_and_print(msg, log_file)
        return None


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


def parse_results_verbose(pdf_bytes, log_file):
    """Парсит PDF с подробным выводом процесса"""
    results = {}

    # Создаем временный файл
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    log_and_print(f"\n📄 Временный файл создан: {tmp_path}", log_file)
    log_and_print("=" * 80, log_file)

    try:
        with pdfplumber.open(tmp_path) as pdf:
            log_and_print(f"📖 Всего страниц в PDF: {len(pdf.pages)}\n", log_file)

            rank_counter = 0

            for page_num, page in enumerate(pdf.pages, 1):
                log_and_print(f"\n{'=' * 80}", log_file)
                log_and_print(f"СТРАНИЦА {page_num}", log_file)
                log_and_print(f"{'=' * 80}", log_file)

                # Пытаемся извлечь таблицы
                tables = page.extract_tables()

                if tables:
                    log_and_print(f"✅ Найдено таблиц: {len(tables)}", log_file)

                    for table_num, table in enumerate(tables, 1):
                        log_and_print(
                            f"\n  📊 Таблица #{table_num}, строк: {len(table)}",
                            log_file,
                        )

                        for row_num, row in enumerate(table, 1):
                            if not row:
                                continue

                            cells = [str(c).strip() if c else "" for c in row]

                            # Пропускаем строки без цифр (заголовки)
                            if not any(c.isdigit() for c in cells):
                                log_and_print(
                                    f"    Строка {row_num}: [ЗАГОЛОВОК] {' | '.join(cells[:3])}...",
                                    log_file,
                                )
                                continue

                            code, score = extract_code_score(cells)

                            if code:
                                rank_counter += 1
                                results[code] = {
                                    "rank": rank_counter,
                                    "score": score,
                                    "row": " | ".join(cells),
                                }
                                log_and_print(
                                    f"    ✅ Строка {row_num}: Код={code}, Балл={score}, Место=#{rank_counter}",
                                    log_file,
                                )
                                log_and_print(
                                    f"       Данные: {' | '.join(cells)}", log_file
                                )
                            else:
                                log_and_print(
                                    f"    ⚠️  Строка {row_num}: Код не найден - {' | '.join(cells[:5])}...",
                                    log_file,
                                )

                else:
                    log_and_print(
                        "⚠️  Таблицы не найдены, пытаюсь извлечь текст...", log_file
                    )

                    text = page.extract_text(layout=True) or ""
                    lines = text.splitlines()
                    log_and_print(f"📝 Извлечено строк текста: {len(lines)}", log_file)

                    for line_num, line in enumerate(lines, 1):
                        line = line.strip()
                        if not line:
                            continue

                        # Разделяем по множественным пробелам или табам
                        parts = re.split(r"\s{2,}|\t", line)
                        if len(parts) < 2:
                            parts = line.split()

                        # Пропускаем строки без цифр
                        if not any(p.isdigit() for p in parts):
                            if line_num <= 10:  # Показываем первые 10 строк
                                log_and_print(
                                    f"    Строка {line_num}: [БЕЗ ЦИФР] {line[:60]}...",
                                    log_file,
                                )
                            continue

                        code, score = extract_code_score(parts)

                        if code:
                            rank_counter += 1
                            results[code] = {
                                "rank": rank_counter,
                                "score": score,
                                "row": line,
                            }
                            log_and_print(
                                f"    ✅ Строка {line_num}: Код={code}, Балл={score}, Место=#{rank_counter}",
                                log_file,
                            )
                            log_and_print(f"       Данные: {line}", log_file)
                        else:
                            if line_num <= 20:  # Показываем первые 20 строк с цифрами
                                log_and_print(
                                    f"    ⚠️  Строка {line_num}: Код не найден - {line[:60]}...",
                                    log_file,
                                )

    finally:
        os.unlink(tmp_path)
        log_and_print(f"\n🗑️  Временный файл удален", log_file)

    return results


def main():
    # Создаем файл для логирования
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"pdf_parsing_log_{timestamp}.txt"

    with open(log_filename, "w", encoding="utf-8") as log_file:
        log_and_print("🤖 Тестирование парсера PDF для телеграм-бота", log_file)
        log_and_print("=" * 80, log_file)
        log_and_print(f"📝 Лог сохраняется в файл: {log_filename}", log_file)
        log_and_print(
            f"🕐 Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            log_file,
        )
        log_and_print("=" * 80, log_file)

        # Скачиваем PDF
        pdf_bytes = download_pdf(PDF_URL, log_file)
        if pdf_bytes is None:
            log_and_print("❌ Не удалось скачать PDF", log_file)
            return

        # Парсим с подробным выводом
        results = parse_results_verbose(pdf_bytes, log_file)

        # Итоговая статистика
        log_and_print("\n" + "=" * 80, log_file)
        log_and_print("📊 ИТОГОВАЯ СТАТИСТИКА", log_file)
        log_and_print("=" * 80, log_file)
        log_and_print(f"Всего найдено записей: {len(results)}", log_file)

        if results:
            log_and_print(f"\n🏆 Первые 10 результатов:", log_file)
            for i, (code, data) in enumerate(list(results.items())[:10], 1):
                log_and_print(
                    f"  {i}. Код: {code:20s} | Место: #{data['rank']:4d} | Балл: {data['score']}",
                    log_file,
                )

            log_and_print(f"\n🔍 Примеры кодов:", log_file)
            codes = list(results.keys())
            for code in codes[:5]:
                log_and_print(f"  - {code}", log_file)
        else:
            log_and_print("⚠️  Записи не найдены!", log_file)

        log_and_print("\n" + "=" * 80, log_file)
        log_and_print(f"✅ Лог сохранен в файл: {log_filename}", log_file)
        log_and_print("=" * 80, log_file)


if __name__ == "__main__":
    main()
