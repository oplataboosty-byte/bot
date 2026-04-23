import asyncio
import logging
import random
import string
import sqlite3
import functools
import re
from datetime import datetime, timedelta
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN   = "8601640788:AAFmh2jGX3VrP_jVuiKnfjXE7BH6wZNetgQ"
OWNER_ID    = 8533402137
SUPPORT_BOT = "https://t.me/YrenerSupbot"
PAYMENT_URL = "https://funpay.com/lots/offer?id=67242489"
DB_PATH     = "yrener.db"

APK_FILE_ID: str | None = None

# ── МЕСЯЦЫ ────────────────────────────────────────────────────────────────────
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

def parse_human_date(text: str):
    """
    Парсит даты вида:
      9 апреля 23:59
      15 мая 12:00
      9 апреля (время = 23:59 по умолчанию)
    Возвращает datetime или None.
    """
    text = text.strip().lower()
    # с временем: "9 апреля 23:59"
    m = re.match(r"(\d{1,2})\s+([а-яё]+)\s+(\d{1,2})[:\.](\d{2})", text)
    if m:
        day, month_str, hour, minute = int(m[1]), m[2], int(m[3]), int(m[4])
        month = MONTHS.get(month_str)
        if not month:
            return None
        year = datetime.now().year
        try:
            dt = datetime(year, month, day, hour, minute)
            # если дата уже прошла — следующий год
            if dt < datetime.now():
                dt = datetime(year + 1, month, day, hour, minute)
            return dt
        except ValueError:
            return None
    # без времени: "9 апреля"
    m = re.match(r"(\d{1,2})\s+([а-яё]+)", text)
    if m:
        day, month_str = int(m[1]), m[2]
        month = MONTHS.get(month_str)
        if not month:
            return None
        year = datetime.now().year
        try:
            dt = datetime(year, month, day, 23, 59)
            if dt < datetime.now():
                dt = datetime(year + 1, month, day, 23, 59)
            return dt
        except ValueError:
            return None
    return None

# ── DATABASE ──────────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS keys (
                key      TEXT PRIMARY KEY,
                expires  TEXT NOT NULL,
                days     INTEGER NOT NULL,
                label    TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS users (
                user_id  INTEGER PRIMARY KEY,
                expires  TEXT NOT NULL,
                key      TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS settings (
                name  TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT NOT NULL,
                event      TEXT NOT NULL,
                user_id    INTEGER,
                username   TEXT,
                details    TEXT
            );
            CREATE TABLE IF NOT EXISTS apk_info (
                id        INTEGER PRIMARY KEY CHECK (id = 1),
                file_id   TEXT NOT NULL,
                filename  TEXT NOT NULL,
                size_mb   REAL NOT NULL,
                uploaded  TEXT NOT NULL
            );
        """)
        # миграция: добавить username в keys если нет
        cols = [r[1] for r in conn.execute("PRAGMA table_info(keys)").fetchall()]
        if "username" not in cols:
            conn.execute("ALTER TABLE keys ADD COLUMN username TEXT NOT NULL DEFAULT ''")
        cols2 = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "username" not in cols2:
            conn.execute("ALTER TABLE users ADD COLUMN username TEXT NOT NULL DEFAULT ''")

# ── LOGS ──────────────────────────────────────────────────────────────────────
def db_log(event: str, user_id: int = None, username: str = None, details: str = None):
    ts = datetime.now().isoformat(sep=" ", timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO logs(ts,event,user_id,username,details) VALUES(?,?,?,?,?)",
            (ts, event, user_id, username, details)
        )

def db_logs_get(limit=50):
    with get_conn() as conn:
        if limit:
            rows = conn.execute(
                "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM logs ORDER BY id DESC"
            ).fetchall()
    return [dict(r) for r in rows]

def db_logs_all_json():
    """Все логи для HTML-страницы"""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM logs ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

# ── KEYS ──────────────────────────────────────────────────────────────────────
def db_key_get(key):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM keys WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    return {"key": row["key"], "expires": datetime.fromisoformat(row["expires"]),
            "days": row["days"], "label": row["label"], "username": row["username"]}

def db_key_exists(key):
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM keys WHERE key=?", (key,)).fetchone() is not None

def db_key_add(key, expires, days, label, username):
    with get_conn() as conn:
        conn.execute("INSERT INTO keys(key,expires,days,label,username) VALUES(?,?,?,?,?)",
                     (key, expires.isoformat(), days, label, username))

def db_key_delete(key):
    with get_conn() as conn:
        conn.execute("DELETE FROM keys WHERE key=?", (key,))

def db_key_update_expires(key, new_expires):
    new_days = (new_expires - datetime.now()).days + 1
    with get_conn() as conn:
        conn.execute("UPDATE keys SET expires=?, days=? WHERE key=?",
                     (new_expires.isoformat(), new_days, key))

def db_keys_all():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM keys ORDER BY expires").fetchall()
    return [{"key": r["key"], "expires": datetime.fromisoformat(r["expires"]),
             "days": r["days"], "label": r["label"], "username": r["username"]} for r in rows]

def db_keys_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0]

# ── USERS ─────────────────────────────────────────────────────────────────────
def db_user_get(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    return {"user_id": row["user_id"], "expires": datetime.fromisoformat(row["expires"]),
            "key": row["key"], "username": row["username"]}

def db_user_set(user_id, expires, key, username):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO users(user_id,expires,key,username) VALUES(?,?,?,?)",
                     (user_id, expires.isoformat(), key, username))

def db_users_all_ids():
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
    return [r["user_id"] for r in rows]

def db_users_active_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users WHERE expires > ?",
                            (datetime.now().isoformat(),)).fetchone()[0]

def db_users_expired():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users WHERE expires <= ?",
                            (datetime.now().isoformat(),)).fetchall()
    return [{"user_id": r["user_id"], "expires": datetime.fromisoformat(r["expires"]),
             "key": r["key"], "username": r["username"]} for r in rows]

def db_user_find_by_key(key):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    return {"user_id": row["user_id"], "expires": datetime.fromisoformat(row["expires"]),
            "key": row["key"], "username": row["username"]}

# ── SETTINGS ──────────────────────────────────────────────────────────────────
def db_setting_get(name):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
    return row["value"] if row else None

def db_setting_set(name, value):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO settings(name,value) VALUES(?,?)", (name, value))

# ── APK INFO ──────────────────────────────────────────────────────────────────
def db_apk_save(file_id, filename, size_mb):
    uploaded = datetime.now().isoformat(sep=" ", timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO apk_info(id,file_id,filename,size_mb,uploaded) VALUES(1,?,?,?,?)",
            (file_id, filename, size_mb, uploaded)
        )

def db_apk_get():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM apk_info WHERE id=1").fetchone()
    if not row:
        return None
    return {"file_id": row["file_id"], "filename": row["filename"],
            "size_mb": row["size_mb"], "uploaded": row["uploaded"]}

def db_apk_delete():
    with get_conn() as conn:
        conn.execute("DELETE FROM apk_info WHERE id=1")

# ── SPAM ──────────────────────────────────────────────────────────────────────
spam_tracker = defaultdict(
    lambda: {"count": 0, "blocked_until": None, "strikes": 0, "last_msg": None}
)

def check_spam(user_id):
    now     = datetime.now()
    tracker = spam_tracker[user_id]
    if tracker["blocked_until"] and now < tracker["blocked_until"]:
        return int((tracker["blocked_until"] - now).total_seconds())
    last = tracker["last_msg"]
    if last and (now - last).total_seconds() > 3:
        tracker["count"] = 0
    tracker["count"]   += 1
    tracker["last_msg"] = now
    if tracker["count"] >= 5:
        tracker["strikes"] += 1
        tracker["count"]    = 0
        block_secs = 30 if tracker["strikes"] >= 2 else 15
        tracker["blocked_until"] = now + timedelta(seconds=block_secs)
        return block_secs
    return None

def spam_guard(func):
    @functools.wraps(func)
    async def wrapper(message: Message, **kwargs):
        block = check_spam(message.from_user.id)
        if block:
            await message.answer(f"🚫 ВЫ ЗАБЛОКИРОВАНЫ НА {block} СЕК. ЗА СПАМ")
            return
        return await func(message, **kwargs)
    return wrapper

def make_unique_key():
    while True:
        key = "Yrener" + ''.join(random.choices(string.digits, k=4))
        if not db_key_exists(key):
            return key

# ── KEYBOARDS ─────────────────────────────────────────────────────────────────
def main_keyboard(user_id):
    buttons = [
        [KeyboardButton(text="🔑 Ввести ключ"), KeyboardButton(text="💳 Купить ключ")],
        [KeyboardButton(text="🎲 Кубик"),        KeyboardButton(text="🆘 Поддержка")],
    ]
    # Показываем кнопку "Получить файл" если подписка активна
    info = db_user_get(user_id)
    if info and datetime.now() < info["expires"]:
        buttons.insert(1, [KeyboardButton(text="📥 Получить файл")])
    if user_id == OWNER_ID:
        buttons.append([KeyboardButton(text="⚙️ Создать ключ"), KeyboardButton(text="📋 Панель админа")])
        buttons.append([KeyboardButton(text="📣 Объявление"),   KeyboardButton(text="📤 Загрузить APK")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def support_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Я согласен",  callback_data="support_agree"),
        InlineKeyboardButton(text="❌ Не согласен", callback_data="support_disagree"),
    ]])

def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 Все ключи",       callback_data="admin_list")],
        [InlineKeyboardButton(text="🔍 Поиск по ключу", callback_data="admin_search")],
        [InlineKeyboardButton(text="🗑 Удалить ключ",    callback_data="admin_delete")],
        [InlineKeyboardButton(text="➕ Продлить ключ",   callback_data="admin_extend")],
        [InlineKeyboardButton(text="➖ Сократить ключ",  callback_data="admin_shorten")],
        [InlineKeyboardButton(text="📜 Логи",            callback_data="admin_logs")],
        [InlineKeyboardButton(text="📦 Управление APK",  callback_data="admin_apk")],
    ])

def apk_delete_confirm_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data="apk_delete_confirm"),
        InlineKeyboardButton(text="❌ Отмена",       callback_data="apk_delete_cancel"),
    ]])

# ── BOT + DISPATCHER ──────────────────────────────────────────────────────────
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# ── FSM ───────────────────────────────────────────────────────────────────────
class CreateKey(StatesGroup):
    waiting_for_username = State()
    waiting_for_label    = State()
    waiting_for_date     = State()

class EnterKey(StatesGroup):
    waiting_for_key = State()

class AdminSearch(StatesGroup):
    waiting_for_key = State()

class AdminDelete(StatesGroup):
    waiting_for_key = State()

class AdminExtend(StatesGroup):
    waiting_for_key  = State()
    waiting_for_days = State()

class AdminShorten(StatesGroup):
    waiting_for_key  = State()
    waiting_for_days = State()

class UploadApk(StatesGroup):
    waiting_for_file = State()

class Broadcast(StatesGroup):
    waiting_for_text = State()

# ── /exit, /buy, /enter команды ──────────────────────────────────────────────
@dp.message(Command("exit"))
async def cmd_exit(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🏠 Вы вернулись в главное меню.",
        reply_markup=main_keyboard(message.from_user.id)
    )

@dp.message(Command("buy"))
@spam_guard
async def cmd_buy(message: Message):
    kb_inline = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💰 Перейти к оплате", url=PAYMENT_URL)
    ]])
    await message.answer(
        "💳 Для покупки ключа перейдите по ссылке ниже.\n"
        "После оплаты получите ключ вида: <code>Yrener1234</code>",
        reply_markup=kb_inline, parse_mode="HTML"
    )

@dp.message(Command("enter"))
@spam_guard
async def cmd_enter(message: Message, state: FSMContext):
    kb_reply = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Вернуться домой")]],
        resize_keyboard=True
    )
    await message.answer(
        "🔑 Введите ваш ключ (пример: <code>Yrener1234</code>):",
        reply_markup=kb_reply, parse_mode="HTML"
    )
    await state.set_state(EnterKey.waiting_for_key)

# ── /start ────────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
@spam_guard
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    name     = message.from_user.first_name or "пользователь"
    username = message.from_user.username or ""
    user_id  = message.from_user.id
    db_log("start", user_id, message.from_user.username)
    try:
        await message.react([{"type": "emoji", "emoji": "🥰"}])
    except Exception:
        pass

    uname_display = f"@{username}" if username else f"(id: {user_id})"

    welcome_text = (
        f"👋 Здравствуйте, <b>{name}</b> | {uname_display}\n\n"
        f"🏪 <b>Добро Пожаловать на магазин Yrener</b>\n\n"
        f"Вы тут можете купить <b>Ключ</b>, <b>Файл</b>.\n\n"
        f"💳 Данный момент оплата будет происходить по <b>FunPay</b>. Когда вы оплатите — наш админ выдаст вам ключ, и вы должны будете ввести его сюда.\n\n"
        f"⏳ Ключ будет действовать <b>3 дня</b>. Через 3 дня ключ будет отключён и не будет действовать.\n\n"
        f"📥 Когда вы введёте ключ правильно — для вас откроется раздел <b>«Получить APK»</b> и бот отправит вам файл.\n\n"
        f"‼️ <b>Просим быть внимательнее — не передавайте ключ другим!</b>\n\n"
        f"🆘 Если понадобится помощь — всегда можете написать через раздел <b>Поддержка</b>.\n\n"
        f"🕐 Бот работает с <b>9:00 до 20:00</b> (по МСК)."
    )

    kb_start = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Начать")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    try:
        import os
        photo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "486644.png")
        photo = FSInputFile(photo_path)
        await message.answer_photo(photo=photo, caption=welcome_text, parse_mode="HTML")
    except Exception:
        await message.answer(welcome_text, parse_mode="HTML")

    await message.answer("👇 Нажмите кнопку ниже чтобы начать:", reply_markup=kb_start)

# ── КНОПКА "НАЧАТЬ" → главное меню ───────────────────────────────────────────
@dp.message(F.text == "✅ Начать")
async def go_main_menu(message: Message):
    user_id = message.from_user.id
    info    = db_user_get(user_id)
    if info and datetime.now() < info["expires"]:
        days_left = (info["expires"] - datetime.now()).days
        sub_text  = f"✅ Подписка активна. Осталось дней: <b>{days_left}</b>"
    else:
        sub_text = "❌ Нет активной подписки. Купите ключ ниже."
    await message.answer(
        f"🏠 <b>Главное меню</b>\n\n{sub_text}",
        reply_markup=main_keyboard(user_id), parse_mode="HTML"
    )

# ── ВЕРНУТЬСЯ ДОМОЙ ──────────────────────────────────────────────────────────
@dp.message(F.text == "🏠 Вернуться домой")
async def go_home(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🏠 <b>Главное меню</b>",
        reply_markup=main_keyboard(message.from_user.id), parse_mode="HTML"
    )

# ── КУПИТЬ КЛЮЧ ───────────────────────────────────────────────────────────────
@dp.message(F.text == "💳 Купить ключ")
@spam_guard
async def buy_key(message: Message):
    kb_inline = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💰 Перейти к оплате", url=PAYMENT_URL)
    ]])
    kb_reply = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Вернуться домой")]],
        resize_keyboard=True
    )
    await message.answer(
        "💳 Для покупки ключа перейдите по ссылке ниже.\n"
        "После оплаты вы получите ключ вида: <code>Yrener1234</code>\n"
        "Затем нажмите <b>🔑 Ввести ключ</b> и введите его.",
        reply_markup=kb_inline, parse_mode="HTML"
    )
    await message.answer("⬇️ Или вернитесь в главное меню:", reply_markup=kb_reply)

# ── ВВЕСТИ КЛЮЧ ───────────────────────────────────────────────────────────────
@dp.message(F.text == "🔑 Ввести ключ")
@spam_guard
async def enter_key_prompt(message: Message, state: FSMContext):
    kb_reply = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Вернуться домой")]],
        resize_keyboard=True
    )
    await message.answer(
        "🔑 Введите ваш ключ (пример: <code>Yrener1234</code>):\n\n"
        "Или нажмите кнопку ниже чтобы вернуться в меню.",
        reply_markup=kb_reply, parse_mode="HTML"
    )
    await state.set_state(EnterKey.waiting_for_key)

@dp.message(EnterKey.waiting_for_key)
async def process_key(message: Message, state: FSMContext):
    block = check_spam(message.from_user.id)
    if block:
        await message.answer(f"🚫 ВЫ ЗАБЛОКИРОВАНЫ НА {block} СЕК. ЗА СПАМ")
        await state.clear()
        return

    key      = message.text.strip()
    user_id  = message.from_user.id
    username = (message.from_user.username or "").lower().lstrip("@")
    name     = message.from_user.first_name or "пользователь"

    key_data = db_key_get(key)
    if not key_data:
        db_log("key_invalid", user_id, message.from_user.username, f"key={key}")
        await message.answer("❌ Ключ не найден или уже использован.")
        await state.clear()
        return

    # Проверка username
    key_username = key_data["username"].lower().lstrip("@")
    if key_username and key_username != username:
        db_log("key_wrong_user", user_id, message.from_user.username,
               f"key={key} expected=@{key_username}")
        await message.answer("❌ Ключ не найден или уже истёк.")
        await state.clear()
        return

    expires = key_data["expires"]
    days    = key_data["days"]
    if datetime.now() > expires:
        db_log("key_expired", user_id, message.from_user.username, f"key={key}")
        await message.answer("⏰ Этот ключ уже истёк.")
        await state.clear()
        return

    db_user_set(user_id, expires, key, username)
    db_key_delete(key)
    db_log("key_activated", user_id, message.from_user.username,
           f"key={key} days={days} expires={expires.strftime('%d.%m.%Y %H:%M')}")

    await message.answer(
        f"✅ Здравствуйте, <b>{name}</b>!\n"
        f"Ваш ключ активирован на <b>{days} дней</b>.\n"
        f"Подписка действует до: <code>{expires.strftime('%d.%m.%Y %H:%M')}</code>",
        reply_markup=main_keyboard(user_id), parse_mode="HTML"
    )

    apk = db_apk_get()
    if apk:
        await message.answer("🎮 Нажмите кнопку <b>📥 Получить файл</b> в меню ниже, чтобы скачать приложение.",
                             parse_mode="HTML")
    await state.clear()

# ── ПОЛУЧИТЬ ФАЙЛ (из главного меню) ─────────────────────────────────────────
@dp.message(F.text == "📥 Получить файл")
@spam_guard
async def get_file_main(message: Message):
    user_id = message.from_user.id
    info = db_user_get(user_id)
    if not info or datetime.now() >= info["expires"]:
        await message.answer("❌ Ваша подписка неактивна. Купите ключ.",
                             reply_markup=main_keyboard(user_id))
        return
    apk = db_apk_get()
    if not apk:
        await message.answer("⚠️ APK ещё не загружен. Свяжитесь с поддержкой.")
        return
    await message.answer("📦 Отправляю файл…")
    try:
        await bot.send_document(user_id, apk["file_id"], caption="🎮 Yrener APK")
        db_log("apk_sent", user_id, message.from_user.username)
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке: {e}")

# ── СКАЧАТЬ APK (пользователь) ────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("download_apk:"))
async def send_apk(call: CallbackQuery):
    requester_id = call.from_user.id
    info = db_user_get(requester_id)
    if not info or datetime.now() >= info["expires"]:
        await call.answer("❌ Ваша подписка неактивна.", show_alert=True)
        return
    apk = db_apk_get()
    if not apk:
        await call.answer("⚠️ APK ещё не загружен. Свяжитесь с поддержкой.", show_alert=True)
        return
    await call.answer("📦 Отправляю файл…")
    try:
        await bot.send_document(requester_id, apk["file_id"], caption="🎮 Yrener Casino APK")
        db_log("apk_sent", requester_id, call.from_user.username)
    except Exception as e:
        await bot.send_message(requester_id, f"❌ Ошибка при отправке: {e}")

# ── СОЗДАТЬ КЛЮЧ (админ) ──────────────────────────────────────────────────────
ADMIN_BACK_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🏠 Вернуться домой")]],
    resize_keyboard=True
)

@dp.message(F.text == "⚙️ Создать ключ")
async def create_key_prompt(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Нет доступа.")
        return
    await message.answer(
        "👤 Введите <b>username покупателя</b> (с @ или без):\n"
        "Пример: <code>@username</code>\n\n"
        "<i>Ключ будет привязан к этому username.</i>\n"
        "Или нажмите кнопку ниже чтобы отменить.",
        reply_markup=ADMIN_BACK_KB, parse_mode="HTML"
    )
    await state.set_state(CreateKey.waiting_for_username)

@dp.message(CreateKey.waiting_for_username)
async def create_key_got_username(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    if message.text.strip() == "🏠 Вернуться домой":
        await state.clear()
        await message.answer("🏠 Главное меню.", reply_markup=main_keyboard(message.from_user.id))
        return
    username = message.text.strip().lstrip("@").lower()
    await state.update_data(username=username)
    await message.answer(
        "👤 Введите <b>метку</b> (имя/заметка для себя):\n"
        "Пример: <code>Иван, постоянный клиент</code>\n"
        "Или просто продублируйте username.",
        reply_markup=ADMIN_BACK_KB, parse_mode="HTML"
    )
    await state.set_state(CreateKey.waiting_for_label)

@dp.message(CreateKey.waiting_for_label)
async def create_key_got_label(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    if message.text.strip() == "🏠 Вернуться домой":
        await state.clear()
        await message.answer("🏠 Главное меню.", reply_markup=main_keyboard(message.from_user.id))
        return
    await state.update_data(label=message.text.strip())
    await message.answer(
        "📅 Введите дату окончания ключа:\n\n"
        "Форматы:\n"
        "• <code>9 апреля 23:59</code>\n"
        "• <code>15 мая 12:00</code>\n"
        "• <code>9 апреля</code> (время = 23:59)\n\n"
        "<i>Год подставляется автоматически.</i>",
        reply_markup=ADMIN_BACK_KB, parse_mode="HTML"
    )
    await state.set_state(CreateKey.waiting_for_date)

@dp.message(CreateKey.waiting_for_date)
async def process_create_key(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    if message.text.strip() == "🏠 Вернуться домой":
        await state.clear()
        await message.answer("🏠 Главное меню.", reply_markup=main_keyboard(message.from_user.id))
        return
    expires = parse_human_date(message.text.strip())
    if not expires:
        await message.answer(
            "❌ Не удалось распознать дату.\n"
            "Попробуйте: <code>9 апреля 23:59</code>",
            parse_mode="HTML"
        )
        return
    if expires <= datetime.now():
        await message.answer("❌ Дата уже прошла. Введите будущую дату.")
        return
    data     = await state.get_data()
    label    = data.get("label", "—")
    username = data.get("username", "")
    days     = (expires - datetime.now()).days + 1
    key      = make_unique_key()
    db_key_add(key, expires, days, label, username)
    db_log("key_created", message.from_user.id, message.from_user.username,
           f"key={key} for=@{username} days={days}")
    await message.answer(
        f"✅ Ключ создан!\n\n"
        f"🔑 Ключ: <code>{key}</code>\n"
        f"👤 Для: <b>@{username}</b>\n"
        f"📝 Метка: <b>{label}</b>\n"
        f"📅 До: <code>{expires.strftime('%d.%m.%Y %H:%M')}</code>\n"
        f"⏳ Дней: <b>{days}</b>",
        reply_markup=main_keyboard(message.from_user.id), parse_mode="HTML"
    )
    await state.clear()

# ── ПАНЕЛЬ АДМИНА ─────────────────────────────────────────────────────────────
@dp.message(F.text == "📋 Панель админа")
async def admin_panel(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Нет доступа.")
        return
    await message.answer(
        f"⚙️ <b>Панель администратора</b>\n\n"
        f"🔑 Неиспользованных ключей: <b>{db_keys_count()}</b>\n"
        f"👥 Активных пользователей: <b>{db_users_active_count()}</b>",
        reply_markup=admin_panel_keyboard(), parse_mode="HTML"
    )

# все ключи
@dp.callback_query(F.data == "admin_list")
async def admin_list_keys(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("⛔", show_alert=True)
        return
    await call.answer()

    # Неиспользованные ключи
    keys = db_keys_all()
    if keys:
        lines = ["<b>🗝 Неиспользованные ключи:</b>"]
        for v in keys:
            exp   = v["expires"].strftime("%d.%m.%Y %H:%M")
            label = v.get("label") or "—"
            uname = f"@{v['username']}" if v.get("username") else "—"
            lines.append(f"🔑 <code>{v['key']}</code>\n👤 Владелец: {uname} | {label}\n📅 До: {exp}\n")
        for i in range(0, len(lines), 20):
            await call.message.answer("\n".join(lines[i:i+20]), parse_mode="HTML")
    else:
        await call.message.answer("📭 Нет неиспользованных ключей.")

    # Активные пользователи с ключами
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE expires > ? ORDER BY expires DESC",
            (datetime.now().isoformat(),)
        ).fetchall()
    if rows:
        active_lines = ["\n<b>✅ Активные пользователи:</b>"]
        for r in rows:
            exp = datetime.fromisoformat(r["expires"]).strftime("%d.%m.%Y %H:%M")
            uname = f"@{r['username']}" if r["username"] else f"id:{r['user_id']}"
            key_val = r["key"] or "—"
            active_lines.append(f"👤 {uname}\n🔑 Ключ: <code>{key_val}</code>\n📅 До: {exp}\n")
        for i in range(0, len(active_lines), 20):
            await call.message.answer("\n".join(active_lines[i:i+20]), parse_mode="HTML")

# поиск
@dp.callback_query(F.data == "admin_search")
async def admin_search_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer("⛔", show_alert=True)
        return
    await call.message.answer("🔍 Введите ключ для поиска:")
    await state.set_state(AdminSearch.waiting_for_key)
    await call.answer()

@dp.message(AdminSearch.waiting_for_key)
async def admin_search_key(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    key = message.text.strip()
    kd  = db_key_get(key)
    if kd:
        exp = kd["expires"].strftime("%d.%m.%Y %H:%M")
        await message.answer(
            f"✅ Ключ найден (не активирован)\n\n"
            f"🔑 <code>{key}</code>\n"
            f"👤 Для: <b>@{kd['username'] or '—'}</b>\n"
            f"📝 Метка: <b>{kd['label'] or '—'}</b>\n"
            f"📅 До: <code>{exp}</code>\n"
            f"⏳ Дней: <b>{kd['days']}</b>",
            parse_mode="HTML"
        )
        await state.clear()
        return
    ud = db_user_find_by_key(key)
    if ud:
        exp    = ud["expires"].strftime("%d.%m.%Y %H:%M")
        status = "✅ Активен" if datetime.now() < ud["expires"] else "❌ Истёк"
        await message.answer(
            f"✅ Ключ найден (активирован)\n\n"
            f"🔑 <code>{key}</code>\n"
            f"👤 User ID: <code>{ud['user_id']}</code>\n"
            f"👤 Username: <b>@{ud.get('username') or '—'}</b>\n"
            f"📅 До: <code>{exp}</code>\n"
            f"📊 Статус: {status}",
            parse_mode="HTML"
        )
        await state.clear()
        return
    await message.answer(f"❌ Ключ <code>{key}</code> не найден.", parse_mode="HTML")
    await state.clear()

# удалить ключ
@dp.callback_query(F.data == "admin_delete")
async def admin_delete_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer("⛔", show_alert=True)
        return
    await call.message.answer("🗑 Введите ключ для удаления:")
    await state.set_state(AdminDelete.waiting_for_key)
    await call.answer()

@dp.message(AdminDelete.waiting_for_key)
async def admin_delete_key(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    key = message.text.strip()
    if db_key_exists(key):
        db_key_delete(key)
        db_log("key_deleted", message.from_user.id, message.from_user.username, f"key={key}")
        await message.answer(f"✅ Ключ <code>{key}</code> удалён.", parse_mode="HTML")
    else:
        await message.answer(f"❌ Ключ <code>{key}</code> не найден.", parse_mode="HTML")
    await state.clear()

# продлить ключ
@dp.callback_query(F.data == "admin_extend")
async def admin_extend_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer("⛔", show_alert=True)
        return
    await call.message.answer("➕ Введите ключ для продления:")
    await state.set_state(AdminExtend.waiting_for_key)
    await call.answer()

@dp.message(AdminExtend.waiting_for_key)
async def admin_extend_got_key(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    key = message.text.strip()
    if not db_key_exists(key):
        await message.answer(f"❌ Ключ <code>{key}</code> не найден.", parse_mode="HTML")
        await state.clear()
        return
    await state.update_data(key=key)
    await message.answer("➕ На сколько дней продлить?")
    await state.set_state(AdminExtend.waiting_for_days)

@dp.message(AdminExtend.waiting_for_days)
async def admin_extend_days(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число дней.")
        return
    data    = await state.get_data()
    key     = data["key"]
    kd      = db_key_get(key)
    new_exp = kd["expires"] + timedelta(days=days)
    db_key_update_expires(key, new_exp)
    db_log("key_extended", message.from_user.id, message.from_user.username,
           f"key={key} +{days}d new={new_exp.strftime('%d.%m.%Y %H:%M')}")
    await message.answer(
        f"✅ Ключ <code>{key}</code> продлён на <b>{days} дн.</b>\n"
        f"Новая дата: <code>{new_exp.strftime('%d.%m.%Y %H:%M')}</code>",
        parse_mode="HTML"
    )
    await state.clear()

# сократить ключ
@dp.callback_query(F.data == "admin_shorten")
async def admin_shorten_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer("⛔", show_alert=True)
        return
    await call.message.answer("➖ Введите ключ для сокращения:")
    await state.set_state(AdminShorten.waiting_for_key)
    await call.answer()

@dp.message(AdminShorten.waiting_for_key)
async def admin_shorten_got_key(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    key = message.text.strip()
    if not db_key_exists(key):
        await message.answer(f"❌ Ключ <code>{key}</code> не найден.", parse_mode="HTML")
        await state.clear()
        return
    await state.update_data(key=key)
    await message.answer("➖ На сколько дней сократить?")
    await state.set_state(AdminShorten.waiting_for_days)

@dp.message(AdminShorten.waiting_for_days)
async def admin_shorten_days(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число дней.")
        return
    data    = await state.get_data()
    key     = data["key"]
    kd      = db_key_get(key)
    new_exp = kd["expires"] - timedelta(days=days)
    if new_exp <= datetime.now():
        await message.answer("❌ После сокращения ключ уже будет истёкшим. Операция отменена.")
        await state.clear()
        return
    db_key_update_expires(key, new_exp)
    db_log("key_shortened", message.from_user.id, message.from_user.username,
           f"key={key} -{days}d new={new_exp.strftime('%d.%m.%Y %H:%M')}")
    await message.answer(
        f"✅ Ключ <code>{key}</code> сокращён на <b>{days} дн.</b>\n"
        f"Новая дата: <code>{new_exp.strftime('%d.%m.%Y %H:%M')}</code>",
        parse_mode="HTML"
    )
    await state.clear()

# ── /logs — генерация HTML страницы логов ────────────────────────────────────
@dp.message(Command("logs"))
async def cmd_logs_html(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Нет доступа.")
        return
    logs = db_logs_all_json()
    import json, os
    logs_json = json.dumps(logs, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Yrener Cheat — Логи</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=JetBrains+Mono:wght@400;600&family=Montserrat:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #060608; --card: #0e0e12; --border: #1e1e26;
    --accent: #fff; --dim: #555; --dim2: #888;
    --green: #00ff88; --red: #ff4466; --yellow: #ffcc00; --blue: #4499ff;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--accent); font-family:'Montserrat',sans-serif; min-height:100vh; }}

  header {{
    border-bottom: 1px solid var(--border);
    padding: 24px 32px;
    display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; background: var(--bg); z-index: 100;
  }}
  .header-left {{ display:flex; align-items:center; gap:16px; }}
  .logo-text {{ font-family:'Bebas Neue',sans-serif; font-size:28px; letter-spacing:4px; }}
  .logo-sub {{ font-size:11px; color:var(--dim2); letter-spacing:3px; text-transform:uppercase; }}
  .count-badge {{
    background: var(--card); border: 1px solid var(--border);
    padding: 6px 14px; border-radius:4px;
    font-family:'JetBrains Mono',monospace; font-size:13px; color:var(--dim2);
  }}
  .count-badge span {{ color:var(--accent); font-weight:600; }}

  .controls {{
    padding: 20px 32px;
    display: flex; gap: 12px; flex-wrap: wrap;
    border-bottom: 1px solid var(--border);
  }}
  .search-wrap {{
    position: relative; flex: 1; min-width: 200px;
  }}
  .search-wrap input {{
    width: 100%;
    background: var(--card); border: 1px solid var(--border);
    color: var(--accent); font-family:'JetBrains Mono',monospace; font-size:13px;
    padding: 10px 16px 10px 36px;
    border-radius: 4px; outline: none;
    transition: border-color 0.2s;
  }}
  .search-wrap input:focus {{ border-color: #333; }}
  .search-wrap::before {{
    content: '🔍'; position:absolute; left:10px; top:50%; transform:translateY(-50%);
    font-size:14px; pointer-events:none;
  }}

  .filter-btn {{
    background: var(--card); border: 1px solid var(--border);
    color: var(--dim2); font-family:'Montserrat',sans-serif; font-size:12px;
    letter-spacing: 1px; text-transform: uppercase;
    padding: 10px 18px; border-radius:4px; cursor:pointer;
    transition: all 0.2s;
  }}
  .filter-btn:hover, .filter-btn.active {{ border-color:#444; color:var(--accent); }}
  .filter-btn.active {{ background:#1a1a1a; }}

  .sort-btn {{
    background: var(--card); border: 1px solid var(--border);
    color: var(--dim2); font-family:'Montserrat',sans-serif; font-size:12px;
    padding: 10px 18px; border-radius:4px; cursor:pointer;
    transition: all 0.2s; display:flex; align-items:center; gap:8px;
  }}
  .sort-btn:hover {{ border-color:#444; color:var(--accent); }}
  .sort-arrow {{ font-size:16px; transition: transform 0.2s; }}

  .log-table {{ padding: 0 32px 60px; }}

  .log-row {{
    display: grid;
    grid-template-columns: 180px 140px 160px 1fr;
    gap: 0;
    border-bottom: 1px solid var(--border);
    transition: background 0.15s;
  }}
  .log-row:hover {{ background: var(--card); }}
  .log-row.header-row {{
    font-size: 10px; letter-spacing: 3px; text-transform: uppercase;
    color: var(--dim); padding: 14px 0 10px;
    position: sticky; top: 73px; background: var(--bg);
    border-bottom: 1px solid var(--border); z-index:50;
  }}
  .log-cell {{
    padding: 14px 16px;
    font-family:'JetBrains Mono',monospace; font-size:12px;
  }}
  .log-row.header-row .log-cell {{ padding: 0 16px 10px; font-family:'Montserrat',sans-serif; }}

  .ts {{ color: var(--dim2); }}
  .event-tag {{
    display:inline-block; padding:3px 8px; border-radius:3px;
    font-size:11px; font-weight:600; letter-spacing:0.5px;
  }}
  .ev-start       {{ background:#1a2a1a; color:var(--green); }}
  .ev-key_activated {{ background:#1a2510; color:#88ff44; }}
  .ev-key_created {{ background:#1a1a2a; color:var(--blue); }}
  .ev-key_deleted {{ background:#2a1a1a; color:var(--red); }}
  .ev-apk_sent    {{ background:#1a2020; color:#44ffcc; }}
  .ev-sub_expired {{ background:#2a1e10; color:var(--yellow); }}
  .ev-broadcast   {{ background:#201a2a; color:#cc88ff; }}
  .ev-other       {{ background:#1a1a1a; color:var(--dim2); }}

  .uname {{ color:var(--dim2); }}
  .uname b {{ color:var(--accent); }}
  .details {{ color:var(--dim); word-break:break-all; }}

  .empty-state {{
    text-align:center; padding:80px 0;
    color:var(--dim); font-size:14px; letter-spacing:2px;
  }}
  .empty-state .big {{ font-family:'Bebas Neue',sans-serif; font-size:48px; color:#1a1a1a; display:block; margin-bottom:12px; }}

  #log-count {{ transition: all 0.2s; }}

  @media(max-width:700px) {{
    .log-row {{ grid-template-columns: 1fr; }}
    .log-row.header-row {{ display:none; }}
    .log-cell {{ padding: 8px 16px; border-bottom:1px solid #111; }}
    .log-cell:last-child {{ border-bottom:none; }}
    .controls {{ padding:16px; }}
    .log-table {{ padding:0 0 60px; }}
    header {{ padding:16px; }}
  }}
</style>
</head>
<body>

<header>
  <div class="header-left">
    <div>
      <div class="logo-text">YRENER CHEAT</div>
      <div class="logo-sub">Панель логов</div>
    </div>
  </div>
  <div class="count-badge">Записей: <span id="log-count">0</span></div>
</header>

<div class="controls">
  <div class="search-wrap">
    <input type="text" id="search" placeholder="Поиск по юзернейму или команде..." oninput="applyFilters()">
  </div>
  <button class="filter-btn active" onclick="setFilter('all', this)">Все</button>
  <button class="filter-btn" onclick="setFilter('key', this)">🔑 Ключи</button>
  <button class="filter-btn" onclick="setFilter('apk', this)">📦 APK</button>
  <button class="filter-btn" onclick="setFilter('start', this)">🏠 Старты</button>
  <button class="filter-btn" onclick="setFilter('expired', this)">⏰ Истёкшие</button>
  <button class="sort-btn" id="sort-btn" onclick="toggleSort()">
    <span id="sort-label">Новые</span><span class="sort-arrow" id="sort-arrow">↓</span>
  </button>
</div>

<div class="log-table">
  <div class="log-row header-row">
    <div class="log-cell">Время</div>
    <div class="log-cell">Событие</div>
    <div class="log-cell">Пользователь</div>
    <div class="log-cell">Детали</div>
  </div>
  <div id="log-body"></div>
  <div class="empty-state" id="empty-state" style="display:none">
    <span class="big">0</span>Нет записей по фильтру
  </div>
</div>

<script>
const RAW = {logs_json};
let sortDesc = true;
let filterMode = 'all';

function getEventClass(ev) {{
  if (ev === 'start') return 'ev-start';
  if (ev.startsWith('key_activated')) return 'ev-key_activated';
  if (ev.startsWith('key_created')) return 'ev-key_created';
  if (ev.startsWith('key_deleted') || ev.startsWith('key_expired') || ev.startsWith('key_invalid') || ev.startsWith('key_wrong')) return 'ev-key_deleted';
  if (ev.startsWith('apk')) return 'ev-apk_sent';
  if (ev.startsWith('sub_expired')) return 'ev-sub_expired';
  if (ev.startsWith('broadcast')) return 'ev-broadcast';
  return 'ev-other';
}}

function matchFilter(ev) {{
  if (filterMode === 'all') return true;
  if (filterMode === 'key') return ev.startsWith('key');
  if (filterMode === 'apk') return ev.startsWith('apk');
  if (filterMode === 'start') return ev === 'start';
  if (filterMode === 'expired') return ev.startsWith('sub_expired');
  return true;
}}

function applyFilters() {{
  const q = document.getElementById('search').value.trim().toLowerCase();
  let data = [...RAW];
  if (!sortDesc) data.reverse();
  
  const rows = data.filter(r => {{
    if (!matchFilter(r.event)) return false;
    if (q) {{
      const uname = (r.username || '').toLowerCase();
      const ev = (r.event || '').toLowerCase();
      const det = (r.details || '').toLowerCase();
      if (!uname.includes(q) && !ev.includes(q) && !det.includes(q)) return false;
    }}
    return true;
  }});

  const body = document.getElementById('log-body');
  document.getElementById('log-count').textContent = rows.length;
  document.getElementById('empty-state').style.display = rows.length === 0 ? 'block' : 'none';

  body.innerHTML = rows.map(r => {{
    const uname = r.username ? `<b>@${{r.username}}</b>` : (r.user_id ? `id:${{r.user_id}}` : '—');
    const det = r.details || '';
    return `<div class="log-row">
      <div class="log-cell ts">${{r.ts || '—'}}</div>
      <div class="log-cell"><span class="event-tag ${{getEventClass(r.event)}}">${{r.event}}</span></div>
      <div class="log-cell uname">${{uname}}</div>
      <div class="log-cell details">${{det}}</div>
    </div>`;
  }}).join('');
}}

function setFilter(mode, btn) {{
  filterMode = mode;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  applyFilters();
}}

function toggleSort() {{
  sortDesc = !sortDesc;
  document.getElementById('sort-label').textContent = sortDesc ? 'Новые' : 'Старые';
  document.getElementById('sort-arrow').textContent = sortDesc ? '↓' : '↑';
  applyFilters();
}}

applyFilters();
</script>
</body>
</html>"""

    log_path = "/tmp/yrener_logs.html"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    await message.answer(f"📊 Генерирую страницу логов... ({len(logs)} записей)")
    try:
        file = FSInputFile(log_path, filename="yrener_logs.html")
        await message.answer_document(file, caption=f"📋 <b>Yrener Cheat — Логи</b>\n\nВсего записей: <b>{len(logs)}</b>\nОткройте файл в браузере.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# логи
@dp.callback_query(F.data == "admin_logs")
async def admin_logs(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("⛔", show_alert=True)
        return
    await call.answer()
    logs = db_logs_get(50)
    if not logs:
        await call.message.answer("📭 Логов пока нет.")
        return
    lines = []
    for l in logs:
        uname = f"@{l['username']}" if l.get("username") else f"id:{l.get('user_id','?')}"
        lines.append(f"<code>{l['ts']}</code> <b>{l['event']}</b> {uname}\n<i>{l.get('details') or ''}</i>")
    # отправляем по 15 записей
    for i in range(0, len(lines), 15):
        await call.message.answer("\n\n".join(lines[i:i+15]), parse_mode="HTML")

# ── APK УПРАВЛЕНИЕ (админ) ────────────────────────────────────────────────────
@dp.callback_query(F.data == "admin_apk")
async def admin_apk_panel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("⛔", show_alert=True)
        return
    await call.answer()
    apk = db_apk_get()
    if apk:
        await call.message.answer(
            f"📦 <b>Текущий APK:</b>\n\n"
            f"📄 Файл: <b>{apk['filename']}</b>\n"
            f"💾 Размер: <b>{apk['size_mb']} МБ</b>\n"
            f"🕐 Загружен: <code>{apk['uploaded']}</code>\n\n"
            f"Хотите удалить APK?",
            reply_markup=apk_delete_confirm_keyboard(), parse_mode="HTML"
        )
    else:
        await call.message.answer("📭 APK не загружен. Используйте кнопку <b>📤 Загрузить APK</b>.",
                                  parse_mode="HTML")

@dp.callback_query(F.data == "apk_delete_confirm")
async def apk_delete_confirmed(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("⛔", show_alert=True)
        return
    db_apk_delete()
    db_setting_set("apk_file_id", "")
    global APK_FILE_ID
    APK_FILE_ID = None
    db_log("apk_deleted", call.from_user.id, call.from_user.username)
    await call.message.edit_text("✅ APK успешно удалён.")

@dp.callback_query(F.data == "apk_delete_cancel")
async def apk_delete_cancelled(call: CallbackQuery):
    await call.message.edit_text("❌ Удаление отменено.")

# ── ЗАГРУЗИТЬ APK (админ) ─────────────────────────────────────────────────────
@dp.message(F.text == "📤 Загрузить APK")
async def upload_apk_prompt(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Нет доступа.")
        return
    apk = db_apk_get()
    if apk:
        await message.answer(
            f"⚠️ APK уже загружен: <b>{apk['filename']}</b> ({apk['size_mb']} МБ)\n\n"
            f"Сначала удалите его через <b>📋 Панель админа → 📦 Управление APK</b>.",
            parse_mode="HTML"
        )
        return
    await message.answer("📤 Отправьте APK-файл следующим сообщением.")
    await state.set_state(UploadApk.waiting_for_file)

@dp.message(UploadApk.waiting_for_file)
async def receive_apk(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    global APK_FILE_ID
    if message.document:
        file_id  = message.document.file_id
        filename = message.document.file_name or "app.apk"
        size_mb  = round(message.document.file_size / 1024 / 1024, 2)
        APK_FILE_ID = file_id
        db_apk_save(file_id, filename, size_mb)
        db_setting_set("apk_file_id", file_id)
        db_log("apk_uploaded", message.from_user.id, message.from_user.username,
               f"file={filename} size={size_mb}MB")
        await message.answer(
            f"✅ APK сохранён!\n"
            f"📄 Файл: <code>{filename}</code>\n"
            f"💾 Размер: <b>{size_mb} МБ</b>",
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Пожалуйста, отправьте именно файл (.apk).")
    await state.clear()

# ── ОБЪЯВЛЕНИЕ (рассылка) ─────────────────────────────────────────────────────
@dp.message(F.text == "📣 Объявление")
async def broadcast_prompt(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Нет доступа.")
        return
    await message.answer(
        "📣 Введите текст объявления.\n"
        "Он будет отправлен <b>всем пользователям</b>, которые когда-либо писали боту.",
        parse_mode="HTML"
    )
    await state.set_state(Broadcast.waiting_for_text)

@dp.message(Broadcast.waiting_for_text)
async def broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return
    text = message.text.strip()
    users = db_users_all_ids()
    if not users:
        await message.answer("📭 Нет пользователей для рассылки.")
        await state.clear()
        return
    sent = 0
    failed = 0
    broadcast_text = (
        f"🙈 <b>Сообщение от Yrener:</b>\n\n"
        f"{text}"
    )
    for uid in users:
        try:
            await bot.send_message(uid, broadcast_text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # flood control
    db_log("broadcast", message.from_user.id, message.from_user.username,
           f"sent={sent} failed={failed}")
    await message.answer(
        f"✅ Рассылка завершена!\n"
        f"📨 Отправлено: <b>{sent}</b>\n"
        f"❌ Не доставлено: <b>{failed}</b>",
        parse_mode="HTML"
    )
    await state.clear()

# ── ПОДДЕРЖКА ─────────────────────────────────────────────────────────────────
@dp.message(F.text == "🆘 Поддержка")
@spam_guard
async def support(message: Message):
    await message.answer(
        "<b>⚠️ Правила обращения в поддержку:</b>\n\n"
        "• Не спамьте сообщениями\n"
        "• Описывайте проблему чётко и по делу\n"
        "• Не оскорбляйте операторов\n"
        "• Одно обращение — одна проблема\n\n"
        "Вы согласны с правилами?",
        reply_markup=support_keyboard(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "support_agree")
async def support_agree(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Открыть поддержку", url=SUPPORT_BOT)
    ]])
    await call.message.edit_text("✅ Вы согласились с правилами. Нажмите кнопку ниже.", reply_markup=kb)

@dp.callback_query(F.data == "support_disagree")
async def support_disagree(call: CallbackQuery):
    await call.message.edit_text("❌ Вы отказались. Если передумаете — нажмите «🆘 Поддержка» снова.")

# ── КУБИК ─────────────────────────────────────────────────────────────────────
@dp.message(F.text == "🎲 Кубик")
@spam_guard
async def dice(message: Message):
    await message.answer_dice(emoji="🎲")

# ── ФОНОВАЯ ПРОВЕРКА ПОДПИСОК ─────────────────────────────────────────────────
async def subscription_checker():
    while True:
        now = datetime.now()
        with get_conn() as conn:
            all_users = conn.execute("SELECT * FROM users").fetchall()

        for row in all_users:
            uid      = row["user_id"]
            uname    = row["username"] or ""
            key_used = row["key"] or ""
            expires  = datetime.fromisoformat(row["expires"])
            mins_left = (expires - now).total_seconds() / 60

            kb_buy = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="💳 Купить ключ", url=PAYMENT_URL)
            ]])

            # Уведомление за ~1 минуту до конца
            if 0 < mins_left <= 1.5:
                already = db_setting_get(f"warn1m_{uid}_{key_used}")
                if not already:
                    try:
                        await bot.send_message(
                            uid,
                            f"⚠️ У вас осталась <b>1 минута</b> до окончания подписки!\n\n"
                            f"Если хотите продолжить пользоваться — купите новый ключ прямо сейчас.\n"
                            f"После истечения доступ к APK и меню будет закрыт.",
                            reply_markup=kb_buy,
                            parse_mode="HTML"
                        )
                        db_setting_set(f"warn1m_{uid}_{key_used}", "1")
                        db_log("sub_warn_1min", uid, uname, f"key={key_used}")
                    except Exception:
                        pass

            # Подписка истекла — уведомить и удалить
            if mins_left <= 0:
                already = db_setting_get(f"expired_{uid}_{key_used}")
                if not already:
                    try:
                        await bot.send_message(
                            uid,
                            f"❌ Ваша подписка закончилась!\n\n"
                            f"Ключ <code>{key_used}</code> больше не активен.\n"
                            f"Купите новый ключ чтобы продолжить пользоваться.",
                            reply_markup=kb_buy,
                            parse_mode="HTML"
                        )
                        db_setting_set(f"expired_{uid}_{key_used}", "1")
                        db_log("sub_expired_notified", uid, uname, f"key={key_used}")
                    except Exception:
                        pass
                    # Удаляем — подписка отключена
                    with get_conn() as conn:
                        conn.execute("DELETE FROM users WHERE user_id=?", (uid,))

        await asyncio.sleep(30)

# ── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    global APK_FILE_ID
    apk = db_apk_get()
    if apk:
        APK_FILE_ID = apk["file_id"]
    # Регистрация команд (появляются при вводе /)
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start",  description="🏠 Главное меню"),
        BotCommand(command="buy",    description="💳 Купить ключ"),
        BotCommand(command="enter",  description="🔑 Ввести ключ"),
        BotCommand(command="exit",   description="🔙 Вернуться в главное меню"),
    ])
    asyncio.create_task(subscription_checker())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
