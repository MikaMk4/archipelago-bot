import json
import os

import discord

from bot.config import load_config
from bot.session_manager import SessionManager

config = load_config()

async def _update_preparation_embed(session_manager: SessionManager):
    if not session_manager.preparation_message:
        return

    player_statuses = session_manager.get_player_status()
    
    status_lines = []
    all_ready = True
    for player_info in player_statuses:
        status_icon = "✅" if player_info['ready'] else "❌"
        status_lines.append(f"{status_icon} {player_info['user'].mention}")
        if not player_info['ready']:
            all_ready = False
            
    embed = discord.Embed(
        title="Archipelago session preparation",
        description=f"Host: {session_manager.host.mention}\n\nUpload your YAML file with `/session upload_yaml`.",
        color=discord.Color.blue()
    )
    embed.add_field(name="Player status", value="\n".join(status_lines), inline=False)
    
    if all_ready:
        embed.add_field(
            name="All players ready!",
            value=f"The host {session_manager.host.mention} can now start the game with `/session start`.",
            inline=False
        )
        embed.color = discord.Color.green()

    try:
        await session_manager.preparation_message.edit(embed=embed)
    except discord.NotFound:
        print("ERROR: Preparation message not found, could not edit.")
        if session_manager.preparation_channel:
            await session_manager.preparation_channel.send("The preparation message was deleted. Session canceled.")

def get_whitelist():
    if not os.path.exists(config['whitelist_path']):
        return []
    with open(config['whitelist_path'], 'r') as f:
        return json.load(f)

def is_whitelisted(user_id: int): return user_id in get_whitelist()