import os
from typing import Literal

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from bot.config import load_config
from bot.helper import _update_preparation_embed, is_whitelisted
from bot.session_manager import SessionManager

config = load_config()

class SessionCog(commands.Cog):
    def __init__(self, bot: commands.Bot, session_manager: SessionManager):
        self.bot = bot
        self.session_manager = session_manager

    session_group = app_commands.Group(name="session", description="Manages an Archipelago session.")

    @session_group.command(name="create", description="Starts the preparation for a new session.")
    async def create(self, interaction: discord.Interaction):
        if not is_whitelisted(interaction.user.id) and not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return
        
        await interaction.response.defer()

        anchor_message = interaction.original_response()

        success, message = await self.session_manager.create_session(interaction.user, anchor_message)

        if not success:
            await interaction.followup.send(message, ephemeral=True)
            return

        # Create and send the initial status message
        await interaction.followup.send("Session preparation started. Players can now upload their YAML files.")
        self.session_manager.anchor_message = anchor_message
        await _update_preparation_embed(self.session_manager)

    @session_group.command(name="add_player", description="Add a player to the current session.")
    async def add_player(self, interaction: discord.Interaction, new_player: discord.Member):
        await interaction.response.defer(ephemeral=True)

        if self.session_manager.state != "preparing":
            await interaction.followup.send("No session is being prepared right now.", ephemeral=True)
            return

        if interaction.user.id != self.session_manager.host.id:
            await interaction.followup.send("Only the host can add players.", ephemeral=True)
            return

        success, message = self.session_manager.add_player(new_player)

        if success:
            await _update_preparation_embed(self.session_manager)
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)

    @session_group.command(name="upload_yaml", description="Upload your YAML file for the session.")
    @app_commands.describe(yaml_file="The YAML file to upload.")
    async def upload_yaml(self, interaction: discord.Interaction, yaml_file: discord.Attachment):
        await interaction.response.defer()

        if not self.session_manager.is_active() or self.session_manager.state != "preparing":
            await interaction.followup.send("There is currently no session preparing.", ephemeral=True)
            return

        if not yaml_file.filename.lower().endswith('.yaml'):
            await interaction.followup.send("Please upload a valid .yaml file.", ephemeral=True)
            return

        # Read the file content and parse it to get the player name
        try:
            yaml_content_bytes = await yaml_file.read()
            yaml_data = yaml.safe_load(yaml_content_bytes)
            player_name = yaml_data.get('name')
            if not player_name:
                await interaction.followup.send("Your YAML file needs a `name` entry.", ephemeral=True)
                return
        except Exception as e:
            await interaction.followup.send(f"Error when reading YAML: {e}", ephemeral=True)
            return
            
        # Check if the uploader's name matches a player in the session
        if player_name not in self.session_manager.players:
            await interaction.followup.send(f"Your slot name {player_name} in the YAML file is not part of the session.", ephemeral=True)
            return
        
        upload_path = os.path.join(config['upload_path'], f"{player_name}.yaml")
        await yaml_file.save(upload_path)

        # Mark the player as ready
        self.session_manager.set_player_ready(player_name)
        await _update_preparation_embed(self.session_manager)
        await interaction.followup.send(f"File for '{player_name}' uploaded successfully.", ephemeral=True)

    @session_group.command(name="start", description="Starts the game generation and server.")
    async def start(
            self,
            interaction: discord.Interaction, 
            password: str = None,
            release_mode: Literal['auto', 'enabled', 'disabled', 'goal', 'auto-enabled'] = 'auto',
            collect_mode: Literal['auto', 'enabled', 'disabled', 'goal', 'auto-enabled'] = 'auto',
            remaining_mode: Literal['enabled', 'disabled', 'goal'] = 'goal'
        ):
        await interaction.response.defer()

        if not self.session_manager.is_active() or self.session_manager.state != "preparing":
            await interaction.followup.send("There is no session that could be started.", ephemeral=True)
            return
        if interaction.user.id != self.session_manager.host.id:
            await interaction.followup.send("Only the host can start the session.", ephemeral=True)
            return

        # Check if all players are ready
        if not all(p['ready'] for p in self.session_manager.get_player_status()):
            await interaction.followup.send("Not everyone has uploaded their YAML yet.", ephemeral=True)
            return

        await interaction.followup.send("Generating and starting game. This may take a while...", ephemeral=True)
        
        success, message = self.session_manager.begin_generation_and_start(
                password, 
                interaction.channel,
                release_mode,
                collect_mode,
                remaining_mode
            )

        # Delete the preparation message
        if self.session_manager.anchor_message:
            await self.session_manager.anchor_message.delete()
            self.session_manager.anchor_message = None

        await interaction.followup.send("Game generation started in the background. You will be notified when it's ready.")

    @session_group.command(name="cancel", description="Cancels the current session preparation or running game.")
    async def cancel(self, interaction: discord.Interaction):
        if not self.session_manager.is_active():
            await interaction.response.send_message("There is no active session to cancel.", ephemeral=True)
            return

        if interaction.user.id != self.session_manager.host.id:
            await interaction.response.send_message("Only the host can cancel the session.", ephemeral=True)
            return

        if self.session_manager.anchor_message:
            try:
                await self.session_manager.anchor_message.delete()
            except discord.NotFound:
                pass

        self.session_manager.reset_session()
        await interaction.response.send_message("The session was canceled.")

async def setup(bot: commands.Bot):
    await bot.add_cog(SessionCog(bot, bot.session_manager), guilds=[discord.Object(id=config['guild_id'])])