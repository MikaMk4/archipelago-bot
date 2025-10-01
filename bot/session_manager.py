import asyncio
import os
import re
import discord
import yaml
import glob
import zipfile
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

            final_embed = discord.Embed(
                title="Archipelago Session Started!",
                description=f"The server is reachable at `{self.config['server_public_ip']}:{self.config['server_port']}`.",
                color=discord.Color.green()
            )
            if password:
                final_embed.add_field(name="Password", value=f"`{password}`", inline=False)
            
            await self.bridge_channel.send(embed=final_embed, view=self.get_patch_files_view())

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

    def _extract_patch_files(self, zip_file_path: str):
        """
        Opens the generated zip file and extracts all patch files into the patch directory
        by checking for the '.ap' signature in the file extension.
        """
        patch_dir = self.config['patches_path']
        os.makedirs(patch_dir, exist_ok=True)

        print(f"Extracting patch files from {zip_file_path} to {patch_dir}")
        try:
            with zipfile.ZipFile(zip_file_path, 'r') as archive:
                for file_info in archive.infolist():
                    # Ignore directories
                    if file_info.is_dir():
                        continue
                    
                    # Split the filename into name and extension
                    _filename, extension = os.path.splitext(file_info.filename)
                    
                    # Check if '.ap' is in the extension (e.g., '.apz5', '.aplttp')
                    if '.ap' in extension.lower():
                        # Extract the file to the root of the patch directory
                        archive.extract(file_info, path=patch_dir)
                        print(f"  - Extracted {file_info.filename}")

        except FileNotFoundError:
            print(f"ERROR: Could not find zip file at {zip_file_path} to extract patches.")
        except Exception as e:
            print(f"An error occurred during patch extraction: {e}")

    def get_patch_files_view(self) -> discord.ui.View:
        view = discord.ui.View(timeout=None) # timeout=None makes the view persistent
        
        patch_dir = self.config['patches_path']
        if not os.path.exists(patch_dir):
            return view # Return an empty view if the directory doesn't exist

        patch_files = glob.glob(os.path.join(patch_dir, '*.ap*'))
        
        if not patch_files:
            button = discord.ui.Button(label="No patch files found.", style=discord.ButtonStyle.secondary, disabled=True)
            view.add_item(button)
            return view

        for i, patch_file_path in enumerate(patch_files):
            filename = os.path.basename(patch_file_path)
            custom_id = f"patch_download::{filename[:80]}"
            
            button = discord.ui.Button(label=f"{filename}", custom_id=custom_id, style=discord.ButtonStyle.primary, emoji="📄")
            view.add_item(button)
            
        return view

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

