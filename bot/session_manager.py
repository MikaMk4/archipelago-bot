import asyncio
import os
import re
import discord
import yaml
import glob
from .config import load_config

class SessionManager:
    def __init__(self):
        self.config = load_config()
        self.state = "inactive"  # "inactive", "preparing", "running"
        self.host = None
        self.players = {}  # Map: slot_name -> {'user': discord.User, 'ready': bool}
        self.preparation_message = None
        self.archipelago_path = "/opt/archipelago/squashfs-root"
        self.server_process = None
        self.chat_bridge_task = None
        self.bridge_channel = None

    def is_active(self):
        return self.state != "inactive"

    def reset_session(self):
        self.state = "inactive"
        self.host = None
        self.players = {}
        self.preparation_message = None
        self.bridge_channel = None
        
        # Terminate running processes
        if self.server_process and self.server_process.returncode is None:
            self.server_process.terminate()
        if self.chat_bridge_task:
            self.chat_bridge_task.cancel()
        
        self.server_process = None
        self.chat_bridge_task = None

        # Clean up directories
        for folder in [self.config['upload_dir'], self.config['games_dir'], self.config['patch_dir']]:
            if os.path.exists(folder):
                for file in os.listdir(folder):
                    os.remove(os.path.join(folder, file))
        print("Session reset and directories cleaned.")


    async def create_session(self, host: discord.User, players: list[discord.User]):
        if self.is_active():
            return False, "A session is already active or being prepared."
        
        self.reset_session()
        self.state = "preparing"
        self.host = host
        
        # Use display_name as it can be the server nickname.
        self.players = {member.display_name: {'user': member, 'ready': False} for member in players}
        
        # Also add the host to the players if they weren't tagged
        if host.display_name not in self.players:
            self.players[host.display_name] = {'user': host, 'ready': False}
            
        return True, "Session created successfully."

    def set_player_ready(self, player_name: str):
        if player_name in self.players:
            self.players[player_name]['ready'] = True
            return True
        return False
        
    def get_player_status(self):
        return list(self.players.values())

    async def start_session(self, password: str, channel: discord.TextChannel):
        if self.state != "preparing":
            return "Error: No session is being prepared."
        
        self.bridge_channel = channel # Save the channel for the bridge

        try:
            zip_file_path = await self._run_generation()
            await self._run_server(zip_file_path, password)
            self.state = "running"
            self._start_chat_bridge()
            return "Session started successfully."
        except Exception as e:
            print(f"ERROR during session start: {e}")
            self.reset_session()
            return f"Error starting session: {e}"

    async def _run_generation(self):
        generator_executable = os.path.join(self.archipelago_path, 'ArchipelagoGenerate')
        
        process = await asyncio.create_subprocess_exec(
            generator_executable,
            '--player_files', self.config['upload_dir'],
            '--outputpath', self.config['games_dir'],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_message = stderr.decode().strip()
            print(f"Generation failed: {error_message}")
            raise RuntimeError(f"Game generation failed: {error_message}")

        print("Game generation successful.")
        # Find the generated .zip file
        try:
            return glob.glob(os.path.join(self.config['games_dir'], '*.zip'))[0]
        except IndexError:
            raise FileNotFoundError("Could not find generated game zip file.")


    async def _run_server(self, zip_file_path, password):
        server_executable = os.path.join(self.archipelago_path, 'ArchipelagoServer')
        
        args = [
            server_executable,
            '--port', str(self.config['server_port']),
            '--multidata', zip_file_path
        ]
        if password:
            args.extend(['--password', password])

        # Start the server process
        self.server_process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        print(f"Server started with PID: {self.server_process.pid}")

    def _start_chat_bridge(self):
        if self.server_process and self.bridge_channel:
            self.chat_bridge_task = asyncio.create_task(self._chat_bridge_task())
        else:
            print("ERROR: Server process or bridge channel not found. Chat bridge not started.")

    async def _chat_bridge_task(self):
        print("Chat bridge task started.")
        item_sent_pattern = re.compile(r"^(?:\(.+?\)\s)?(.+?)\ssent\s(.+?)\sto\s(.+?)(?:\s\(.+?\))?\.")

        # Silent mentions: Does not trigger a notification
        silent_mentions = discord.AllowedMentions(users=False)

        while self.server_process and self.server_process.returncode is None:
            try:
                line_bytes = await self.server_process.stdout.readline()
                if not line_bytes:
                    await asyncio.sleep(0.1)
                    continue
                
                line = line_bytes.decode('utf-8', errors='ignore').strip()
                if not line:
                    continue

                print(f"[AP Server]: {line}")

                match = item_sent_pattern.match(line)
                if match:
                    sender_name, item_name, receiver_name = match.groups()

                    sender_data = self.players.get(sender_name.strip())
                    receiver_data = self.players.get(receiver_name.strip())
                    
                    sender_mention = sender_data['user'].mention if sender_data else f"**{sender_name}**"
                    receiver_mention = receiver_data['user'].mention if receiver_data else f"**{receiver_name}**"
                    
                    message = f"🎁 {sender_mention} sent **{item_name}** to {receiver_mention}!"
                    await self.bridge_channel.send(message, allowed_mentions=silent_mentions)
                else:
                    if "[Server]:" in line and not line.endswith("joined the game.") and not line.endswith("left the game."):
                        await self.bridge_channel.send(f"```{line}```")

            except asyncio.CancelledError:
                print("Chat bridge task was cancelled.")
                break
            except Exception as e:
                print(f"Error in chat bridge: {e}")
        
        print("Chat bridge loop finished.")

