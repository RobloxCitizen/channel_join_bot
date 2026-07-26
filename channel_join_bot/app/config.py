from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    bot_token: str
    channel_id: int
    channel_name: str
    channel_url: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        channel_id = os.getenv("CHANNEL_ID", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN is required")
        if not channel_id:
            raise RuntimeError("CHANNEL_ID is required")
        return cls(
            bot_token=token,
            channel_id=int(channel_id),
            channel_name=os.getenv("CHANNEL_NAME", "Закрытый канал").strip(),
            channel_url=os.getenv("CHANNEL_URL", "").strip() or None,
        )
