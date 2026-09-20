import asyncio
import logging
import os
import secrets
import psycopg2
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.filters import CommandStart
from fastapi import FastAPI
import uvicorn

# ==================== НАСТРОЙКА БОТА И СЕРВЕРА ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_БОТА")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@host:port/dbname")
TICKET_PRICE = 5        # Стоимость билета в Telegram Stars со 2-го раза
FRIENDS_REQUIRED = 3    # Сколько друзей нужно пригласить для 1-го прокрута

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "alive", "message": "Бот и реферальная система работают!"}

# ==================== РАБОТА С POSTGRESQL ====================
def get_db_connection():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS box_prizes (
            id SERIAL PRIMARY KEY,
            prize_name TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            referrer_id BIGINT,
            friends_invited INTEGER DEFAULT 0,
            free_spin_used BOOLEAN DEFAULT FALSE
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()

def register_user(user_id: int, referrer_id: int = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
    if cursor.fetchone() is None:
        if referrer_id and referrer_id != user_id:
            cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (referrer_id,))
            if cursor.fetchone():
                cursor.execute("INSERT INTO users (user_id, referrer_id) VALUES (%s, %s)", (user_id, referrer_id))
                cursor.execute("UPDATE users SET friends_invited = friends_invited + 1 WHERE user_id = %s", (referrer_id,))
                conn.commit()
                cursor.close()
                conn.close()
                return
        cursor.execute("INSERT INTO users (user_id) VALUES (%s)", (user_id,))
        conn.commit()
    cursor.close()
    conn.close()

def get_user_stats(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT friends_invited, free_spin_used FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row if row else (0, False)

def mark_free_spin_used(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET free_spin_used = TRUE WHERE user_id = %s", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

def refill_box_if_empty():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM box_prizes")
    count = cursor.fetchone()
    if count == 0:
        prizes = ["🧸 Цифровой подарок: МИШКА (15 звёзд)"] + ["🎁 Утешительный приз: 1 Звезда обратно"] * 9
        secrets.SystemRandom().shuffle(prizes)
        for prize in prizes:
            cursor.execute("INSERT INTO box_prizes (prize_name) VALUES (%s)", (prize,))
        conn.commit()
    cursor.close()
    conn.close()

def pull_random_prize() -> str:
    refill_box_if_empty()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, prize_name FROM box_prizes ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    prize_id, prize_name = row, row
    cursor.execute("DELETE FROM box_prizes WHERE id = %s", (prize_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return prize_name

# ==================== ИНТЕРФЕЙС И КНОПКИ ====================
def get_main_keyboard(invited_count: int, free_used: bool):
    buttons = []
    if not free_used:
        if invited_count >= FRIENDS_REQUIRED:
            buttons.append([InlineKeyboardButton(text="🎁 Открыть БЕСПЛАТНУЮ коробку!", callback_data="start_free_spin")])
        else:
            buttons.append([InlineKeyboardButton(text=f"👥 Приглашено: {invited_count}/{FRIENDS_REQUIRED} друзей", callback_data="invite_info")])
    else:
        buttons.append([InlineKeyboardButton(text=f"🎟️ Купить билет ({TICKET_PRICE} ⭐️)", callback_data="buy_ticket")])
        
    buttons.append([InlineKeyboardButton(text="🔗 Получить ссылку для друзей", callback_data="get_link")])
    buttons.append([InlineKeyboardButton(text="🛡️ Честность", callback_data="rules")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_spin_keyboard(is_free: bool):
    callback_data = "spin_box_free" if is_free else "spin_box_paid"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Открыть коробку", callback_data=callback_data)]
    ])

# ==================== ЛОГИКА БОТА И ОБРАБОТЧИКИ ====================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    
    register_user(message.from_user.id, referrer_id)
    friends_invited, free_spin_used = get_user_stats(message.from_user.id)
    
    welcome_text = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Добро пожаловать в беспроигрышную коробку удачи! 🧸\n"
    )
    if not free_spin_used:
        welcome_text += (
            f"🔥 Твой **первый прокрут БЕСПЛАТНЫЙ**!\n"
            f"Но чтобы активировать его, тебе нужно **пригласить ровно {FRIENDS_REQUIRED} друзей** в бота по своей ссылке.\n\n"
            f"Как только друзья зайдут, кнопка открытия коробки станет доступна автоматически!"
        )
    else:
        welcome_text += f"Твой бесплатный билет использован. Теперь ты можешь покупать билеты за **{TICKET_PRICE} звёзд** и продолжать ловить Мишек!"

    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(friends_invited, free_spin_used),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "invite_info")
async def invite_info(callback: CallbackQuery):
    friends_invited, _ = get_user_stats(callback.from_user.id)
    needed = FRIENDS_REQUIRED - friends_invited
    await callback.answer(f"Зови друзей! Осталось пригласить: {needed}", show_alert=True)

@dp.callback_query(F.data == "get_link")
async def get_link(callback: CallbackQuery):
    bot_info = await bot.get_me()
    invite_url = f"https://t.me{bot_info.username}?start={callback.from_user.id}"
    await callback.message.answer(
        "🔗 Отправь эту ссылку своим друзьям:\n\n"
        f"`{invite_url}`\n\n"
        f"Как только {FRIENDS_REQUIRED} человека запустят бота по ней, ты сможешь открыть свою первую беспроигрышную коробку совершенно бесплатно!",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "rules")
async def show_rules(callback: CallbackQuery):
    await callback.message.edit_text(
        "🛡️ **100% Честность и Прозрачность**\n\n"
        "Игра ведется по системе ограниченных партий (коробок). "
        "В каждой коробке из 10 билетов заложен ровно **1 Главный Мишка** и **9 утешительных призов**.\n\n"
        "Алгоритм случайным образом выдает билеты из коробки. Администратор никак не может повлиять на то, "
        "какой именно билет достанется тебе. Всё абсолютно честно!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
        ]),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    friends_invited, free_spin_used = get_user_stats(callback.from_user.id)
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_keyboard(friends_invited, free_spin_used))

# --- БЕСПЛАТНЫЙ ПРОКРУТ ЗА ДРУЗЕЙ ---
@dp.callback_query(F.data == "start_free_spin")
async def start_free_spin(callback: CallbackQuery):
    friends_invited, free_spin_used = get_user_stats(callback.from_user.id)
    if free_spin_used:
        await callback.answer("Вы уже использовали бесплатный прокрут!", show_alert=True)
        return
    if friends_invited < FRIENDS_REQUIRED:
        await callback.answer("Недостаточно друзей!", show_alert=True)
        return
    await callback.message.edit_text(
        "🎉 Условия выполнены! Ваш бесплатный билет активирован.",
        reply_markup=get_spin_keyboard(is_free=True)
    )

# --- ПЛАТНЫЙ ПРОКРУТ (ЧЕРЕЗ TELEGRAM STARS) ---
@dp.callback_query(F.data == "buy_ticket")
async def process_payment(callback: CallbackQuery):
    try:
        await callback.message.answer_invoice(
            title="Билет на розыгрыш",
            description="Оплата 1 билета для открытия коробки.",
            payload="paid_ticket",
            provider_token="",  
            currency="XTR",     
            prices=[LabeledPrice(label="1 билет", amount=TICKET_PRICE)]
        )
        await callback.answer()
    except Exception as e:
        logging.error(f"Ошибка платежа: {e}")
        await callback.answer("Ошибка создания счета.", show_alert=True)

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    await message.answer(
        "💚 **Оплата принята!**",
        reply_markup=get_spin_keyboard(is_free=False),
        parse_mode="Markdown"
    )

# --- ОБРАБОТКА И ВЫДАЧА ПРИЗА ---
@dp.callback_query(F.data.startswith("spin_box_"))
async def play_lottery(callback: CallbackQuery):
    is_free = callback.data == "spin_box_free"
    user_id = callback.from_user.id
    friends_invited, free_spin_used = get_user_stats(user_id)
    
    if is_free:
        if free_spin_used or friends_invited < FRIENDS_REQUIRED:
            await callback.answer("Ошибка активации бонуса.", show_alert=True)
            return
        mark_free_spin_used(user_id)

    await callback.message.edit_text("📦 Открываем коробку с призом...")
    await asyncio.sleep(1.0)
    
    final_prize = pull_random_prize()
    friends_invited, free_spin_used = get_user_stats(user_id)
    
    await callback.message.answer(
        f"🎉 **ВЫИГРЫШ:**\n\n➡️ **{final_prize}**\n\n"
        "ℹ️ *Для получения цифрового Мишки напишите админу.*",
        reply_markup=get_main_keyboard(friends_invited, free_spin_used),
        parse_mode="Markdown"
    )
    await callback.message.delete()

# ==================== ЗАПУСК ====================
async def run_bot():
    init_db()
    refill_box_if_empty()
    await dp.start_polling(bot)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_bot())

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
