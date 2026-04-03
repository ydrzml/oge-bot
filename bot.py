import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import requests

# ===================== НАСТРОЙКИ =====================
BOT_TOKEN = "8277052363:AAHJLfUCBmq95wnWZUuyIjQ67-ZSsvK6Yzg"                  # Токен от BotFather
TUTOR_CHAT_ID = "873039793"             # Твой ID из @getmyid_bot
NOTION_TOKEN = "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"             # Токен из Notion Integrations
NOTION_SLOTS_DB_ID = "33799618f8c3803d85b6f3cfdac03d42"       # ID таблицы "Слоты" в Notion
NOTION_STUDENTS_DB_ID = "33799618f8c380ebb393c8bc7d57ac1f"  # ID таблицы "Ученики" в Notion

WELCOME_TEXT = """👋 Привет! Я бот репетитора по информатике (ОГЭ).

Меня зовут *Yadro* — учусь в Центральном университете, сам сдал ОГЭ и ЕГЭ на высокие баллы 🎯

Готовлю к ОГЭ по информатике:
✅ Индивидуальная программа под тебя
✅ Без стресса, в твоём темпе
✅ Первое занятие — бесплатно (2 часа)

💰 Стоимость: 1200 руб/час (онлайн)

Нажми кнопку ниже, чтобы записаться 👇"""

# Порядок дней для сортировки
DAY_ORDER = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}

CHOOSING_SLOT, ENTERING_NAME, ENTERING_CONTACT = range(3)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===================== NOTION API =====================
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_free_slots():
    """Читает свободные слоты из таблицы Слоты в Notion"""
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_SLOTS_DB_ID}/query",
            headers=NOTION_HEADERS,
            json={}
        )
        results = r.json().get("results", [])
        slots = []
        for page in results:
            props = page["properties"]
            day = props.get("День", {}).get("select", {})
            time = props.get("Время", {}).get("rich_text", [])
            if day and time:
                slot_str = f"{day['name']} {time[0]['text']['content']}"
                slots.append(slot_str)
        # Сортируем по дню и времени
        slots.sort(key=lambda s: (
            DAY_ORDER.get(s.split()[0], 9),
            s.split()[1]
        ))
        return slots
    except Exception as e:
        logger.error(f"Ошибка получения слотов: {e}")
        return []

def save_to_notion(name, contact, slot, user_id):
    data = {
        "parent": {"database_id": NOTION_STUDENTS_DB_ID},
        "properties": {
            "Имя": {"title": [{"text": {"content": name}}]},
            "Контакт": {"rich_text": [{"text": {"content": contact}}]},
            "Слот": {"rich_text": [{"text": {"content": slot}}]},
            "Telegram ID": {"rich_text": [{"text": {"content": str(user_id)}}]},
            "Статус": {"select": {"name": "активна"}},
            "Дата записи": {"rich_text": [{"text": {"content": datetime.now().strftime("%d.%m.%Y %H:%M")}}]},
        }
    }
    try:
        r = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=data)
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
            headers=NOTION_HEADERS, json=data
        )
        results = r.json().get("results", [])
        bookings = []
        for page in results:
            slot = page["properties"]["Слот"]["rich_text"]
            if slot:
                bookings.append((slot[0]["text"]["content"], page["id"]))
        return bookings
    except Exception as e:
        logger.error(f"Ошибка получения записей: {e}")
        return []

def cancel_in_notion(page_id):
    try:
        requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS,
            json={"properties": {"Статус": {"select": {"name": "отменена"}}}}
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка отмены: {e}")
        return False

# ===================== ВСПОМОГАТЕЛЬНОЕ =====================
def to_yakutsk(slot):
    """МСК → Якутск (+6 часов)"""
    try:
        day, time = slot.split()
        h, m = map(int, time.split(":"))
        h = (h + 6) % 24
        return f"{day} {h:02d}:{m:02d}"
    except:
        return slot

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

    if data == "book":
        return await show_slots(update, context)
    elif data == "my_bookings":
        return await show_my_bookings(update, context)
    elif data == "how_it_works":
        return await how_it_works(update, context)
    elif data.startswith("slot_"):
        return await slot_selected(update, context)
    elif data.startswith("cancel_"):
        return await cancel_booking(update, context)
    elif data == "back":
        return await back_to_menu(update, context)

async def show_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    slots = get_free_slots()

    if not slots:
        await query.edit_message_text(
            "😔 Сейчас свободных слотов нет. Напиши напрямую — договоримся!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]])
        )
        return ConversationHandler.END

    keyboard = []
    for slot in slots:
        label = f"🕐 {slot} МСК  |  {to_yakutsk(slot)} ЯКТ"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"slot_{slot}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])

    await query.edit_message_text(
        "📅 Выбери удобное время:\n_(МСК = московское, ЯКТ = якутское)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING_SLOT

async def slot_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    slot = query.data.replace("slot_", "")
    context.user_data["selected_slot"] = slot
    await query.edit_message_text(
        f"✅ Выбрано: *{slot}* МСК / *{to_yakutsk(slot)}* ЯКТ\n\nКак тебя зовут? (Имя и фамилия)",
        parse_mode="Markdown"
    )
    return ENTERING_NAME

async def entering_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("📱 Оставь контакт для связи\n(Telegram @username или номер телефона):")
    return ENTERING_CONTACT

async def entering_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.text
    name = context.user_data.get("name")
    slot = context.user_data.get("selected_slot")
    user_id = update.effective_user.id

    save_to_notion(name, contact, slot, user_id)

    try:
        await context.bot.send_message(
            TUTOR_CHAT_ID,
            f"🔔 *Новая запись!*\n\n"
            f"👤 {name}\n📱 {contact}\n"
            f"🕐 {slot} МСК / {to_yakutsk(slot)} ЯКТ\n"
            f"🆔 {user_id}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления: {e}")

    await update.message.reply_text(
        f"🎉 *Запись подтверждена!*\n\n"
        f"📅 *{slot}* МСК / *{to_yakutsk(slot)}* ЯКТ\n\n"
        f"⚠️ Отменить можно за 24+ часа — через кнопку «Мои записи».\n\nЖду тебя! 😊",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

async def show_my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    bookings = get_user_bookings(user_id)

    if not bookings:
        await query.edit_message_text(
            "У тебя нет активных записей.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]])
        )
        return

    keyboard = []
    for slot, page_id in bookings:
        keyboard.append([InlineKeyboardButton(f"❌ Отменить: {slot}", callback_data=f"cancel_{page_id}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])

    await query.edit_message_text(
        "📋 *Твои записи:*\n\n⚠️ Отмена возможна за 24+ часа до занятия.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    page_id = query.data.replace("cancel_", "")
    user_id = update.effective_user.id

    success = cancel_in_notion(page_id)
    if success:
        try:
            await context.bot.send_message(TUTOR_CHAT_ID, f"❌ Ученик отменил запись\nID: {user_id}")
        except:
            pass
        await query.edit_message_text(
            "✅ Запись отменена.",
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
