import json

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import load_config
from bot.helper import get_whitelist

config = load_config()

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    admin_group = app_commands.Group(name="admin", description="Manages administrative settings.")

    @admin_group.command(name="whitelist_add", description="Adds a user to the whitelist.")
    async def whitelist_add(self, interaction: discord.Interaction, user: discord.Member):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        whitelist = get_whitelist()
        if user.id in whitelist:
            await interaction.response.send_message(f"{user.mention} is already whitelisted.", ephemeral=True)
            return

        whitelist.append(user.id)
        with open(config['whitelist_path'], 'w') as f:
            json.dump(whitelist, f)

        await interaction.response.send_message(f"{user.mention} has been added to the whitelist.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot), guilds=[discord.Object(id=config['guild_id'])])