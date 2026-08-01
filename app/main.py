import asyncio
import logging
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import (
    ChatJoinRequest,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.config import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
router = Router()
settings: Settings
DB_PATH = "data/bot.db"


# ---------------------------------------------------------------------------
# SQLite: храним только "долги" бота — заявки, которые не удалось одобрить,
# и приветствия, которые не удалось доставить. При /start бот их закрывает.
# ---------------------------------------------------------------------------

def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS pending_approvals "
            "(user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS pending_welcomes "
            "(user_id INTEGER PRIMARY KEY)"
        )


def save_pending_approval(user_id: int, chat_id: int) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO pending_approvals(user_id, chat_id) VALUES (?, ?)",
            (user_id, chat_id),
        )


def pop_pending_approval(user_id: int) -> int | None:
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            "SELECT chat_id FROM pending_approvals WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row:
            connection.execute(
                "DELETE FROM pending_approvals WHERE user_id = ?", (user_id,)
            )
    return row[0] if row else None


def save_pending_welcome(user_id: int) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO pending_welcomes(user_id) VALUES (?)", (user_id,)
        )


def pop_pending_welcome(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            "SELECT user_id FROM pending_welcomes WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row:
            connection.execute(
                "DELETE FROM pending_welcomes WHERE user_id = ?", (user_id,)
            )
    return row is not None


# ---------------------------------------------------------------------------
# Тексты
# ---------------------------------------------------------------------------

FIRST_TEXT = """💸 <b>Хочешь получить доступ к закрытому каналу?</b>

Жми кнопку 👉 <b>«Подать заявку»</b> ниже.

После этого:

🔓 Бот мгновенно одобрит твою заявку в канал — без ожидания и ручной модерации.

🎥 Сразу пришлёт сообщение с подтверждением и ссылкой на канал.

⚡ Всё максимально просто: подай заявку и заходи.

<span class="tg-spoiler">Нажимая кнопку, ты соглашаешься на получение сообщений от бота.</span>"""

SECOND_TEXT = """🔥 <b>Добро пожаловать!</b>

Твоя заявка успешно одобрена, ты добавлен в канал ✅

Коротко о главном:

💬 [Здесь короткое описание канала: чем он полезен и что пользователь найдёт внутри]

⚠️ Остерегайтесь фейков и мошенников.

ТГ менеджеров: @your_manager_1, @your_manager_2

❗️Мы никогда не пишем первыми. Если вам написал человек от имени проекта, обязательно проверьте его username через менеджеров или администрацию канала.

👇 Переходи в основной канал:

🔗 Ссылка на канал: <a href="{channel_url}">ТУТ</a>

<a href="{offer_url}">Наша оферта</a>"""

ALREADY_MEMBER_TEXT = (
    "✅ Ты уже участник канала.\n\n"
    '🔗 Ссылка на канал: <a href="{channel_url}">ТУТ</a>'
)


# ---------------------------------------------------------------------------
# Вспомогательные функции отправки
# ---------------------------------------------------------------------------

def photo(path: str) -> FSInputFile:
    if not Path(path).is_file():
        raise RuntimeError(f"Photo not found: {path}")
    return FSInputFile(path)


def apply_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📩 Подать заявку", url=settings.channel_url)]
        ]
    )


async def send_photo_with_text(
    bot: Bot,
    chat_id: int,
    image_path: str,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    # У подписи к фото лимит 1024 символа. Если текст длиннее, фото всё равно
    # отправляется как изображение, а полный текст приходит сразу следом.
    if len(text) <= 1024:
        await bot.send_photo(
            chat_id=chat_id,
            photo=photo(image_path),
            caption=text,
            reply_markup=reply_markup,
        )
    else:
        await bot.send_photo(chat_id=chat_id, photo=photo(image_path))
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )


async def send_welcome(bot: Bot, chat_id: int) -> None:
    text = SECOND_TEXT.format(
        channel_url=settings.channel_url,
        offer_url=settings.offer_url,
    )
    await send_photo_with_text(bot, chat_id, settings.second_photo, text)


async def is_channel_member(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(settings.channel_id, user_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    return member.status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.RESTRICTED,
    }


# ---------------------------------------------------------------------------
# ГЛАВНЫЙ сценарий (по ТЗ): заявка -> мгновенное одобрение -> ЛС первым.
# Telegram разрешает боту писать пользователю, отправившему заявку в канал,
# где бот является администратором, поэтому /start заранее не требуется.
# ---------------------------------------------------------------------------

@router.chat_join_request()
async def on_join_request(request: ChatJoinRequest, bot: Bot) -> None:
    if request.chat.id != settings.channel_id:
        return

    user_id = request.from_user.id

    # Шаг 1. Действие: мгновенно одобряем заявку.
    try:
        await request.approve()
        logger.info("Approved join request from user %s", user_id)
    except TelegramBadRequest as error:
        # USER_ALREADY_PARTICIPANT / HIDE_REQUESTER_MISSING — заявка уже закрыта.
        logger.warning("Approve failed for user %s: %s", user_id, error)
        if "USER_ALREADY_PARTICIPANT" not in str(error):
            # Не хватило прав и т.п. — запомним и попробуем ещё раз при /start.
            save_pending_approval(user_id, request.chat.id)
            return
    except TelegramForbiddenError:
        logger.exception("Bot has no rights to approve requests in the channel")
        save_pending_approval(user_id, request.chat.id)
        return

    # Шаг 2. Обратная связь: сразу пишем пользователю в ЛС первыми.
    try:
        await send_welcome(bot, user_id)
        logger.info("Sent approval confirmation to user %s", user_id)
    except (TelegramForbiddenError, TelegramBadRequest):
        # Пользователь запретил ЛС — доставим приветствие, когда он нажмёт /start.
        logger.warning("Could not DM user %s, welcome deferred to /start", user_id)
        save_pending_welcome(user_id)


# ---------------------------------------------------------------------------
# /start — всегда отвечает, никогда не молчит.
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def start(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id

    # Есть неодобренная заявка (ранее не удалось одобрить) — одобряем сейчас.
    pending_chat_id = pop_pending_approval(user_id)
    if pending_chat_id == settings.channel_id:
        try:
            await bot.approve_chat_join_request(chat_id=pending_chat_id, user_id=user_id)
            await send_welcome(bot, message.chat.id)
            return
        except (TelegramBadRequest, TelegramForbiddenError):
            logger.exception("Could not approve join request from user %s", user_id)

    # Приветствие не было доставлено при одобрении — отправляем его сейчас.
    if pop_pending_welcome(user_id):
        await send_welcome(bot, message.chat.id)
        return

    # Пользователь уже в канале — напоминаем ссылку, а не молчим.
    if await is_channel_member(bot, user_id):
        await message.answer(
            ALREADY_MEMBER_TEXT.format(channel_url=settings.channel_url),
            disable_web_page_preview=True,
        )
        return

    # Новый пользователь — первое сообщение с кнопкой «Подать заявку».
    await send_photo_with_text(
        bot,
        message.chat.id,
        settings.first_photo,
        FIRST_TEXT,
        reply_markup=apply_keyboard(),
    )


async def main() -> None:
    global settings
    settings = Settings.from_env()
    init_db()
    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    logger.info("Bot started, polling for updates")
    await dispatcher.start_polling(
        bot,
        allowed_updates=dispatcher.resolve_used_update_types(),
    )


if __name__ == "__main__":
    asyncio.run(main())
