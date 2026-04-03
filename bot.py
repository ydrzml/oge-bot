import logging
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import requests
from dotenv import load_dotenv

load_dotenv()  # Загружает переменные из файла .env

# ===================== НАСТРОЙКИ =====================
BOT_TOKEN              = os.getenv("BOT_TOKEN")
TUTOR_CHAT_ID          = os.getenv("TUTOR_CHAT_ID")
NOTION_TOKEN           = os.getenv("NOTION_TOKEN")
NOTION_SLOTS_DB_ID     = os.getenv("NOTION_SLOTS_DB_ID")
NOTION_STUDENTS_DB_ID  = os.getenv("NOTION_STUDENTS_DB_ID")

WELCOME_TEXT = """👋 Привет! Я бот репетитора по информатике (ОГЭ).

Меня зовут *Yadro* — учусь в Центральном университете, сам сдал ОГЭ и ЕГЭ на высокие баллы 🎯

Готовлю к ОГЭ по информатике:
✅ Индивидуальная программа под тебя
✅ Без стресса, в твоём темпе
✅ Первое занятие — бесплатно (2 часа)

💰 Стоимость: 1200 руб/час (онлайн)

Нажми кнопку ниже, чтобы записаться 👇"""

DAY_ORDER = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
CHOOSING_SLOT, ENTERING_NAME, ENTERING_CONTACT = range(3)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===================== NOTION API =====================
def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

def get_free_slots():
    """Читает слоты из Notion где Занят = false"""
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_SLOTS_DB_ID}/query",
            headers=notion_headers(),
            json={
                "filter": {
                    "property": "Занят",
                    "checkbox": {"equals": False}
                }
            }
        )
        results = r.json().get("results", [])
        slots = []
        for page in results:
            props = page["properties"]
            day = props.get("День", {}).get("select", {})
            time = props.get("Время", {}).get("rich_text", [])
            if day and time:
                slots.append({
                    "label": f"{day['name']} {time[0]['text']['content']}",
                    "page_id": page["id"]
                })
        slots.sort(key=lambda s: (
            DAY_ORDER.get(s["label"].split()[0], 9),
            s["label"].split()[1]
        ))
        return slots
    except Exception as e:
        logger.error(f"Ошибка получения слотов: {e}")
        return []

def mark_slot_busy(slot_page_id):
    """Помечает слот как занятый в Notion"""
    try:
        requests.patch(
            f"https://api.notion.com/v1/pages/{slot_page_id}",
            headers=notion_headers(),
            json={"properties": {"Занят": {"checkbox": True}}}
        )
    except Exception as e:
        logger.error(f"Ошибка пометки слота: {e}")

def mark_slot_free(slot_label):
    """Освобождает слот при отмене записи"""
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_SLOTS_DB_ID}/query",
            headers=notion_headers(),
            json={
                "filter": {
                    "and": [
                        {"property": "День", "select": {"equals": slot_label.split()[0]}},
                        {"property": "Время", "rich_text": {"equals": slot_label.split()[1]}},
                    ]
                }
            }
        )
        results = r.json().get("results", [])
        for page in results:
            requests.patch(
                f"https://api.notion.com/v1/pages/{page['id']}",
                headers=notion_headers(),
                json={"properties": {"Занят": {"checkbox": False}}}
            )
    except Exception as e:
        logger.error(f"Ошибка освобождения слота: {e}")

def save_student(name, contact, slot_label, slot_page_id, user_id):
    data = {
        "parent": {"database_id": NOTION_STUDENTS_DB_ID},
        "properties": {
            "Имя": {"title": [{"text": {"content": name}}]},
            "Контакт": {"rich_text": [{"text": {"content": contact}}]},
            "Слот": {"rich_text": [{"text": {"content": slot_label}}]},
            "Slot Page ID": {"rich_text": [{"text": {"content": slot_page_id}}]},
            "Telegram ID": {"rich_text": [{"text": {"content": str(user_id)}}]},
            "Статус": {"select": {"name": "активна"}},
            "Дата записи": {"rich_text": [{"text": {"content": datetime.now().strftime("%d.%m.%Y %H:%M")}}]},
        }
    }
    try:
        r = requests.post("https://api.notion.com/v1/pages", headers=notion_headers(), json=data)
        return r.json().get("id")
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        return None

def get_user_bookings(user_id):
    data = {
        "filter": {
            "and": [
                {"property": "Telegram ID", "rich_text": {"equals": str(user_id)}},
                {"property": "Статус", "select": {"equals": "активна"}}
            ]
        }
    }
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_STUDENTS_DB_ID}/query",
            headers=notion_headers(), json=data
        )
        results = r.json().get("results", [])
        bookings = []
        for page in results:
            slot = page["properties"]["Слот"]["rich_text"]
            slot_pid = page["properties"].get("Slot Page ID", {}).get("rich_text", [])
            if slot:
                bookings.append({
                    "slot_label": slot[0]["text"]["content"],
                    "slot_page_id": slot_pid[0]["text"]["content"] if slot_pid else "",
                    "page_id": page["id"]
                })
        return bookings
    except Exception as e:
        logger.error(f"Ошибка получения записей: {e}")
        return []

def cancel_booking(page_id, slot_label, slot_page_id):
    try:
        requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=notion_headers(),
            json={"properties": {"Статус": {"select": {"name": "отменена"}}}}
        )
        if slot_page_id:
            requests.patch(
                f"https://api.notion.com/v1/pages/{slot_page_id}",
                headers=notion_headers(),
                json={"properties": {"Занят": {"checkbox": False}}}
            )
        return True
    except Exception as e:
        logger.error(f"Ошибка отмены: {e}")
        return False

# ===================== ВСПОМОГАТЕЛЬНОЕ =====================
def to_yakutsk(slot_label):
    try:
        day, time = slot_label.split()
        h, m = map(int, time.split(":"))
        h = (h + 6) % 24
        return f"{day} {h:02d}:{m:02d}"
    except:
        return slot_label

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Записаться на занятие", callback_data="book")],
        [InlineKeyboardButton("📋 Мои записи", callback_data="my_bookings")],
        [InlineKeyboardButton("❓ Как проходит занятие", callback_data="how_it_works")],
    ])

# ===================== ХЕНДЛЕРЫ =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "book":                     return await show_slots(update, context)
    elif data == "my_bookings":            return await show_my_bookings(update, context)
    elif data == "how_it_works":           return await how_it_works(update, context)
    elif data.startswith("slot_"):         return await slot_selected(update, context)
    elif data.startswith("cancel_"):       return await do_cancel_booking(update, context)
    elif data == "back":                   return await back_to_menu(update, context)

async def show_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    slots = get_free_slots()
    if not slots:
        await query.edit_message_text(
            "😔 Сейчас свободных слотов нет.\nНапиши напрямую — договоримся!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]])
        )
        return ConversationHandler.END
    keyboard = []
    for s in slots:
        label = f"🕐 {s['label']} МСК  |  {to_yakutsk(s['label'])} ЯКТ"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"slot_{s['page_id']}__{s['label']}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])
    await query.edit_message_text(
        "📅 Выбери удобное время:\n_(МСК = московское, ЯКТ = якутское)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING_SLOT

async def slot_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, slot_page_id, slot_label = query.data.split("__", 2) if "__" in query.data else ("", "", "")
    # Формат callback: slot_{page_id}__{label}
    raw = query.data[len("slot_"):]
    parts = raw.split("__")
    slot_page_id = parts[0]
    slot_label = parts[1] if len(parts) > 1 else raw

    context.user_data["slot_label"] = slot_label
    context.user_data["slot_page_id"] = slot_page_id
    await query.edit_message_text(
        f"✅ Выбрано: *{slot_label}* МСК / *{to_yakutsk(slot_label)}* ЯКТ\n\nКак тебя зовут? (Имя и фамилия)",
        parse_mode="Markdown"
    )
    return ENTERING_NAME

async def entering_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("📱 Оставь контакт для связи\n(Telegram @username или номер телефона):")
    return ENTERING_CONTACT

async def entering_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.text
    name = context.user_data["name"]
    slot_label = context.user_data["slot_label"]
    slot_page_id = context.user_data["slot_page_id"]
    user_id = update.effective_user.id

    save_student(name, contact, slot_label, slot_page_id, user_id)
    mark_slot_busy(slot_page_id)  # ← слот исчезает из списка

    try:
        await context.bot.send_message(
            TUTOR_CHAT_ID,
            f"🔔 *Новая запись!*\n\n👤 {name}\n📱 {contact}\n🕐 {slot_label} МСК / {to_yakutsk(slot_label)} ЯКТ",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления: {e}")

    await update.message.reply_text(
        f"🎉 *Запись подтверждена!*\n\n"
        f"📅 *{slot_label}* МСК / *{to_yakutsk(slot_label)}* ЯКТ\n\n"
        f"⚠️ Отменить можно за 24+ часа — через кнопку «Мои записи».\n\nЖду тебя! 😊",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

async def show_my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    bookings = get_user_bookings(update.effective_user.id)
    if not bookings:
        await query.edit_message_text(
            "У тебя нет активных записей.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]])
        )
        return
    keyboard = []
    for b in bookings:
        cb = f"cancel_{b['page_id']}__{b['slot_page_id']}__{b['slot_label']}"
        keyboard.append([InlineKeyboardButton(f"❌ Отменить: {b['slot_label']}", callback_data=cb)])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])
    await query.edit_message_text(
        "📋 *Твои записи:*\n\n⚠️ Отмена возможна за 24+ часа до занятия.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def do_cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    raw = query.data[len("cancel_"):]
    parts = raw.split("__")
    page_id = parts[0]
    slot_page_id = parts[1] if len(parts) > 1 else ""
    slot_label = parts[2] if len(parts) > 2 else ""

    success = cancel_booking(page_id, slot_label, slot_page_id)
    if success:
        try:
            await context.bot.send_message(TUTOR_CHAT_ID, f"❌ Ученик отменил запись: *{slot_label}*", parse_mode="Markdown")
        except:
            pass
        await query.edit_message_text(
            f"✅ Запись на *{slot_label}* отменена. Слот снова свободен.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]])
        )
    else:
        await query.edit_message_text(
            "⚠️ Не удалось отменить. Напиши напрямую.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]])
        )

async def how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "📖 *Как проходит занятие:*\n\n"
        "🆓 *Первое занятие — бесплатно (2 часа):*\n"
        "— 10 мин: знакомство\n"
        "— 1.5 часа: демо-занятие\n"
        "— 30 мин: разбор и план\n\n"
        "📚 *Дальнейшие занятия:*\n"
        "— Индивидуальная программа\n"
        "— 1 час / 1200 руб\n"
        "— Онлайн (Zoom / Discord)\n\n"
        "🎯 Для тех, кто хочет сдать на 4–5 без стресса.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Записаться", callback_data="book")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back")]
        ])
    )

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. Напиши /start чтобы начать заново.")
    return ConversationHandler.END

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(show_slots, pattern="^book$")],
        states={
            CHOOSING_SLOT: [CallbackQueryHandler(slot_selected, pattern="^slot_")],
            ENTERING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, entering_name)],
            ENTERING_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, entering_contact)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_handler))
    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
