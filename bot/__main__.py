import discord
from discord.ext import commands
from discord import app_commands
import os
import yaml
import glob
import zipfile
import json
from typing import Literal
from .config import load_config
from .session_manager import SessionManager

config = load_config()
session_manager = SessionManager()

# Define privileged intents
intents = discord.Intents.default()
intents.members = True          # Required to resolve user info from mentions

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

def get_whitelist():
    if not os.path.exists(config['whitelist_path']): return []
    with open(config['whitelist_path'], 'r') as f: return json.load(f)

def is_whitelisted(user_id: int): return user_id in get_whitelist()

@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=config['guild_id']))
    print(f'Bot {bot.user} is online and synced with Guild ID {config["guild_id"]}.')

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

session_group = app_commands.Group(name="session", description="Commands to manage Archipelago sessions.")

@session_group.command(name="create", description="Starts the preparation for a new session.")
async def create_session(interaction: discord.Interaction):
    if not is_whitelisted(interaction.user.id) and not await bot.is_owner(interaction.user):
        await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
        return

    success, message = await session_manager.create_session(interaction.user)

    if not success:
        await interaction.response.send_message(message, ephemeral=True)
        return

    # Create and send the initial status message
    await interaction.response.send_message("Session preparation started. Players can now upload their YAML files.")
    original_response = await interaction.original_response()
    session_manager.preparation_message = original_response
    await _update_preparation_embed()

@session_group.command(name="upload_yaml", description="Upload your YAML file for the session.")
@app_commands.describe(yaml_file="The YAML file to upload.")
async def upload_yaml(interaction: discord.Interaction, yaml_file: discord.Attachment):
    await interaction.response.defer()

    if not session_manager.is_active() or session_manager.state != "preparing":
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
    if player_name not in session_manager.players:
        await interaction.followup.send(f"Your slot name {player_name} in the YAML file is not part of the session.", ephemeral=True)
        return
    
    upload_path = os.path.join(config['upload_path'], f"{player_name}.yaml")
    await yaml_file.save(upload_path)

    # Mark the player as ready
    session_manager.set_player_ready(player_name)
    await _update_preparation_embed()
    await interaction.followup.send(f"File for '{player_name}' uploaded successfully.", ephemeral=True)


@session_group.command(name="start", description="Starts the game generation and server.")
async def start_session(
        interaction: discord.Interaction, 
        password: str = None,
        release_mode: Literal['auto', 'enabled', 'disabled', 'goal', 'auto-enabled'] = 'auto',
        collect_mode: Literal['auto', 'enabled', 'disabled', 'goal', 'auto-enabled'] = 'auto',
        remaining_mode: Literal['enabled', 'disabled', 'goal'] = 'goal'
    ):
    await interaction.response.defer()

    if not session_manager.is_active() or session_manager.state != "preparing":
        await interaction.followup.send("There is no session that could be started.", ephemeral=True)
        return
    if interaction.user.id != session_manager.host.id:
        await interaction.followup.send("Only the host can start the session.", ephemeral=True)
        return

    # Check if all players are ready
    if not all(p['ready'] for p in session_manager.get_player_status()):
        await interaction.followup.send("Not everyone has uploaded their YAML yet.", ephemeral=True)
        return
    
    if session_manager.preparation_message:
        try:
            await session_manager.preparation_message.delete()
            session_manager.preparation_message = None
        except discord.NotFound:
            print("Warning: Preparation message was already deleted before start command.")
            pass

    await interaction.followup.send("Generating and starting game. This may take a while...", ephemeral=True)
    
    success, message = session_manager.begin_generation_and_start(
            password, 
            interaction.channel,
            release_mode,
            collect_mode,
            remaining_mode
        )

    # Delete the preparation message
    if session_manager.preparation_message:
        await session_manager.preparation_message.delete()
        session_manager.preparation_message = None

    await interaction.followup.send("Game generation started in the background. You will be notified when it's ready.")


@session_group.command(name="cancel", description="Cancels the current session preparation or running game.")
async def cancel_session(interaction: discord.Interaction):
    if not session_manager.is_active():
        await interaction.response.send_message("There is no active session to cancel.", ephemeral=True)
        return

    if interaction.user.id != session_manager.host.id:
        await interaction.response.send_message("Only the host can cancel the session.", ephemeral=True)
        return

    # Delete the preparation message if it exists
    if session_manager.preparation_message:
        await session_manager.preparation_message.delete()

    session_manager.reset_session()
    await interaction.response.send_message("The session was canceled.")

@session_group.command(name="add_player", description="Add a player to the current session.")
async def add_player(interaction: discord.Interaction, new_player: discord.Member):
    await interaction.response.defer(ephemeral=True)

    if session_manager.state != "preparing":
        await interaction.followup.send("No session is being prepared right now.", ephemeral=True)
        return

    if interaction.user.id != session_manager.host.id:
        await interaction.followup.send("Only the host can add players.", ephemeral=True)
        return

    success, message = session_manager.add_player(new_player)

    if success:
        await _update_preparation_embed()
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.followup.send(message, ephemeral=True)


# --- Helper Functions ---
async def _update_preparation_embed():
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

def get_patch_files_view():
    view = discord.ui.View()
    patch_dir = config.get('patches_path', 'data/patches')
    
    # Extract patch files from the generated game zip
    game_zip_path = glob.glob(os.path.join(config['games_path'], '*.zip'))
    if game_zip_path:
        with zipfile.ZipFile(game_zip_path[0], 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                 # Add more patch file extensions if needed
                if file_info.filename.endswith(('.apz5', '.apbp', '.apmc', '.apv6', '.apsb', '.aptww', '.aplttp', '.apsoe', '.apct', '.apoot', '.apmm', '.apss')):
                    zip_ref.extract(file_info, patch_dir)

    if os.path.exists(patch_dir):
        for filename in os.listdir(patch_dir):
            # Create a button for each patch file
            button = discord.ui.Button(
                label=f"Download {filename}",
                style=discord.ButtonStyle.secondary,
                emoji="📄"
            )
            # We can't directly send files from buttons, so we send the file to the channel
            async def callback(interaction: discord.Interaction, file_path=os.path.join(patch_dir, filename)):
                await interaction.response.send_message(file=discord.File(file_path), ephemeral=True)
            
            button.callback = callback
            view.add_item(button)
    return view

# --- Bot start ---
def main():
    tree.add_command(session_group, guild=discord.Object(id=config['guild_id']))
    bot.run(config['discord_token'])

if __name__ == "__main__":
    main()

