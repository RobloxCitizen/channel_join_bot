import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.client.default import DefaultBotProperties

from app.config import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
router = Router()
settings: Settings


def apply_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Подать заявку", callback_data="apply")]]
    )


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        f"Нажмите кнопку, чтобы вступить в «{settings.channel_name}».",
        reply_markup=apply_keyboard(),
    )


@router.callback_query(F.data == "apply")
async def create_application(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.message:
        await callback.answer("Откройте личный чат с ботом.", show_alert=True)
        return

    try:
        invite = await bot.create_chat_invite_link(
            chat_id=settings.channel_id,
            name=f"bot-request-{callback.from_user.id}",
            creates_join_request=True,
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.exception("Could not create an invite link")
        await callback.answer("Не удалось создать приглашение. Попробуйте позже.", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Перейти в канал", url=invite.invite_link)]]
    )
    await callback.message.answer(
        "Готово. Перейдите в канал и подтвердите отправку заявки, бот одобрит её автоматически.",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.chat_join_request()
async def approve_request(request: ChatJoinRequest, bot: Bot) -> None:
    if request.chat.id != settings.channel_id:
        logger.warning("Ignored join request for chat %s", request.chat.id)
        return

    user_id = request.from_user.id
    try:
        await bot.approve_chat_join_request(chat_id=request.chat.id, user_id=user_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.exception("Could not approve join request from user %s", user_id)
        try:
            await bot.send_message(
                chat_id=request.user_chat_id,
                text="Не удалось одобрить заявку автоматически. Попробуйте ещё раз позже.",
            )
        except Exception:
            logger.exception("Could not notify user %s about approval error", user_id)
        return

    text = f"Вы добавлены в канал «{settings.channel_name}»."
    keyboard = None
    if settings.channel_url:
        text += f"\n{settings.channel_url}"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть канал", url=settings.channel_url)]]
        )

    # Пользователь, пришедший через /start, уже разрешил боту писать в ЛС.
    # user_chat_id оставлен запасным вариантом для join request из прямой ссылки.
    for chat_id in dict.fromkeys((user_id, request.user_chat_id)):
        try:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
            logger.info("Approved and notified user %s", user_id)
            return
        except (TelegramBadRequest, TelegramForbiddenError):
            continue

    logger.warning("Approved user %s, but Telegram did not allow a private message", user_id)


async def main() -> None:
    global settings
    settings = Settings.from_env()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
