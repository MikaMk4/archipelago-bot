import asyncio
import os

import discord
from discord.ext import commands

from bot.config import load_config
from bot.session_manager import SessionManager

config = load_config()

class ArchipelagoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True

        super().__init__(command_prefix="!", intents=intents)

        self.session_manager = SessionManager()

    async def setup_hook(self):
        print("Running setup hook...")

        for filename in os.listdir("./bot/cogs"):
            if filename.endswith(".py"):
                try:
                    await self.load_extension(f"bot.cogs.{filename[:-3]}")
                    print(f"Successfully loaded cog {filename}")
                except Exception as e:
                    print(f"Failed to load cog {filename}: {e}")

        # Sync application commands to guild
        guild = discord.Object(id=config['guild_id'])
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print("Commands synced.")

    async def on_ready(self):

        activity = discord.Activity(
            type=discord.ActivityType.playing,
            name="Archipelago"
        )
        await self.change_presence(activity=activity, status=discord.Status.online)

        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Set status to online and activity to {activity.name}")
        print("-------")

async def main():
    bot = ArchipelagoBot()

    # --- Global Event Listeners ---
    @bot.event
    async def on_interaction(interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.component and interaction.data['custom_id'].startswith("patch_download::"):
            await interaction.response.defer(ephemeral=True, thinking=True)
            
            custom_id = interaction.data['custom_id']
            try:
                filename = custom_id.split('::')[1]
                file_path = os.path.join(config['patches_path'], filename)
                
                if os.path.exists(file_path):
                    await interaction.followup.send(file=discord.File(file_path))
                else:
                    await interaction.followup.send(f"Error: File {filename} not found.", ephemeral=True)
            except Exception as e:
                print(f"Error during patch download interaction: {e}")
                await interaction.followup.send("An error occurred while processing your request.", ephemeral=True)

    await bot.start(config['discord_token'])

if __name__ == '__main__':
    asyncio.run(main())
