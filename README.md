# HSE Olympiad Results Monitor Bot 🤖

Telegram-бот для отслеживания изменений в таблице результатов олимпиады ВШЭ.

## Что умеет

- Спрашивает у пользователя **код работы** при первом запуске
- Проверяет PDF-таблицу каждые N минут (по умолчанию 15)
- При изменении PDF уведомляет пользователя:
  - если код работы **появился** — сообщает балл и место в рейтинге
  - если данные **обновились** — показывает новые значения и изменение позиции
- Поддерживает нескольких пользователей одновременно (каждый со своим кодом)

## Команды бота

|Команда   |Действие                          |
|----------|----------------------------------|
|`/start`  |Регистрация / приветствие         |
|`/status` |Проверить свой статус прямо сейчас|
|`/setcode`|Изменить код работы               |
|`/stop`   |Отписаться от уведомлений         |
|`/cancel` |Отменить текущий диалог           |

## Установка и запуск

### 1. Создай бота в Telegram

1. Напиши [@BotFather](https://t.me/BotFather) → `/newbot`
1. Получи токен вида `7123456789:AAH...`

### 2. Установи зависимости

```bash
pip install -r requirements.txt
```

### 3. Настрой бота

Открой `bot.py` и замени в начале файла:

```python
BOT_TOKEN = "7123456789:AAH..."   # ← твой токен
PDF_URL   = "https://olymp50.hse.ru/..."  # ← URL таблицы (уже вписан)
CHECK_INTERVAL_MINUTES = 15        # ← частота проверки (мин.)
```

### 4. Запусти

```bash
python bot.py
```

Бот сохраняет состояние в `bot_data.json` в той же папке.

## Запуск на сервере (systemd)

Создай `/etc/systemd/system/hse-bot.service`:

```ini
[Unit]
Description=HSE Olympiad Bot
After=network.target

[Service]
WorkingDirectory=/path/to/hse_olymp_bot
ExecStart=/usr/bin/python3 /path/to/hse_olymp_bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable hse-bot
sudo systemctl start hse-bot
sudo systemctl status hse-bot
```

## Как работает парсер таблицы

PDF скачивается по URL → вычисляется SHA-256 хэш.
Если хэш изменился — таблица обновилась → парсится через `pdfplumber`.

Парсер ищет в каждой строке:

- **Код работы** — строка из букв/цифр/дефисов длиной ≥ 4 символа
- **Балл** — числовое значение (целое или дробное)

Место в рейтинге определяется порядком строк в PDF.

## Структура файлов

```
hse_olymp_bot/
├── bot.py           — основной код бота
├── requirements.txt — зависимости
├── README.md        — документация
└── bot_data.json    — создаётся автоматически при запуске
```
