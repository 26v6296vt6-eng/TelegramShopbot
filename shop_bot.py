"""
Telegram-бот для продаж (aiogram 3.x) с остатками и админ-панелью.
Запуск:
    pip install aiogram
    export BOT_TOKEN="токен_от_BotFather"
    export ADMIN_ID="ваш_telegram_id"   # узнать: бот @userinfobot
    python shop_bot.py
Админ-панель: команда /admin (только для ADMIN_ID).
"""
import asyncio
import logging
import os
import sqlite3

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

WELCOME = "Добро пожаловать в магазин! 👋"
AFTER_ORDER = "Заказ принят ✅ Менеджер скоро свяжется с вами для оплаты и доставки."

# Стартовые товары (добавятся только при первом запуске, потом управляйте через /admin)
SEED = [
    ("Под-система Model A", 2500, "Компактная, 800 мАч, USB-C.", 10),
    ("Под-система Model B", 3200, "1000 мАч, регулировка тяги.", 5),
    ("Картридж 2 мл (2 шт.)", 700, "Сменные картриджи.", 0),
]

DB = "shop.db"
carts: dict[int, dict[int, int]] = {}


# ======================= БАЗА ДАННЫХ =======================
def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS adults (user_id INTEGER PRIMARY KEY)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER,
            desc TEXT, stock INTEGER DEFAULT 0)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
            name TEXT, phone TEXT, address TEXT, items TEXT, total INTEGER,
            created TEXT DEFAULT CURRENT_TIMESTAMP)"""
        )
        if c.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            c.executemany("INSERT INTO products (name, price, desc, stock) VALUES (?,?,?,?)", SEED)


def is_adult(user_id: int) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM adults WHERE user_id=?", (user_id,)).fetchone() is not None


def all_products():
    with db() as c:
        return c.execute("SELECT * FROM products ORDER BY id").fetchall()


def get_product(pid: int):
    with db() as c:
        return c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()


def stock_list(products) -> str:
    """Список наличия: ✅ есть / ❌ нет."""
    if not products:
        return "Товаров пока нет."
    lines = []
    for p in products:
        if p["stock"] > 0:
            lines.append(f"✅ {p['name']} — {p['price']} ₽ (в наличии: {p['stock']})")
        else:
            lines.append(f"❌ {p['name']} — нет в наличии")
    return "\n".join(lines)


# ======================= РОУТЕРЫ =======================
async def adult_filter(event, **_) -> bool:
    return is_adult(event.from_user.id)


async def admin_filter(event, **_) -> bool:
    return event.from_user.id == ADMIN_ID


class Order(StatesGroup):
    name = State()
    phone = State()
    address = State()


class AddProduct(StatesGroup):
    name = State()
    price = State()
    desc = State()
    stock = State()


class SetStock(StatesGroup):
    value = State()


admin = Router()
admin.message.filter(admin_filter)
admin.callback_query.filter(admin_filter)

public = Router()

shop = Router()
shop.message.filter(adult_filter)
shop.callback_query.filter(adult_filter)


async def show(cb: CallbackQuery, text: str, markup=None):
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except Exception:
        await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🛍 Каталог", callback_data="catalog")
    kb.button(text="🧺 Корзина", callback_data="cart")
    kb.adjust(2)
    return kb.as_markup()


# ======================= ПРОВЕРКА ВОЗРАСТА =======================
@public.message(CommandStart())
async def start(m: Message):
    if is_adult(m.from_user.id):
        return await m.answer(WELCOME, reply_markup=main_menu())
    kb = InlineKeyboardBuilder()
    kb.button(text="Мне есть 18 лет", callback_data="age:yes")
    kb.button(text="Мне нет 18", callback_data="age:no")
    kb.adjust(1)
    await m.answer("Товары только для лиц старше 18 лет. Подтвердите возраст:", reply_markup=kb.as_markup())


@public.callback_query(F.data == "age:yes")
async def age_yes(cb: CallbackQuery):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO adults VALUES (?)", (cb.from_user.id,))
    await show(cb, WELCOME, main_menu())


@public.callback_query(F.data == "age:no")
async def age_no(cb: CallbackQuery):
    await show(cb, "К сожалению, мы не можем вас обслужить.")


# ======================= КАТАЛОГ =======================
@shop.callback_query(F.data == "menu")
async def menu(cb: CallbackQuery):
    await show(cb, WELCOME, main_menu())


@shop.callback_query(F.data == "catalog")
async def catalog(cb: CallbackQuery):
    products = all_products()
    kb = InlineKeyboardBuilder()
    for p in products:
        if p["stock"] > 0:
            kb.button(text=f"{p['name']} — {p['price']} ₽", callback_data=f"item:{p['id']}")
    kb.button(text="⬅️ Назад", callback_data="menu")
    kb.adjust(1)
    await show(cb, "Каталог и наличие:\n\n" + stock_list(products), kb.as_markup())


@shop.callback_query(F.data.startswith("item:"))
async def item(cb: CallbackQuery):
    p = get_product(int(cb.data.split(":")[1]))
    if not p:
        return await cb.answer("Товар больше не продаётся", show_alert=True)
    kb = InlineKeyboardBuilder()
    if p["stock"] > 0:
        kb.button(text="➕ В корзину", callback_data=f"add:{p['id']}")
    kb.button(text="⬅️ Каталог", callback_data="catalog")
    kb.adjust(1)
    status = f"В наличии: {p['stock']}" if p["stock"] > 0 else "Нет в наличии"
    await show(cb, f"{p['name']}\n{p['price']} ₽\n{status}\n\n{p['desc']}", kb.as_markup())


@shop.callback_query(F.data.startswith("add:"))
async def add(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    p = get_product(pid)
    cart = carts.setdefault(cb.from_user.id, {})
    if not p or cart.get(pid, 0) >= p["stock"]:
        return await cb.answer("Больше нет в наличии", show_alert=True)
    cart[pid] = cart.get(pid, 0) + 1
    await cb.answer(f"Добавлено ✅ (в корзине: {cart[pid]})")


# ======================= КОРЗИНА =======================
def cart_text(uid: int):
    lines, total = [], 0
    for pid, qty in carts.get(uid, {}).items():
        p = get_product(pid)
        if not p:
            continue
        lines.append(f"• {p['name']} × {qty} = {p['price'] * qty} ₽")
        total += p["price"] * qty
    return "\n".join(lines), total


@shop.callback_query(F.data == "cart")
async def cart_view(cb: CallbackQuery):
    text, total = cart_text(cb.from_user.id)
    kb = InlineKeyboardBuilder()
    if not text:
        kb.button(text="⬅️ Назад", callback_data="menu")
        return await show(cb, "Корзина пуста.", kb.as_markup())
    kb.button(text="✅ Оформить заказ", callback_data="checkout")
    kb.button(text="🗑 Очистить", callback_data="clear")
    kb.button(text="⬅️ Назад", callback_data="menu")
    kb.adjust(1)
    await show(cb, f"Ваша корзина:\n{text}\n\nИтого: {total} ₽", kb.as_markup())


@shop.callback_query(F.data == "clear")
async def clear(cb: CallbackQuery):
    carts.pop(cb.from_user.id, None)
    await show(cb, "Корзина очищена.", main_menu())


# ======================= ОФОРМЛЕНИЕ ЗАКАЗА =======================
@shop.callback_query(F.data == "checkout")
async def checkout(cb: CallbackQuery, state: FSMContext):
    if not cart_text(cb.from_user.id)[0]:
        return await cb.answer("Корзина пуста", show_alert=True)
    await state.set_state(Order.name)
    await cb.message.answer("Как к вам обращаться?")
    await cb.answer()


@shop.message(Order.name)
async def get_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text)
    await state.set_state(Order.phone)
    await m.answer("Ваш номер телефона:")


@shop.message(Order.phone)
async def get_phone(m: Message, state: FSMContext):
    await state.update_data(phone=m.text)
    await state.set_state(Order.address)
    await m.answer("Адрес доставки (или город/пункт самовывоза):")


@shop.message(Order.address)
async def get_address(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    uid = m.from_user.id
    text, total = cart_text(uid)
    conn = db()
    try:
        with conn:  # транзакция: при ошибке всё откатывается
            for pid, qty in carts.get(uid, {}).items():
                cur = conn.execute(
                    "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                    (qty, pid, qty),
                )
                if cur.rowcount == 0:
                    raise ValueError
            oid = conn.execute(
                "INSERT INTO orders (user_id, username, name, phone, address, items, total) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid, m.from_user.username, data["name"], data["phone"], m.text, text, total),
            ).lastrowid
    except ValueError:
        return await m.answer(
            "Пока вы оформляли заказ, часть товаров закончилась 😔 Проверьте корзину и наличие.",
            reply_markup=main_menu(),
        )
    carts.pop(uid, None)
    await m.answer(f"Заказ №{oid}\n{text}\n\nИтого: {total} ₽\n\n{AFTER_ORDER}", reply_markup=main_menu())
    if ADMIN_ID:
        uname = f"@{m.from_user.username}" if m.from_user.username else f"id {uid}"
        await bot.send_message(
            ADMIN_ID,
            f"🆕 Заказ №{oid}\nКлиент: {data['name']} ({uname})\nТелефон: {data['phone']}\n"
            f"Адрес: {m.text}\n\n{text}\n\nИтого: {total} ₽",
        )


# ======================= АДМИН-ПАНЕЛЬ =======================
def admin_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Остатки", callback_data="a:list")
    kb.button(text="➕ Добавить товар", callback_data="a:add")
    kb.button(text="✏️ Изменить остаток", callback_data="a:stock")
    kb.button(text="🗑 Удалить товар", callback_data="a:del")
    kb.button(text="📦 Последние заказы", callback_data="a:orders")
    kb.adjust(1)
    return kb.as_markup()


def back_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В админ-меню", callback_data="a:menu")
    return kb.as_markup()


@admin.message(Command("admin"))
async def admin_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Админ-панель:", reply_markup=admin_menu())


@admin.callback_query(F.data == "a:menu")
async def a_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, "Админ-панель:", admin_menu())


@admin.callback_query(F.data == "a:list")
async def a_list(cb: CallbackQuery):
    await show(cb, "Остатки:\n\n" + stock_list(all_products()), back_kb())


@admin.callback_query(F.data == "a:orders")
async def a_orders(cb: CallbackQuery):
    with db() as c:
        rows = c.execute(
            "SELECT id, name, phone, total, created FROM orders ORDER BY id DESC LIMIT 10"
        ).fetchall()
    text = "\n".join(f"№{r['id']} | {r['name']} | {r['phone']} | {r['total']} ₽ | {r['created']}" for r in rows)
    await show(cb, text or "Заказов пока нет.", back_kb())


# --- добавить товар ---
@admin.callback_query(F.data == "a:add")
async def a_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddProduct.name)
    await cb.message.answer("Название товара:")
    await cb.answer()


@admin.message(AddProduct.name)
async def ap_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text)
    await state.set_state(AddProduct.price)
    await m.answer("Цена (число, ₽):")


@admin.message(AddProduct.price)
async def ap_price(m: Message, state: FSMContext):
    if not (m.text or "").isdigit():
        return await m.answer("Введите цену числом, например 2500")
    await state.update_data(price=int(m.text))
    await state.set_state(AddProduct.desc)
    await m.answer("Описание (или напишите «-» чтобы пропустить):")


@admin.message(AddProduct.desc)
async def ap_desc(m: Message, state: FSMContext):
    await state.update_data(desc="" if m.text == "-" else m.text)
    await state.set_state(AddProduct.stock)
    await m.answer("Количество на складе (число):")


@admin.message(AddProduct.stock)
async def ap_stock(m: Message, state: FSMContext):
    if not (m.text or "").isdigit():
        return await m.answer("Введите количество числом, например 10")
    d = await state.get_data()
    await state.clear()
    with db() as c:
        c.execute(
            "INSERT INTO products (name, price, desc, stock) VALUES (?,?,?,?)",
            (d["name"], d["price"], d["desc"], int(m.text)),
        )
    await m.answer(f"Товар «{d['name']}» добавлен ✅", reply_markup=admin_menu())


# --- изменить остаток ---
def product_picker(prefix: str):
    kb = InlineKeyboardBuilder()
    for p in all_products():
        kb.button(text=f"{p['name']} (сейчас: {p['stock']})", callback_data=f"{prefix}:{p['id']}")
    kb.button(text="⬅️ В админ-меню", callback_data="a:menu")
    kb.adjust(1)
    return kb.as_markup()


@admin.callback_query(F.data == "a:stock")
async def a_stock(cb: CallbackQuery):
    await show(cb, "Выберите товар:", product_picker("a:sp"))


@admin.callback_query(F.data.startswith("a:sp:"))
async def a_stock_pick(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":")[2])
    await state.set_state(SetStock.value)
    await state.update_data(pid=pid)
    await cb.message.answer("Новое количество на складе (число, 0 = нет в наличии):")
    await cb.answer()


@admin.message(SetStock.value)
async def a_stock_set(m: Message, state: FSMContext):
    if not (m.text or "").isdigit():
        return await m.answer("Введите число, например 7")
    pid = (await state.get_data())["pid"]
    await state.clear()
    with db() as c:
        c.execute("UPDATE products SET stock=? WHERE id=?", (int(m.text), pid))
    await m.answer("Остаток обновлён ✅\n\n" + stock_list(all_products()), reply_markup=admin_menu())


# --- удалить товар ---
@admin.callback_query(F.data == "a:del")
async def a_del(cb: CallbackQuery):
    await show(cb, "Какой товар удалить?", product_picker("a:dp"))


@admin.callback_query(F.data.startswith("a:dp:"))
async def a_del_pick(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    p = get_product(pid)
    if not p:
        return await cb.answer("Уже удалён", show_alert=True)
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, удалить", callback_data=f"a:dc:{pid}")
    kb.button(text="Отмена", callback_data="a:menu")
    kb.adjust(2)
    await show(cb, f"Удалить «{p['name']}»?", kb.as_markup())


@admin.callback_query(F.data.startswith("a:dc:"))
async def a_del_confirm(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    with db() as c:
        c.execute("DELETE FROM products WHERE id=?", (pid,))
    await show(cb, "Товар удалён ✅", admin_menu())


# ======================= ЗАПУСК =======================
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(admin, public, shop)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
