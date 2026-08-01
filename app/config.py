from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    bot_token: str
    channel_id: int
    channel_url: str
    offer_url: str
    first_photo: str
    second_photo: str

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        channel_id = os.getenv("CHANNEL_ID", "").strip()
        channel_url = os.getenv("CHANNEL_URL", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN is required")
        if not channel_id:
            raise RuntimeError("CHANNEL_ID is required")
        if not channel_url:
            raise RuntimeError(
                "CHANNEL_URL is required (invite link with join request enabled)"
            )
        return cls(
            bot_token=token,
            channel_id=int(channel_id),
            channel_url=channel_url,
            offer_url=os.getenv("OFFER_URL", "https://t.me/your_offer_link").strip(),
            first_photo=os.getenv("FIRST_PHOTO", "app/assets/first.jpg").strip(),
            second_photo=os.getenv("SECOND_PHOTO", "app/assets/second.jpg").strip(),
        )
