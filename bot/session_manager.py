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
        self.archipelago_path = self.config['archipelago_path']
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
        for folder in [self.config['upload_path'], self.config['games_path'], self.config['patches_path']]:
            if os.path.exists(folder):
                for file in os.listdir(folder):
                    os.remove(os.path.join(folder, file))
        print("Session reset and directories cleaned.")


    async def create_session(self, host: discord.User):
        if self.is_active():
            return False, "A session is already active or being prepared."
        
        self.reset_session()
        self.state = "preparing"
        self.host = host
        
        # Add the host to the players
        if host.display_name not in self.players:
            self.players[host.display_name] = {'user': host, 'ready': False}
            
        return True, "Session created successfully."
    
    def add_player(self, new_player: discord.Member):
        if self.state != "preparing":
            return False, "There is no session being prepared."

        if new_player.display_name in self.players:
            return False, f"{new_player.mention} is already in the session."

        self.players[new_player.display_name] = {'user': new_player, 'ready': False}
        print(f"Player {new_player.display_name} added to the session.")
        return True, f"{new_player.mention} added to the session."
    
    def begin_generation_and_start(self, password: str, channel: discord.TextChannel, release_mode: str = None, collect_mode: str = None, remaining_mode: str = None):
        if self.state != "preparing":
            return False, "Session is not in the correct state to start generation."
        
        print("State transition: preparing -> generating")
        self.state = "generating"
        
        print("Creating background task for session start...")
        asyncio.create_task(self._start_session_task(password, channel, release_mode, collect_mode, remaining_mode))
        return True, "Generation process has been successfully launched."

    def set_player_ready(self, player_name: str):
        if player_name in self.players:
            self.players[player_name]['ready'] = True
            return True
        return False
        
    def get_player_status(self):
        return list(self.players.values())

    async def _start_session_task(self, password: str, channel: discord.TextChannel, release_mode: str, collect_mode: str, remaining_mode: str):
        self.bridge_channel = channel
        try:
            zip_file_path = await self._run_generation()
            self._extract_patch_files(zip_file_path)
            await self._run_server(zip_file_path, password, release_mode, collect_mode, remaining_mode)

            await asyncio.sleep(1) 
            if not self.server_process or self.server_process.returncode is not None:
                raise RuntimeError("Server process failed to start or terminated immediately.")

            print("State transition: generating -> running")
            self.state = "running"
            self._start_chat_bridge()
            print("Background task: Game generated and server started successfully.")

        except Exception as e:
            error_message = f"An error occurred: {str(e)}"
            print(f"ERROR during session start: {error_message}")
            await self.bridge_channel.send(f"Error at starting session for host {self.host.mention}:\n```\n{error_message}\n```")
            self.reset_session()

    async def _run_generation(self):
        generator_executable = os.path.join(self.archipelago_path, 'ArchipelagoGenerate')
        
        process = await asyncio.create_subprocess_exec(
            generator_executable,
            '--player_files', self.config['upload_path'],
            '--outputpath', self.config['games_path'],
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
            return glob.glob(os.path.join(self.config['games_path'], '*.zip'))[0]
        except IndexError:
            raise FileNotFoundError("Could not find generated game zip file.")


    async def _run_server(self, zip_file_path: str, password: str, release_mode: str, collect_mode: str, remaining_mode: str):
        server_executable = os.path.join(self.archipelago_path, 'ArchipelagoServer')
        
        args = [
            server_executable,
            '--host', '0.0.0.0',
            '--port', str(self.config['server_port']),
        ]
        if password:
            args.extend(['--password', password])

        if release_mode:
            args.extend(['--release_mode', release_mode])
        if collect_mode:
            args.extend(['--collect_mode', collect_mode])
        if remaining_mode:
            args.extend(['--remaining_mode', remaining_mode])

        args.append(zip_file_path)

        # Start the server process
        self.server_process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        print(f"Server started with PID: {self.server_process.pid}")

    def _start_chat_bridge(self):
        if self.server_process and self.bridge_channel:
            self.chat_bridge_task = asyncio.create_task(self._chat_bridge_task(
                self.server_process.stdout,
                self.server_process.stderr
            ))
            print("Chat bridge task started.")

    async def _chat_bridge_task(self, stdout, stderr):
        item_sent_pattern = re.compile(r"^(?:\(.+?\)\s)?(.+?)\ssent\s(.+?)\sto\s(.+?)(?:\s\(.+?\))?\.?$")
        silent_mentions = discord.AllowedMentions(users=False)

        async def log_stream(stream, prefix):
            while True:
                try:
                    line_bytes = await stream.readline()
                    if not line_bytes: break
                    
                    line = line_bytes.decode('utf-8', errors='ignore').strip()
                    if not line: continue
                    
                    print(f"[{prefix}]: {line}")

                    if prefix == "AP Server STDOUT":
                        match = item_sent_pattern.match(line)
                        if match:
                            sender_name, item_name, receiver_name = match.groups()
                            
                            sender_data = self.players.get(sender_name.strip())
                            receiver_data = self.players.get(receiver_name.strip())
                            
                            sender_mention = sender_data['user'].mention if sender_data else f"**{sender_name.strip()}**"
                            receiver_mention = receiver_data['user'].mention if receiver_data else f"**{receiver_name.strip()}**"
                            
                            message = f"🎁 {sender_mention} sent **{item_name.strip()}** to {receiver_mention}!"
                            await self.bridge_channel.send(message, allowed_mentions=silent_mentions)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"Error reading stream {prefix}: {e}")
                    break

        await asyncio.gather(
            log_stream(stdout, "AP Server STDOUT"),
            log_stream(stderr, "AP Server STDERR")
        )
        print("Chat bridge loop finished.")

