import os


def get_token():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("尚未設定 DISCORD_TOKEN，請在執行環境中設定。")
    return token


def get_test_guild_id():
    raw_id = os.getenv("TEST_GUILD_ID", "").strip()
    if not raw_id:
        return None
    if not raw_id.isdigit():
        raise ValueError("TEST_GUILD_ID 必須是 Discord 伺服器的數字 ID")
    return int(raw_id)


DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    "data/bot.sqlite3"
)
