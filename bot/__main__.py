import os
import discord
from discord.ext import commands
from discord import app_commands, File, Member, Attachment
import asyncio
import json
from typing import Optional

from .config import load_config
from .session_manager import SessionManager

config = load_config()
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
session_manager = SessionManager(config)

def get_whitelist():
    if not os.path.exists(config['whitelist_path']): return []
    with open(config['whitelist_path'], 'r') as f: return json.load(f)

def is_whitelisted(user_id: int): return user_id in get_whitelist()

def create_session_embed(session: dict):
    """Creates or updates the status embed for a session in preparation."""
    host = bot.get_user(session['host_id'])
    embed = discord.Embed(
        title="Archipelago session in preparation",
        description=f"Host: {host.mention if host else 'Unknown'}\nUpload your `.yaml`-files with `/session upload_yaml`.",
        color=discord.Color.orange()
    )
    
    player_status = []
    for player_id, data in session['players'].items():
        status_icon = "✅" if data['has_uploaded'] else "❌"
        player_user = bot.get_user(player_id)
        player_mention = player_user.mention if player_user else data['name']
        player_status.append(f"{status_icon} {player_mention}")

    embed.add_field(name="Player", value="\n".join(player_status), inline=False)
    embed.set_footer(text=f"Session ID: {session['session_id']} | Host can start the game with `/session start`.")
    return embed

@bot.event
async def on_ready():
    print(f'{bot.user} is online!')
    try:
        guild_id = config.get('guild_id')
        if guild_id:
            guild = discord.Object(id=guild_id)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
        print("Slash commands synchronized.")
    except Exception as e:
        print(f"Error at synchronization of commands: {e}")

# --- Command Groups ---
session_group = app_commands.Group(name="session", description="Commands for management of Archipelago sessions.")
admin_group = app_commands.Group(name="admin", description="Admin commands for the bot.", parent=session_group)

# --- Session Commands ---
@session_group.command(name="create", description="Prepares a new Archipelago session.")
@app_commands.describe(
    player1="Player 1 (Discord User)",
    player2="Optional: Player 2",
    player3="Optional: Player 3",
    player4="Optional: Player 4",
    player5="Optional: Player 5"
)
async def create_session(interaction: discord.Interaction, player1: Member, player2: Optional[Member] = None, player3: Optional[Member] = None, player4: Optional[Member] = None, player5: Optional[Member] = None):
    if not is_whitelisted(interaction.user.id):
        return await interaction.response.send_message("You are not authorized to create a session.", ephemeral=True)

    invited_users = {p.id: p.name for p in [player1, player2, player3, player4, player5] if p}
    
    result = session_manager.stage_session(interaction.user.id, invited_users, interaction.channel_id)
    if not result['success']:
        return await interaction.response.send_message(f"Error: {result['message']}", ephemeral=True)

    session = session_manager.get_staged_session(interaction.user.id)
    embed = create_session_embed(session)
    await interaction.response.send_message(embed=embed)
    message = await interaction.original_response()
    session['message_id'] = message.id

@session_group.command(name="upload_yaml", description="Uploads a .yaml file for the current session.")
@app_commands.describe(yaml_file="Your personal .yaml config file.")
async def upload_yaml(interaction: discord.Interaction, yaml_file: Attachment):
    if not yaml_file.filename.lower().endswith('.yaml'):
        return await interaction.response.send_message("Error: Please only upload `.yaml`-files.", ephemeral=True)

    session = session_manager.get_session_for_player(interaction.user.id)
    if not session:
        return await interaction.response.send_message("You were not invited to any session in preparation.", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    save_path = session_manager.add_yaml_to_staged_session(session, interaction.user.id, yaml_file)
    await yaml_file.save(save_path)
    
    try:
        channel = bot.get_channel(session['channel_id'])
        message = await channel.fetch_message(session['message_id'])
        await message.edit(embed=create_session_embed(session))
    except Exception as e:
        print(f"Could not update the session message: {e}")

    await interaction.followup.send("Your .yaml file was uploaded successfully!")

@session_group.command(name="start", description="Starts the session you prepared.")
@app_commands.describe(password="Set an optional password for the server.")
async def start_session(interaction: discord.Interaction, password: Optional[str] = None):
    session = session_manager.get_staged_session(interaction.user.id)
    if not session:
        return await interaction.response.send_message("You are not preparing any session.", ephemeral=True)
    
    await interaction.response.defer(thinking=True)
    
    session_id = session['session_id']
    
    # 1. Generate game
    gen_result = await session_manager.generate_game(session_id)
    if not gen_result['success']:
        return await interaction.followup.send(f"Error at game generation:\n```\n{gen_result['output']}\n```")

    # 2. Host game
    host_result = await session_manager.host_game(session_id, interaction.user.id, password)
    if not host_result['success']:
        return await interaction.followup.send(f"Error at hosting of game:\n```\n{host_result['output']}\n```")

    # 3. Start chat bridge
    asyncio.create_task(session_manager.chat_bridge(interaction.channel))

    # 4. Post success message and patches
    patch_files = [File(p) for p in host_result['patches']]
    
    embed = discord.Embed(
        title="✅ Archipelago session started!",
        description=f"Server admin: {interaction.user.mention}",
        color=discord.Color.green()
    )
    server_ip = config.get('server_public_ip', 'YOUR_SERVER_IP')
    embed.add_field(name="Server adress", value=f"`{server_ip}:{config['archipelago_port']}`", inline=False)
    if password:
        embed.add_field(name="Password", value=f"`{password}`", inline=False)
    embed.set_footer(text="The Patch files for the players are attached.")
    
    await interaction.followup.send(embed=embed, files=patch_files)

    # Delete old staging messages
    try:
        message = await interaction.channel.fetch_message(session['message_id'])
        await message.delete()
    except Exception as e:
        print(f"Could not delete old staging messages: {e}")

@session_group.command(name="stop", description="Stops the currently running Archipelago session.")
async def stop_session(interaction: discord.Interaction):
    if not session_manager.is_server_running():
        return await interaction.response.send_message("No running session currently.", ephemeral=True)

    if not session_manager.is_admin(interaction.user.id) and not await bot.is_owner(interaction.user):
        return await interaction.response.send_message("Only the session admin or the bot owner can stop the session.", ephemeral=True)
        
    session_manager.stop_game()
    await interaction.response.send_message("The Archipelago session was successfully terminated and all game data was cleaned up.")

@session_group.command(name="cancel", description="Cancels the preparation of your session.")
async def cancel_session(interaction: discord.Interaction):
    session = session_manager.get_staged_session(interaction.user.id)
    if not session:
        return await interaction.response.send_message("You are not preparing any session.", ephemeral=True)
    
    session_manager.cancel_staged_session(interaction.user.id)
    
    try:
        message = await interaction.channel.fetch_message(session['message_id'])
        await message.delete()
    except Exception: pass

    await interaction.response.send_message("Session preparation was canceled.", ephemeral=True)

# --- Admin Commands ---
@admin_group.command(name="whitelist_add", description="Adds a user to the whitelist.")
@app_commands.describe(user="The user to be added to the whitelist.")
async def whitelist_add(interaction: discord.Interaction, user: Member):
    if not await bot.is_owner(interaction.user):
        return await interaction.response.send_message("Only the bot owner can use this command.", ephemeral=True)

    whitelist = get_whitelist()
    if user.id not in whitelist:
        whitelist.append(user.id)
        with open(config['whitelist_path'], 'w') as f: json.dump(whitelist, f)
        await interaction.response.send_message(f"{user.mention} was added to the whitelist.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} already is on the whitelist.", ephemeral=True)

# --- Bot-Start ---
bot.tree.add_command(session_group)
bot.run(config['discord_token'])

