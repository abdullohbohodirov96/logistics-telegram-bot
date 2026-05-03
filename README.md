# Logistics Telegram Bot

Telegram-бот для логистики и управления доставками. Интегрирован с Google Sheets для получения заданий и с Supabase (PostgreSQL) для хранения истории статусов и заказов.

## Требования
- Python 3.11+
- Аккаунт Supabase
- Google Cloud Console (Service Account)
- Аккаунт Render (для деплоя)

---

## 🛠 Инструкция по настройке

### 1. Как создать Telegram bot
1. Откройте в Telegram бота [@BotFather](https://t.me/BotFather).
2. Напишите команду `/newbot`.
3. Придумайте имя и юзернейм (должен заканчиваться на `bot`).
4. BotFather выдаст вам **HTTP API Token** (это ваш `BOT_TOKEN`).

### 2. Как получить Telegram ID водителя
1. Водитель должен написать любому боту для получения ID, например [@userinfobot](https://t.me/userinfobot) или [@getmyid_bot](https://t.me/getmyid_bot).
2. Бот пришлет набор цифр — это и есть `telegram_id` водителя.
3. Важно: Водитель должен обязательно написать нашему логистическому боту (нажать кнопку `/start`), иначе бот не сможет отправить ему задание в личку.

### 3. Как создать Google Sheet и структуру
1. Создайте новую таблицу в Google Sheets.
2. В URL таблицы найдите `GOOGLE_SHEET_ID`. Например: `https://docs.google.com/spreadsheets/d/ВАШ_ID/edit` -> `ВАШ_ID`.
3. Создайте лист с названием `drivers` и колонками (строка 1):
   - A: car_number
   - B: driver_name
   - C: telegram_id
4. Создайте лист с названием `orders` и колонками (строка 1):
   - A: order_id
   - B: car_number
   - C: address
   - D: cargo
   - E: comment
   - F: status

### 4. Как подключить Google Sheets API и Service Account
1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/).
2. Создайте новый проект (New Project).
3. Перейдите в **APIs & Services** -> **Library** и включите **Google Sheets API**.
4. Перейдите в **Credentials** -> **Create Credentials** -> **Service Account**.
5. Назовите его, нажмите Done.
6. Нажмите на созданный Service Account, перейдите во вкладку **Keys** -> **Add Key** -> **Create new key** -> выберите **JSON**. Файл скачается на компьютер.
7. Откройте этот JSON-файл и скопируйте его содержимое (оно понадобится для переменной `GOOGLE_SERVICE_ACCOUNT_JSON`).
8. В скачанном файле найдите `client_email`. Скопируйте этот email.
9. Откройте вашу таблицу Google Sheets, нажмите "Настройки доступа" (Share) и **выдайте права Редактора** этому email.

### 5. Как подключить Supabase
1. Зарегистрируйтесь на [Supabase](https://supabase.com/) и создайте новый проект.
2. Перейдите в настройки проекта: **Project Settings** -> **API**.
3. Скопируйте `Project URL` (это `SUPABASE_URL`) и `anon / public key` (это `SUPABASE_KEY`).
4. Перейдите в **SQL Editor** в Supabase и выполните SQL код из файла `supabase.sql` для создания таблиц `orders` и `order_steps`.

### 6. Как получить GROUP_CHAT_ID
1. Добавьте вашего бота в нужную группу в Telegram (где будут публиковаться статусы).
2. Дайте боту права администратора (минимум права на отправку сообщений и медиа).
3. Чтобы узнать ID группы, напишите любое сообщение в группу, перешлите его боту [@JsonDumpBot](https://t.me/JsonDumpBot) или используйте веб-версию Telegram: в URL после `#` будет ID. В API Telegram ID групп обычно начинаются с `-100` (например, `-100123456789`). Это и есть ваш `GROUP_CHAT_ID`.

---

## 🚀 Запуск локально

1. Склонируйте репозиторий.
2. Создайте виртуальное окружение:
   ```bash
   python -m venv venv
   source venv/bin/activate  # для Mac/Linux
   # venv\Scripts\activate  # для Windows
   ```
3. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```
4. Скопируйте `.env.example` в `.env` и заполните все переменные.
   > **Важно:** `GOOGLE_SERVICE_ACCOUNT_JSON` должно быть одной строкой JSON (минифицированный формат) без переносов строк. Или просто используйте стандартный JSON, если ваш сервер/Render это поддерживает (в Render поддерживается).
5. Запустите бота:
   ```bash
   python main.py
   ```

---

## ☁️ Деплой на Render

1. Загрузите код в репозиторий на GitHub.
2. Зарегистрируйтесь на [Render](https://render.com/).
3. Создайте новый сервис (New -> Blueprint или New -> Background Worker).
   - *Вариант через Blueprint:* Просто подключите репозиторий, Render прочитает `render.yaml` и настроит всё сам.
   - *Вариант вручную:* Выберите **Background Worker**, подключите GitHub репозиторий.
     - Build Command: `pip install -r requirements.txt`
     - Start Command: `python main.py`
4. В разделе **Environment Variables** добавьте следующие ключи:
   - `BOT_TOKEN`
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
   - `GOOGLE_SHEET_ID`
   - `GOOGLE_SERVICE_ACCOUNT_JSON` (вставьте содержимое JSON-файла целиком)
   - `GROUP_CHAT_ID`
   - `POLL_INTERVAL_SECONDS` (например, 60)
5. Нажмите **Deploy**. Бот будет запущен 24/7 (если используется платный тариф; на бесплатном тарифе есть лимит часов в месяц).
