# Logistics Telegram Bot

Logistika va yetkazib berishlarni nazorat qilish uchun Telegram-bot. Google Sheets bilan bog'langan (buyurtmalarni o'qish va statuslarni yangilash) va Supabase (PostgreSQL) orqali ma'lumotlarni saqlaydi.

## Asosiy imkoniyatlar
- Yangi buyurtmalarni Google Sheets'dan avtomatik qabul qiladi.
- Haydovchini topib, unga shaxsiy xabar (PM) yuboradi.
- Bosqichma-bosqich jarayon: 
  - Buyurtmani olish
  - Ombor bloklari (A, B, C, D) bo'yicha yukni qabul qilish.
  - Mashinaga yuklangan rasm.
  - Yo'lga chiqish.
  - Manzilga yetib borish va lokatsiya yuborish.
  - Manzildagi holat rasmi.
  - Yetkazib berishni yakunlash.
- Har bir bosqich guruh/kanal dagi bitta xabarda yangilanib boradi.
- Jarayon tugagach kanalga rasmlar va Google Maps lokatsiyasi tushadi.
- Holatlar avtomatik ravishda Sheets'da (SENT, IN_PROGRESS, DONE) va Supabase'da saqlanadi.
- Keng qamrovli tarix (Admin panel va Haydovchi uchun).

## Talablar
- Python 3.11+
- Supabase (bepul akkaunt)
- Google Cloud Console (Service Account JSON)
- Render akkaunti (server uchun)

---

## 🛠 Sozlash bo'yicha qo'llanma

### 1. Telegram bot yaratish
1. Telegramda [@BotFather](https://t.me/BotFather) botiga kiring.
2. `/newbot` buyrug'ini bering.
3. Ism va username tanlang.
4. BotFather bergan **HTTP API Token** ni nusxalang (`BOT_TOKEN`).

### 2. Haydovchi ID sini olish
1. Haydovchi o'z ID sini olish uchun [@getmyid_bot](https://t.me/getmyid_bot) ga yozishi kerak.
2. Olingan `telegram_id` raqamini Google Sheets'dagi `drivers` listiga yozish kerak.
3. Haydovchi logistika botiga kirib `/start` ni bosishi **SHART**, aks holda bot unga xabar yubora olmaydi.

### 3. Google Sheets tuzilishi
1. Google Sheets yarating va uning URL id qismini oling (`GOOGLE_SHEET_ID`).
2. `drivers` nomli list (sheet) yarating:
   - A: car_number
   - B: driver_name
   - C: telegram_id
3. `orders` nomli list yarating:
   - A: order_id
   - B: car_number
   - C: address
   - D: cargo
   - E: comment
   - F: status

### 4. Supabase va Google Service Account
- [Supabase](https://supabase.com/) da proyekt yarating va SQL Editor da `supabase.sql` dagi kodni ishga tushiring.
- Google Cloud da Service Account ochib JSON kalit oling va uni Sheets'ga "Editor" (Tahrirlovchi) huquqi bilan qo'shing.

### 5. Guruhni ulash (GROUP_CHAT_ID)
Agar xabarlar guruhga tushishini istasangiz, botni guruhga qo'shib admin qiling va uning ID sini `-100...` formatida `GROUP_CHAT_ID` ga yozing. Agar guruh kerak bo'lmasa `0` qilib qoldiring.

---

## ☁️ Render.com ga joylash

GitHub dagi ushbu repozitoriyani Render.com dagi **Background Worker** orqali ulab, quyidagi Environment Variables (Muhit o'zgaruvchilari) ni qo'shing:
- `BOT_TOKEN`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GROUP_CHAT_ID` (yoki 0)
- `ADMIN_IDS` (masalan, 1282014621,123456789)
- `POLL_INTERVAL_SECONDS` (masalan, 60)
- `PYTHON_VERSION` (3.11.9 qilib yozing)
