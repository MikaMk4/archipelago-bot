import asyncio
import glob
import os
import shutil
import zipfile
import re

class SessionManager:
    def __init__(self, config):
        self.config = config
        self.server_process = None
        self.running_session_admin_id = None
        self.staged_sessions = {}

    def is_server_running(self):
        return self.server_process is not None and self.server_process.returncode is None
    
    def is_admin(self, user_id: int):
        return user_id == self.running_session_admin_id
    
    def get_admin(self):
        return self.running_session_admin_id
    
    def get_staged_session(self, host_id: int):
        return self.staged_sessions.get(host_id)
    
    def get_session_for_player(self, player_id: int):
        for host_id, session in self.staged_sessions.items():
            if player_id in session["players"]:
                return session
        return None
    
    def _cleanup_session_files(self, session_id: str):
        for dir_key in ['upload_path', 'games_path', 'patches_path']:
            path = os.path.join(self.config[dir_key], str(session_id))
            if os.path.exists(path):
                shutil.rmtree(path)

    def stage_session(self, host_id: int, invited_players: dict, channel_id: int):
        """Starts the preparation phase of a session."""
        if self.is_server_running():
            return {'success': False, 'message': 'A session is already running.'}
        if host_id in self.staged_sessions:
            return {'success': False, 'message': 'You are already preparing a session. Cancel that first with `/session cancel`.'}
        
        session_id = str(host_id)
        self._cleanup_session_files(session_id)
        os.makedirs(os.path.join(self.config['upload_path'], session_id), exist_ok=True)

        self.staged_sessions[host_id] = {
            "session_id": session_id,
            "host_id": host_id,
            "channel_id": host_id,
            "message_id": None,
            "players": {player_id: {"name": name, "has_uploaded": False} for player_id, name in invited_players.items()}
        }

        return {'success': True}
    
    def add_yaml_to_staged_session(self, session: dict, player_id: int, attachment):
        session_id = session['session_id']
        player_slot_name = os.path.splitext(attachment.filename)[0] # Name in yaml file TODO: change to not rely on index
        save_path = os.path.join(self.config['upload_path'], session_id, f"{player_id}_{player_slot_name}.yaml")

        session["players"][player_id]["has_uploaded"] = True
        return save_path
    
    def cancel_staged_session(self, host_id: int):
        if host_id in self.staged_sessions:
            session_id = self.staged_sessions[host_id]["session_id"]
            self._cleanup_session_files(session_id)
            del self.staged_sessions[host_id]
            return True
        return False

    async def generate_game(self, session_id: str):
        """Generates a game for a specific session id."""
        upload_path = os.path.join(self.config['upload_path'], session_id)
        games_path = os.path.join(self.config['games_path'], session_id)
        os.makedirs(games_path, exist_ok=True)

        generator_path = os.path.join(self.config['archipelago_path'], 'ArchipelagoGenerate')
        
        process = await asyncio.create_subprocess_exec(
            generator_path,
            '--player_files', upload_path,
            '--outputpath', games_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            return {'success': False, 'output': stderr.decode()}
        return {'success': True, 'output': stdout.decode()}

    async def host_game(self, session_id: str, admin_id: int, password: str = None):
        """Hosts the generated game and changes status from staged to running."""
        games_path = os.path.join(self.config['games_path'], session_id)
        patches_path = os.path.join(self.config['patches_path'], session_id)
        os.makedirs(patches_path, exist_ok=True)
        
        try:
            game_zip = glob.glob(os.path.join(games_path, '*.zip'))[0]
        except IndexError:
            return {'success': False, 'output': 'Keine .zip-Datei im Output-Ordner gefunden.'}

        patch_files = []
        with zipfile.ZipFile(game_zip, 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.filename.endswith(('.apz5', '.apbp', '.apmc', '.apv6', '.apsb', '.aptww', '.aplttp', '.apsoe', '.apct', '.apoot', '.apmm', '.apss')):
                    zip_ref.extract(file_info, patches_path)
                    patch_files.append(os.path.join(patches_path, file_info.filename))

        server_path = os.path.join(self.config['archipelago_path'], 'ArchipelagoServer')
        args = [server_path, '--port', str(self.config['archipelago_port']), game_zip]
        if password:
            args.extend(['--password', password])
            
        self.server_process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        
        # Change status
        self.running_session_admin_id = admin_id
        if admin_id in self.staged_sessions:
            del self.staged_sessions[admin_id]

        return {'success': True, 'patches': patch_files}

    def stop_game(self):
        if self.is_server_running():
            session_id = str(self.running_session_admin_id)
            self.server_process.terminate()
            self.server_process = None
            self.running_session_admin_id = None
            self._cleanup_session_files(session_id)

    async def chat_bridge(self, channel):
        """Reads server output and forwards relevant messages to Discord."""
        if not self.is_server_running(): return
            
        while self.is_server_running():
            try:
                line_bytes = await self.server_process.stdout.readline()
                if not line_bytes:
                    await asyncio.sleep(1); continue

                line = line_bytes.decode('utf-8', errors='ignore').strip()
                print(f"[Server] {line}")
                if "sent" in line and "to" in line:
                    await channel.send(f"**[Item-Info]** {line}")
                
                chat_match = re.search(r"\[Chat\]:\s*(.*)", line)
                if chat_match:
                    await channel.send(f"**[In-Game Chat]** {chat_match.group(1)}")

            except Exception as e:
                print(f"Error in chat bridge: {e}"); break
        print("Chat bridge terminated.")