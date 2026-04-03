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

load_dotenv()

# ===================== НАСТРОЙКИ =====================
BOT_TOKEN             = os.getenv("BOT_TOKEN")
TUTOR_CHAT_ID         = os.getenv("TUTOR_CHAT_ID")
NOTION_TOKEN          = os.getenv("NOTION_TOKEN")
NOTION_SLOTS_DB_ID    = os.getenv("NOTION_SLOTS_DB_ID")
NOTION_STUDENTS_DB_ID = os.getenv("NOTION_STUDENTS_DB_ID")

WELCOME_TEXT = """👋 Привет! Я бот репетитора по информатике (ОГЭ).

Меня зовут *Yadro* — учусь в Центральном университете, сам сдал ОГЭ и ЕГЭ на высокие баллы 🎯

Готовлю к ОГЭ по информатике:
✅ Индивидуальная программа под тебя
✅ Без стресса, в твоём темпе
✅ Первое занятие — *бесплатно* (2 часа)

💰 Обычное занятие: 1200 руб/час (онлайн)

Нажми кнопку ниже, чтобы записаться 👇"""

DAY_ORDER = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}

# Состояния диалога
(CHOOSING_TRIAL_SLOT, CHOOSING_REGULAR_SLOT,
 ENTERING_NAME, ENTERING_CONTACT, BOOKING_TYPE) = range(5)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===================== NOTION =====================
def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

def get_free_slots():
    """Все свободные слоты из Notion, отсортированные"""
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_SLOTS_DB_ID}/query",
            headers=notion_headers(),
            json={"filter": {"property": "Занят", "checkbox": {"equals": False}}}
        )
        results = r.json().get("results", [])
        slots = []
        for page in results:
            props = page["properties"]
            day = props.get("День", {}).get("select", {})
            time_val = props.get("Время", {}).get("rich_text", [])
            if day and time_val:
                slots.append({
                    "label": f"{day['name']} {time_val[0]['text']['content']}",
                    "page_id": page["id"]
                })
        slots.sort(key=lambda s: (DAY_ORDER.get(s["label"].split()[0], 9), s["label"].split()[1]))
        return slots
    except Exception as e:
        logger.error(f"Ошибка получения слотов: {e}")
        return []

def get_trial_slot_pairs(slots):
    """
    Из списка свободных слотов возвращает пары подряд идущих слотов
    в один день (например пн 12:00 + пн 13:00).
    Возвращает список: [{"label": "пн 12:00–13:00", "slot1": {...}, "slot2": {...}}]
    """
    pairs = []
    for i in range(len(slots) - 1):
        s1 = slots[i]
        s2 = slots[i + 1]
        day1, time1 = s1["label"].split()
        day2, time2 = s2["label"].split()
        if day1 != day2:
            continue
        h1, m1 = map(int, time1.split(":"))
        h2, m2 = map(int, time2.split(":"))
        # Проверяем что слоты идут подряд (разница ровно 1 час)
        if h2 * 60 + m2 - h1 * 60 - m1 == 60:
            pairs.append({
                "label": f"{day1} {time1}–{time2}",
                "slot1": s1,
                "slot2": s2,
            })
    return pairs

def has_had_trial(user_id):
    """Проверяет был ли у пользователя пробный урок"""
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_STUDENTS_DB_ID}/query",
            headers=notion_headers(),
            json={
                "filter": {
                    "and": [
                        {"property": "Telegram ID", "rich_text": {"equals": str(user_id)}},
                        {"property": "Тип", "select": {"equals": "пробное"}}
                    ]
                }
            }
        )
        return len(r.json().get("results", [])) > 0
    except Exception as e:
        logger.error(f"Ошибка проверки пробного: {e}")
        return False

def save_student(name, contact, slot_label, slot_page_ids, user_id, lesson_type):
    """slot_page_ids — строка с id через запятую (для пробного — два id)"""
    data = {
        "parent": {"database_id": NOTION_STUDENTS_DB_ID},
        "properties": {
            "Имя": {"title": [{"text": {"content": name}}]},
            "Контакт": {"rich_text": [{"text": {"content": contact}}]},
            "Слот": {"rich_text": [{"text": {"content": slot_label}}]},
            "Slot Page ID": {"rich_text": [{"text": {"content": slot_page_ids}}]},
            "Telegram ID": {"rich_text": [{"text": {"content": str(user_id)}}]},
            "Тип": {"select": {"name": lesson_type}},
            "Дата записи": {"rich_text": [{"text": {"content": datetime.now().strftime("%d.%m.%Y %H:%M")}}]},
        }
    }
    try:
        r = requests.post("https://api.notion.com/v1/pages", headers=notion_headers(), json=data)
        resp = r.json()
        logger.info(f"Notion ответ: статус={r.status_code} тело={resp}")
        if r.status_code != 200:
            logger.error(f"Notion ошибка сохранения: {resp}")
        return resp.get("id")
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        return None

def mark_slots_busy(slot_page_ids_str):
    for pid in slot_page_ids_str.split(","):
        pid = pid.strip()
        if pid:
            try:
                requests.patch(
                    f"https://api.notion.com/v1/pages/{pid}",
                    headers=notion_headers(),
                    json={"properties": {"Занят": {"checkbox": True}}}
                )
            except Exception as e:
                logger.error(f"Ошибка пометки слота {pid}: {e}")

def mark_slots_free(slot_page_ids_str):
    for pid in slot_page_ids_str.split(","):
        pid = pid.strip()
        if pid:
            try:
                requests.patch(
                    f"https://api.notion.com/v1/pages/{pid}",
                    headers=notion_headers(),
                    json={"properties": {"Занят": {"checkbox": False}}}
                )
            except Exception as e:
                logger.error(f"Ошибка освобождения слота {pid}: {e}")

def get_user_bookings(user_id):
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_STUDENTS_DB_ID}/query",
            headers=notion_headers(),
            json={
                "filter": {
                    "property": "Telegram ID", "rich_text": {"equals": str(user_id)}
                }
            }
        )
        results = r.json().get("results", [])
        bookings = []
        for page in results:
            slot = page["properties"]["Слот"]["rich_text"]
            slot_pids = page["properties"].get("Slot Page ID", {}).get("rich_text", [])
            lesson_type = page["properties"].get("Тип", {}).get("select", {})
            if slot:
                bookings.append({
                    "slot_label": slot[0]["text"]["content"],
                    "slot_page_ids": slot_pids[0]["text"]["content"] if slot_pids else "",
                    "page_id": page["id"],
                    "type": lesson_type.get("name", "обычное") if lesson_type else "обычное"
                })
        return bookings
    except Exception as e:
        logger.error(f"Ошибка получения записей: {e}")
        return []

def cancel_booking_notion(page_id, slot_page_ids):
    try:
        # Архивируем запись в Notion (удаляем страницу)
        requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=notion_headers(),
            json={"archived": True}
        )
        if slot_page_ids:
            mark_slots_free(slot_page_ids)
        return True
    except Exception as e:
        logger.error(f"Ошибка отмены: {e}")
        return False

# ===================== ВСПОМОГАТЕЛЬНОЕ =====================
def to_yakutsk(slot_label):
    """Конвертирует МСК в якутское время (+6). Поддерживает формат 'пн 12:00' и 'пн 12:00–13:00'"""
    try:
        if "–" in slot_label:
            day, times = slot_label.split(" ", 1)
            t1, t2 = times.split("–")
            h1, m1 = map(int, t1.split(":"))
            h2, m2 = map(int, t2.split(":"))
            return f"{day} {(h1+6)%24:02d}:{m1:02d}–{(h2+6)%24:02d}:{m2:02d}"
        else:
            day, time = slot_label.split()
            h, m = map(int, time.split(":"))
            return f"{day} {(h+6)%24:02d}:{m:02d}"
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
    # Удаляем сообщение пользователя /start
    try:
        await update.message.delete()
    except:
        pass
    # Удаляем предыдущее сообщение бота если есть
    if context.user_data.get("bot_message_id"):
        try:
            await context.bot.delete_message(update.effective_chat.id, context.user_data["bot_message_id"])
        except:
            pass
    msg = await context.bot.send_message(
        update.effective_chat.id, WELCOME_TEXT,
        parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )
    context.user_data["bot_message_id"] = msg.message_id

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "book":                      return await show_booking(update, context)
    elif data == "my_bookings":             return await show_my_bookings(update, context)
    elif data == "how_it_works":            return await how_it_works(update, context)
    elif data.startswith("trial_"):         return await trial_slot_selected(update, context)
    elif data.startswith("regular_"):       return await regular_slot_selected(update, context)
    elif data.startswith("cancel_"):        return await do_cancel_booking(update, context)
    elif data == "book_regular":            return await show_regular_slots(update, context)
    elif data == "skip_regular":            return await back_to_menu(update, context)
    elif data == "back":                    return await back_to_menu(update, context)

async def show_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Определяет тип записи — пробное или обычное"""
    query = update.callback_query
    user_id = update.effective_user.id

    if has_had_trial(user_id):
        # Уже был на пробном — показываем обычные слоты
        return await show_regular_slots(update, context)
    else:
        # Новый ученик — показываем пары слотов для пробного
        return await show_trial_slots(update, context)

async def show_trial_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    slots = get_free_slots()
    pairs = get_trial_slot_pairs(slots)

    if not pairs:
        await query.edit_message_text(
            "😔 Сейчас нет доступных окон для пробного занятия (2 часа подряд).\nНапиши напрямую — договоримся!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]])
        )
        return ConversationHandler.END

    keyboard = []
    for i, pair in enumerate(pairs):
        ykt = to_yakutsk(pair["label"])
        label = f"🆓 {pair['label']} МСК  |  {ykt} ЯКТ"
        # Кодируем индекс пары в callback
        keyboard.append([InlineKeyboardButton(label, callback_data=f"trial_{i}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])

    # Сохраняем пары в user_data для доступа при выборе
    context.user_data["trial_pairs"] = pairs

    await query.edit_message_text(
        "🆓 *Первое занятие бесплатно!*\n\nВыбери удобное время (2 часа подряд):\n_(МСК = московское, ЯКТ = якутское)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING_TRIAL_SLOT

async def trial_slot_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    idx = int(query.data.replace("trial_", ""))
    pairs = context.user_data.get("trial_pairs", [])

    if idx >= len(pairs):
        await query.edit_message_text("Что-то пошло не так. Попробуй заново /start")
        return ConversationHandler.END

    pair = pairs[idx]
    context.user_data["slot_label"] = pair["label"]
    context.user_data["slot_page_ids"] = f"{pair['slot1']['page_id']},{pair['slot2']['page_id']}"
    context.user_data["lesson_type"] = "пробное"

    await query.edit_message_text(
        f"✅ Выбрано: *{pair['label']}* МСК / *{to_yakutsk(pair['label'])}* ЯКТ\n\n"
        f"Как тебя зовут? (Имя и фамилия)",
        parse_mode="Markdown"
    )
    return ENTERING_NAME

async def show_regular_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        keyboard.append([InlineKeyboardButton(label, callback_data=f"regular_{s['page_id']}__{s['label']}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])

    await query.edit_message_text(
        "📅 Выбери время для занятия:\n_(МСК = московское, ЯКТ = якутское)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING_REGULAR_SLOT

async def regular_slot_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    raw = query.data[len("regular_"):]
    parts = raw.split("__")
    slot_page_id = parts[0]
    slot_label = parts[1] if len(parts) > 1 else raw

    context.user_data["slot_label"] = slot_label
    context.user_data["slot_page_ids"] = slot_page_id
    context.user_data["lesson_type"] = "обычное"

    # Если имя уже есть (записываем второй раз после пробного) — пропускаем ввод имени
    if context.user_data.get("name"):
        return await finalize_booking(update, context, via_query=True)

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
    context.user_data["contact"] = update.message.text
    return await finalize_booking(update, context, via_query=False)

async def finalize_booking(update, context, via_query=False):
    """Финализирует запись и отправляет подтверждение"""
    name = context.user_data["name"]
    contact = context.user_data.get("contact", "—")
    slot_label = context.user_data["slot_label"]
    slot_page_ids = context.user_data["slot_page_ids"]
    lesson_type = context.user_data.get("lesson_type", "обычное")
    user_id = update.effective_user.id

    save_student(name, contact, slot_label, slot_page_ids, user_id, lesson_type)
    mark_slots_busy(slot_page_ids)

    type_emoji = "🆓" if lesson_type == "пробное" else "📚"
    type_label = "Пробное (бесплатно)" if lesson_type == "пробное" else "Обычное (1200 руб)"

    try:
        await context.bot.send_message(
            TUTOR_CHAT_ID,
            f"🔔 *Новая запись!*\n\n"
            f"👤 {name}\n📱 {contact}\n"
            f"{type_emoji} {type_label}\n"
            f"🕐 {slot_label} МСК / {to_yakutsk(slot_label)} ЯКТ",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления: {e}")

    confirm_text = (
        f"🎉 *Запись подтверждена!*\n\n"
        f"{type_emoji} *{type_label}*\n"
        f"📅 *{slot_label}* МСК / *{to_yakutsk(slot_label)}* ЯКТ\n\n"
        f"⚠️ Отменить можно за 24+ часа — через кнопку «Мои записи».\n\nЖду тебя! 😊"
    )

    # После пробного — предлагаем записаться на обычное
    if lesson_type == "пробное":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Записаться на обычное занятие", callback_data="book_regular")],
            [InlineKeyboardButton("🏠 В главное меню", callback_data="skip_regular")],
        ])
        confirm_text += "\n\n_Хочешь сразу записаться на следующее занятие?_"
    else:
        keyboard = main_menu_keyboard()
        context.user_data.clear()

    if via_query:
        await update.callback_query.edit_message_text(confirm_text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(confirm_text, parse_mode="Markdown", reply_markup=keyboard)

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
        type_icon = "🆓" if b["type"] == "пробное" else "📚"
        cb = f"cancel_{b['page_id']}__{b['slot_page_ids']}__{b['slot_label']}"
        keyboard.append([InlineKeyboardButton(
            f"❌ {type_icon} {b['slot_label']}", callback_data=cb
        )])
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
    slot_page_ids = parts[1] if len(parts) > 1 else ""
    slot_label = parts[2] if len(parts) > 2 else ""

    success = cancel_booking_notion(page_id, slot_page_ids)
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
        "🆓 *Пробное — бесплатно (2 часа):*\n"
        "— 10 мин: знакомство\n"
        "— 1.5 часа: демо-занятие\n"
        "— 30 мин: разбор и план\n\n"
        "📚 *Обычное занятие:*\n"
        "— 1 час / 1200 руб\n"
        "— Индивидуальная программа\n"
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

# ===================== ЗАПУСК =====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(show_booking, pattern="^book$"),
            CallbackQueryHandler(show_regular_slots, pattern="^book_regular$"),
        ],
        states={
            CHOOSING_TRIAL_SLOT:   [CallbackQueryHandler(trial_slot_selected, pattern="^trial_")],
            CHOOSING_REGULAR_SLOT: [CallbackQueryHandler(regular_slot_selected, pattern="^regular_")],
            ENTERING_NAME:         [MessageHandler(filters.TEXT & ~filters.COMMAND, entering_name)],
            ENTERING_CONTACT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, entering_contact)],
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
