import asyncio
import os
import signal

import discord
from discord import app_commands
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

    @bot.tree.command(name="sync", description="Synchronizes commands globally or to a specific guild.")
    @app_commands.describe(guild_id="Optional: The ID of the guild to sync commands for.")
    async def sync(interaction: discord.Interaction, guild_id: str = None):
        """Manually syncs the command tree with Discord."""

        if not await bot.is_owner(interaction.user):
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        try:
            if guild_id:
                guild = discord.Object(id=int(guild_id))
                await bot.tree.sync(guild=guild)
                message = f"Commands successfully synchronized for guild `{guild_id}`!"
                print(f"Commands synced to guild {guild_id}.")
            else:
                await bot.tree.sync()
                message = "Commands successfully synchronized globally!"
                print("Commands synced globally.")

            await interaction.followup.send(message)

        except (ValueError, discord.HTTPException) as e:
            await interaction.followup.send(f"Failed to sync. Is the Guild ID valid? Error: {e}", ephemeral=True)

    # Shutdown handler
    async def shutdown(sig, loop):
        print(f"Received exit signal {sig.name}...")
        print("Closing bot connection and cleaning up...")
        await bot.session_manager.shutdown_gracefully()
        await bot.close()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s, loop)))

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

    try:
        async with bot:
            await bot.start(config['discord_token'])
    except asyncio.CancelledError:
        pass
    finally:
        print("Bot has shut down.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutdown requested by user (Ctrl+C).")
