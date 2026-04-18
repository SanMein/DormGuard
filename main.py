import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# Включаем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Путь к файлу базы данных SQLite
DB_FILE = 'duty_schedule.db'

# Загружаем настройки из .env
from dotenv import load_dotenv

load_dotenv()

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 5069780438))
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", -1003035362218))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не найден в .env")
    exit(1)


# --- Функции работы с базой данных ---
def init_db():
    """Создаёт таблицы и заполняет их новыми данными по актуальному расписанию."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Таблица дежурств
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS duty_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            days TEXT NOT NULL
        )
    ''')

    # Таблица заметок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL
        )
    ''')

    # Очищаем старую таблицу дежурств и вставляем актуальные данные
    cursor.execute("DELETE FROM duty_schedule")

    schedule_data = [
        ('Кутелёв Константин', '1,5,9,13,17,21,25,29,31'),
        ('Пушкарский Никита', '2,6,10,14,18,22,29,31'),
        ('Мироненко Арсений', '3,7,11,15,19,23,30,31'),
        ('Хамраев Мухаммад', '4,8,12,16,20,24,28,30,31')
    ]
    cursor.executemany("INSERT INTO duty_schedule (name, days) VALUES (?, ?)", schedule_data)

    logger.info("Таблица duty_schedule очищена и заполнена новым расписанием (4 человека).")

    conn.commit()
    conn.close()


def get_duty_for_today():
    """Возвращает имя дежурного на сегодняшнее число согласно обновлённому расписанию."""
    today_day = str(datetime.now().day)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM duty_schedule WHERE days LIKE ?", (f'%{today_day}%',))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "Неизвестно (проверьте расписание)"


def get_all_note_keys():
    """Возвращает список всех ключей заметок."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT key FROM notes ORDER BY key")
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


def set_note_content(note_key, content):
    """Сохраняет или обновляет содержимое заметки по ключу."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO notes (key, content) VALUES (?, ?)", (note_key, content))
    conn.commit()
    conn.close()


def delete_note(note_key):
    """Удаляет заметку по ключу."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notes WHERE key = ?", (note_key,))
    changes = cursor.rowcount
    conn.commit()
    conn.close()
    return changes > 0


# --- Функции для планировщика задач ---
async def send_wake_up(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение 'Подъём' в 07:00 (время в UTC)."""
    job = context.job
    chat_id = job.data['chat_id']
    current_duty = get_duty_for_today()
    date_str = datetime.now().strftime("%d.%m.%y")
    message_text = f"Подъём. {date_str} - дежурный: {current_duty}."

    sent_message = await context.bot.send_message(chat_id=chat_id, text=message_text)
    job.data['last_message_id'] = sent_message.id


async def send_gather_up(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение 'Собираемся к первой паре' в 07:25 и удаляет предыдущее."""
    job = context.job
    chat_id = job.data['chat_id']

    if 'last_message_id' in job.data:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=job.data['last_message_id'])
        except Exception as e:
            logger.warning(f"Не удалось удалить предыдущее сообщение: {e}")

    current_duty = get_duty_for_today()
    date_str = datetime.now().strftime("%d.%m.%y")
    message_text = f"Собираемся к первой паре. {date_str} - дежурный: {current_duty}."

    sent_message = await context.bot.send_message(chat_id=chat_id, text=message_text)
    job.data['last_message_id'] = sent_message.id


async def send_final_and_pin(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет финальное сообщение в 08:30, удаляет предыдущее и закрепляет новое."""
    job = context.job
    chat_id = job.data['chat_id']

    if 'last_message_id' in job.data:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=job.data['last_message_id'])
        except Exception as e:
            logger.warning(f"Не удалось удалить предыдущее сообщение: {e}")

    current_duty = get_duty_for_today()
    date_str = datetime.now().strftime("%d.%m.%y")
    message_text = f"{date_str} - дежурный: {current_duty}."

    sent_message = await context.bot.send_message(chat_id=chat_id, text=message_text)
    try:
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=sent_message.id, disable_notification=True)
    except Exception as e:
        logger.error(f"Не удалось закрепить сообщение: {e}")


async def delete_notification(context: ContextTypes.DEFAULT_TYPE):
    """Удаляет сообщение через 1 час."""
    chat_id, message_id = context.job.data
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Не удалось удалить уведомление {message_id} в {chat_id}: {e}")


# --- Вспомогательная функция для удаления сообщения ---
async def delete_message_safely(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """Пытается удалить сообщение, логирует ошибки."""
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {message_id} в чате {chat_id}: {e}")


# --- Обработчики команд ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_message_safely(context, update.effective_message.chat_id, update.effective_message.message_id)
    await context.bot.send_message(
        chat_id=update.effective_message.chat_id,
        text="DormGuard: бот для комнаты в общежитии.\nИспользуй /help для списка команд."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_message_safely(context, update.effective_message.chat_id, update.effective_message.message_id)

    chat_id = update.effective_message.chat_id
    user_id = update.effective_user.id

    is_private_chat = chat_id == user_id
    is_group_chat = chat_id == GROUP_CHAT_ID

    if is_private_chat and user_id == ADMIN_USER_ID:
        help_text = (
            "Доступные команды (личные сообщения):\n"
            "/start - Начало работы\n"
            "/help - Список команд\n"
            "/duty - Кто дежурный сегодня\n"
            "/duty_list - Расписание дежурств\n"
            "/notife <тип> <текст> - Отправить уведомление/объявление в группу\n"
            "/note_add <ключ> <текст> - Добавить/обновить заметку\n"
            "/note_update <ключ> <текст> - Обновить заметку\n"
            "/note_delete <ключ> - Удалить заметку\n"
            "/note_list - Список ключей заметок\n"
            "/note <ключ> - Получить заметку (доступно всем)"
        )
    else:
        help_text = (
            "Доступные команды:\n"
            "/start - Начало работы\n"
            "/help - Список команд\n"
            "/duty - Кто дежурный сегодня\n"
            "/duty_list - Расписание дежурств\n"
            "/note <ключ> - Получить заметку"
        )

    await context.bot.send_message(chat_id=chat_id, text=help_text)


async def duty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_message_safely(context, update.effective_message.chat_id, update.effective_message.message_id)
    current_duty = get_duty_for_today()
    await context.bot.send_message(
        chat_id=update.effective_message.chat_id,
        text=f"Сегодня дежурный: {current_duty}"
    )


async def duty_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_message_safely(context, update.effective_message.chat_id, update.effective_message.message_id)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT name, days FROM duty_schedule ORDER BY name")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        response_text = "Расписание дежурств пусто."
    else:
        list_str = "\n".join([f"{name}: дни {days}" for name, days in rows])
        response_text = f"Расписание дежурств:\n{list_str}"

    await context.bot.send_message(chat_id=update.effective_message.chat_id, text=response_text)


async def notife(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_message.chat_id

    if chat_id != user_id or user_id != ADMIN_USER_ID:
        await delete_message_safely(context, chat_id, update.effective_message.message_id)
        await context.bot.send_message(chat_id=chat_id,
                                       text="❌ Эта команда доступна только администратору в личных сообщениях.")
        return

    if len(context.args) < 2:
        await delete_message_safely(context, chat_id, update.effective_message.message_id)
        await context.bot.send_message(chat_id=chat_id,
                                       text="❌ Использование: /notife <тип> <текст>\nТип: уведомление или объявление")
        return

    await delete_message_safely(context, chat_id, update.effective_message.message_id)

    type_arg = context.args[0].lower()
    message_text = " ".join(context.args[1:])

    if type_arg == "уведомление":
        emoji = "📘"
    elif type_arg == "объявление":
        emoji = "📕"
    else:
        await context.bot.send_message(chat_id=chat_id,
                                       text="❌ Неверный тип. Используйте 'уведомление' или 'объявление'.")
        return

    full_message = f"{emoji} {message_text}"

    try:
        sent_message = await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=full_message)
        context.job_queue.run_once(
            delete_notification,
            when=timedelta(hours=1),
            data=(GROUP_CHAT_ID, sent_message.id)
        )
        await context.bot.send_message(chat_id=chat_id,
                                       text="✅ Уведомление отправлено в группу и будет удалено через 1 час.")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка при отправке: {e}")


# Команды работы с заметками (только админ в личке)
async def note_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_message.chat_id

    if chat_id != user_id or user_id != ADMIN_USER_ID:
        await delete_message_safely(context, chat_id, update.effective_message.message_id)
        await context.bot.send_message(chat_id=chat_id,
                                       text="❌ Эта команда доступна только администратору в личных сообщениях.")
        return

    if len(context.args) < 2:
        await delete_message_safely(context, chat_id, update.effective_message.message_id)
        await context.bot.send_message(chat_id=chat_id, text="❌ Использование: /note_add <ключ> <текст>")
        return

    await delete_message_safely(context, chat_id, update.effective_message.message_id)

    note_key = context.args[0].lower()
    note_content = " ".join(context.args[1:])

    set_note_content(note_key, note_content)
    await context.bot.send_message(chat_id=chat_id, text=f"✅ Заметка '{note_key}' добавлена/обновлена.")


async def note_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_message.chat_id

    if chat_id != user_id or user_id != ADMIN_USER_ID:
        await delete_message_safely(context, chat_id, update.effective_message.message_id)
        await context.bot.send_message(chat_id=chat_id,
                                       text="❌ Эта команда доступна только администратору в личных сообщениях.")
        return

    if len(context.args) < 2:
        await delete_message_safely(context, chat_id, update.effective_message.message_id)
        await context.bot.send_message(chat_id=chat_id, text="❌ Использование: /note_update <ключ> <текст>")
        return

    await delete_message_safely(context, chat_id, update.effective_message.message_id)

    note_key = context.args[0].lower()
    new_content = " ".join(context.args[1:])

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM notes WHERE key = ?", (note_key,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        await context.bot.send_message(chat_id=chat_id,
                                       text=f"⚠️ Заметка '{note_key}' не найдена. Используйте /note_add.")
        return

    set_note_content(note_key, new_content)
    await context.bot.send_message(chat_id=chat_id, text=f"✅ Заметка '{note_key}' обновлена.")


async def note_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_message.chat_id

    if chat_id != user_id or user_id != ADMIN_USER_ID:
        await delete_message_safely(context, chat_id, update.effective_message.message_id)
        await context.bot.send_message(chat_id=chat_id,
                                       text="❌ Эта команда доступна только администратору в личных сообщениях.")
        return

    if len(context.args) < 1:
        await delete_message_safely(context, chat_id, update.effective_message.message_id)
        await context.bot.send_message(chat_id=chat_id, text="❌ Использование: /note_delete <ключ>")
        return

    await delete_message_safely(context, chat_id, update.effective_message.message_id)

    note_key = context.args[0].lower()
    deleted = delete_note(note_key)

    if deleted:
        await context.bot.send_message(chat_id=chat_id, text=f"✅ Заметка '{note_key}' удалена.")
    else:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Заметка '{note_key}' не найдена.")


async def note_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_message.chat_id

    if chat_id != user_id or user_id != ADMIN_USER_ID:
        await delete_message_safely(context, chat_id, update.effective_message.message_id)
        await context.bot.send_message(chat_id=chat_id,
                                       text="❌ Эта команда доступна только администратору в личных сообщениях.")
        return

    await delete_message_safely(context, chat_id, update.effective_message.message_id)

    keys = get_all_note_keys()
    if keys:
        list_str = "\n".join(keys)
        await context.bot.send_message(chat_id=chat_id, text=f"Список ключей заметок:\n{list_str}")
    else:
        await context.bot.send_message(chat_id=chat_id, text="Список заметок пуст.")


async def note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await delete_message_safely(context, update.effective_message.chat_id, update.effective_message.message_id)
        await context.bot.send_message(chat_id=update.effective_message.chat_id, text="❌ Использование: /note <ключ>")
        return

    await delete_message_safely(context, update.effective_message.chat_id, update.effective_message.message_id)

    note_key = context.args[0].lower()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM notes WHERE key = ?", (note_key,))
    result = cursor.fetchone()
    conn.close()

    if result:
        await context.bot.send_message(chat_id=update.effective_message.chat_id, text=result[0])
    else:
        await context.bot.send_message(chat_id=update.effective_message.chat_id,
                                       text=f"⚠️ Заметка '{note_key}' не найдена.")


# --- Запуск бота ---
def main():
    init_db()

    application = Application.builder().token(TOKEN).build()

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("duty", duty))
    application.add_handler(CommandHandler("duty_list", duty_list))
    application.add_handler(CommandHandler("notife", notife))
    application.add_handler(CommandHandler("note_add", note_add))
    application.add_handler(CommandHandler("note_update", note_update))
    application.add_handler(CommandHandler("note_delete", note_delete))
    application.add_handler(CommandHandler("note_list", note_list))
    application.add_handler(CommandHandler("note", note))

    # Настройка планировщика (время указано в UTC!)
    job_queue = application.job_queue

    if job_queue and GROUP_CHAT_ID != -1003035362218:
        logger.warning("GROUP_CHAT_ID отличается от дефолтного. Планировщик будет запущен.")

    job_queue.run_daily(send_wake_up, time=datetime.strptime("07:00", "%H:%M").time(), data={'chat_id': GROUP_CHAT_ID})
    job_queue.run_daily(send_gather_up, time=datetime.strptime("07:25", "%H:%M").time(),
                        data={'chat_id': GROUP_CHAT_ID})
    job_queue.run_daily(send_final_and_pin, time=datetime.strptime("08:30", "%H:%M").time(),
                        data={'chat_id': GROUP_CHAT_ID})

    logger.info("Бот запущен. Расписание дежурств обновлено на 4 человека.")
    application.run_polling()


if __name__ == '__main__':
    main()