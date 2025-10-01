import discord
from discord import app_commands
import os
import yaml
import glob
import zipfile
from .config import load_config
from .session_manager import SessionManager

config = load_config()
session_manager = SessionManager()

# Define privileged intents
intents = discord.Intents.default()
intents.members = True          # Required to resolve user info from mentions

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=config['guild_id']))
    print(f'Bot {bot.user} is online and synced with Guild ID {config["guild_id"]}.')
    print(f'Whitelist: {config["whitelist"]}')

session_group = app_commands.Group(name="session", description="Verwaltet Archipelago-Sessions.")

@session_group.command(name="create", description="Starts the preparation for a new session.")
@app_commands.describe(players="Tag the players to include in the session.")
async def create_session(interaction: discord.Interaction, players: str):
    if interaction.user.id not in config['whitelist']:
        await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
        return

    # Convert mentions to user objects
    mentioned_users = interaction.message.mentions if interaction.message else []
    if not mentioned_users:
         # Fallback for interactions where message is not available
         # This is a bit of a hack, but necessary for slash commands
        try:
            ids = [int(p.strip('<@!>')) for p in players.split(' ') if p.startswith('<@')]
            mentioned_users = [await bot.fetch_user(uid) for uid in ids]
        except (ValueError, discord.NotFound):
             await interaction.response.send_message("Error: Could not find players. Please tag them with @", ephemeral=True)
             return

    success, message = await session_manager.create_session(interaction.user, mentioned_users)

    if not success:
        await interaction.response.send_message(message, ephemeral=True)
        return

    # Create and send the initial status message
    await interaction.response.send_message("Session preparation started. Players can now upload their YAML files.")
    original_response = await interaction.original_response()
    session_manager.preparation_message = original_response
    await update_preparation_message(session_manager)

@session_group.command(name="upload_yaml", description="Upload your YAML file for the session.")
@app_commands.describe(yaml_file="The YAML file to upload.")
async def upload_yaml(interaction: discord.Interaction, yaml_file: discord.Attachment):
    if not session_manager.is_active() or session_manager.state != "preparing":
        await interaction.response.send_message("There is currently no session preparing.", ephemeral=True)
        return

    if not yaml_file.filename.lower().endswith('.yaml'):
        await interaction.response.send_message("Please upload a valid .yaml file.", ephemeral=True)
        return

    # Read the file content and parse it to get the player name
    try:
        yaml_content_bytes = await yaml_file.read()
        yaml_data = yaml.safe_load(yaml_content_bytes)
        player_name = yaml_data.get('name')
        if not player_name:
            await interaction.response.send_message("Your YAML file needs a `name` entry.", ephemeral=True)
            return
    except Exception as e:
        await interaction.response.send_message(f"Error when reading YAML: {e}", ephemeral=True)
        return
        
    # Check if the uploader's name matches a player in the session
    if player_name not in session_manager.players:
        await interaction.response.send_message(f"Your slot name {player_name} in the YAML file is not part of the session.", ephemeral=True)
        return
    
    upload_path = os.path.join(config['upload_dir'], f"{player_name}.yaml")
    await yaml_file.save(upload_path)

    # Mark the player as ready
    session_manager.set_player_ready(player_name)
    await update_preparation_message(session_manager)
    await interaction.response.send_message(f"File for '{player_name}' uploaded successfully.", ephemeral=True, delete_after=10)


@session_group.command(name="start", description="Starts the game generation and server.")
@app_commands.describe(password="Optional server password.")
async def start_session(interaction: discord.Interaction, password: str = None):
    if not session_manager.is_active() or session_manager.state != "preparing":
        await interaction.response.send_message("There is no session that could be started.", ephemeral=True)
        return
    if interaction.user.id != session_manager.host.id:
        await interaction.response.send_message("Only the host can start the session.", ephemeral=True)
        return

    # Check if all players are ready
    if not all(p['ready'] for p in session_manager.get_player_status()):
        await interaction.response.send_message("Not everyone has uploaded their YAML yet.", ephemeral=True)
        return

    await interaction.response.send_message("Generating and starting game. This may take a while...", ephemeral=True)
    
    # Pass the current channel to the session manager for the chat bridge
    status_message = await session_manager.start_session(password, interaction.channel)

    # Delete the preparation message
    if session_manager.preparation_message:
        await session_manager.preparation_message.delete()
        session_manager.preparation_message = None

    # Send final confirmation with download links
    final_embed = discord.Embed(
        title="Archipelago Session Gestartet!",
        description=f"Der Server läuft unter `{config['server_public_ip']}:{config['server_port']}`.\n{status_message}",
        color=discord.Color.green()
    )
    await interaction.channel.send(embed=final_embed, view=get_patch_files_view())


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
    await interaction.response.send_message("The session was canceled.", ephemeral=True)


# --- Helper Functions ---
async def update_preparation_message(session_manager: SessionManager):
    if not session_manager.preparation_message:
        print("Warning: Attempted to update a non-existent preparation message.")
        return

    host = session_manager.host
    player_statuses = session_manager.get_player_status()
    
    embed = discord.Embed(
        title="Archipelago Session in preparation",
        description=f"Host: {host.mention if host else 'Unknown'}\n\nPlayers can now upload their YAML files using `/session upload_yaml`.",
        color=discord.Color.blue()
    )
    
    status_text = ""
    if not player_statuses:
        status_text = "No players added yet."
    else:
        for player in player_statuses:
            status_text += f"{'✅' if player['ready'] else '❌'} {player['user'].mention}\n"
    
    embed.add_field(name="Player Status", value=status_text, inline=False)
    
    await session_manager.preparation_message.edit(content="", embed=embed)

def get_patch_files_view():
    view = discord.ui.View()
    patch_dir = config.get('patch_dir', 'data/patches')
    
    # Extract patch files from the generated game zip
    game_zip_path = glob.glob(os.path.join(config['games_dir'], '*.zip'))
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

