import os

from dotenv import load_dotenv


def load_config():
    load_dotenv()

    return {
        "discord_token": os.getenv("DISCORD_TOKEN"),
        "guild_id": int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None,
        "server_public_ip": os.getenv("SERVER_PUBLIC_IP"),
        "server_port": 38281,
        "webhost_port": int(os.getenv("WEBHOST_PORT", 8080)),
        "archipelago_path": "/opt/archipelago",
        "data_path": "/app/data",
        "whitelist_path": "/app/data/whitelist.json",
        "upload_path": "/app/data/uploads",
        "games_path": "/app/data/games",
        "patches_path": "/app/data/patches",
    }