import time
import datetime
import asyncio
from telethon.sync import TelegramClient
from telethon import errors
from flask import Flask, request, render_template, redirect, url_for, session
import json
import os
import logging
from collections import defaultdict
from config import Config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Function to set up user-specific logging
def setup_user_logging(phone_number):
    # Create a directory for user logs if it doesn't exist
    if not os.path.exists("user_logs"):
        os.makedirs("user_logs")
    
    # Define the log file path for the specific user
    log_file_path = f"user_logs/log_{phone_number}.log"
    
    # Set up logging for the user
    logger = logging.getLogger(phone_number)
    logger.setLevel(logging.INFO)

    # Create file handler to write logs to user-specific file
    file_handler = logging.FileHandler(log_file_path)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    
    # Remove any existing handlers for the user logger to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()
    
    logger.addHandler(file_handler)
    return logger

# Global dictionary to track user tasks
user_tasks = defaultdict(list)  # Maps user IDs to their tasks

app = Flask(__name__)
app.config.from_object(Config)

# Access the secret key if needed
app.secret_key = app.config['SECRET_KEY']
class TelegramForwarder:
    def __init__(self, api_id, api_hash, phone_number):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone_number = phone_number
        self.client = TelegramClient('session_' + phone_number, api_id, api_hash)
        self.active_tasks = []  # List to track active forwarding tasks
        self.last_forwarded_keywords = {}
        self.last_forwarded_solana = {}
        self.last_forwarded_ethereum = {}
        self.last_forwarded_cashtags = {}

    async def list_chats(self):
        await self.client.connect()

        if not await self.client.is_user_authorized():
            await self.client.send_code_request(self.phone_number)
            await self.client.sign_in(self.phone_number, input('Enter the code: '))

        dialogs = await self.client.get_dialogs()
        chats_list = []
        for dialog in dialogs:
            username = getattr(dialog.entity, 'username', 'N/A')  # Safely check for username attribute
            chats_list.append(f"Chat ID: {dialog.id}, Title: {dialog.title}, Username: {username}")
        
        logger.info("List of groups printed successfully!")
        return chats_list

    async def forward_messages_to_channel(self, user_id, source_chats, destinations, keywords,
                                          solana_enabled=False, solana_source_chats=None, solana_destinations=None, solana_timer=None,
                                          eth_enabled=False, eth_source_chats=None, eth_destinations=None, eth_timer=None,
                                          cashtag_enabled=False, cashtag_source_chats=None, cashtag_destinations=None, cashtag_timer=None,
                                          keyword_timer=None):
        task = asyncio.current_task()
        self.active_tasks.append(task)

        await self.client.connect()

        if not await self.client.is_user_authorized():
            await self.client.send_code_request(self.phone_number)
            await self.client.sign_in(self.phone_number, input('Enter the code: '))

        for source_chat in source_chats:
            if isinstance(source_chat, str) and source_chat.lstrip('-').isdigit():
                source_chat_id = int(source_chat)
            elif isinstance(source_chat, str):
                try:
                    source_chat_id = await self._get_chat_id_from_title(source_chat)
                    logger.info(f"Found chat ID for '{source_chat}': {source_chat_id}")
                except ValueError as e:
                    continue
            else:
                source_chat_id = source_chat

            last_message_id = (await self.client.get_messages(source_chat_id, limit=1))[0].id
            while True:
                if task.cancelled():
                    break

                messages = await self.client.get_messages(source_chat_id, min_id=last_message_id, limit=None)
                for message in reversed(messages):
                    if keywords and message.text and any(keyword in message.text.lower() for keyword in keywords):
                        if self._can_forward(message.text, "keywords", keyword_timer):
                            for destination in destinations:
                                await self._send_message(destination, message.text, False)
                                logger.info(f"Message forwarded to channel/chat ID {destination}: {message.text}")
                            self._update_forward_time(message.text, "keywords")
                    last_message_id = max(last_message_id, message.id)
                await asyncio.sleep(5)

        # Forward Solana contract messages
        if solana_enabled:
            for solana_source_chat in solana_source_chats:
                if isinstance(solana_source_chat, str) and solana_source_chat.lstrip('-').isdigit():
                    solana_source_chat_id = int(solana_source_chat)
                elif isinstance(solana_source_chat, str):
                    try:
                        solana_source_chat_id = await self._get_chat_id_from_title(solana_source_chat)
                    except ValueError as e:
                        continue
                else:
                    solana_source_chat_id = solana_source_chat

                last_message_id = (await self.client.get_messages(solana_source_chat_id, limit=1))[0].id
                while True:
                    if task.cancelled():
                        break

                    messages = await self.client.get_messages(solana_source_chat_id, min_id=last_message_id, limit=None)
                    for message in reversed(messages):
                        solana_contract = self._find_solana_contract(message.text)
                        if solana_contract and self._can_forward(solana_contract, "solana", solana_timer):
                            for solana_destination in solana_destinations:
                                await self._send_message(solana_destination, solana_contract, False)
                                logger.info(f"Solana contract forwarded to {solana_destination}: {solana_contract}")
                            self._update_forward_time(solana_contract, "solana")
                    last_message_id = max(last_message_id, message.id)
                await asyncio.sleep(5)

        # Forward Ethereum contract messages
        if eth_enabled:
            for eth_source_chat in eth_source_chats:
                if isinstance(eth_source_chat, str) and eth_source_chat.lstrip('-').isdigit():
                    eth_source_chat_id = int(eth_source_chat)
                elif isinstance(eth_source_chat, str):
                    try:
                        eth_source_chat_id = await self._get_chat_id_from_title(eth_source_chat)
                    except ValueError as e:
                        continue
                else:
                    eth_source_chat_id = eth_source_chat

                last_message_id = (await self.client.get_messages(eth_source_chat_id, limit=1))[0].id
                while True:
                    if task.cancelled():
                        break

                    messages = await self.client.get_messages(eth_source_chat_id, min_id=last_message_id, limit=None)
                    for message in reversed(messages):
                        eth_contract = self._find_ethereum_contract(message.text)
                        if eth_contract and self._can_forward(eth_contract, "ethereum", eth_timer):
                            for eth_destination in eth_destinations:
                                await self._send_message(eth_destination, eth_contract, False)
                                logger.info(f"Ethereum contract forwarded to {eth_destination}: {eth_contract}")
                            self._update_forward_time(eth_contract, "ethereum")
                    last_message_id = max(last_message_id, message.id)
                await asyncio.sleep(5)

        # Forward Cashtag messages
        if cashtag_enabled:
            for cashtag_source_chat in cashtag_source_chats:
                if isinstance(cashtag_source_chat, str) and cashtag_source_chat.lstrip('-').isdigit():
                    cashtag_source_chat_id = int(cashtag_source_chat)
                elif isinstance(cashtag_source_chat, str):
                    try:
                        cashtag_source_chat_id = await self._get_chat_id_from_title(cashtag_source_chat)
                    except ValueError as e:
                        continue
                else:
                    cashtag_source_chat_id = cashtag_source_chat

                last_message_id = (await self.client.get_messages(cashtag_source_chat_id, limit=1))[0].id
                while True:
                    if task.cancelled():
                        break

                    messages = await self.client.get_messages(cashtag_source_chat_id, min_id=last_message_id, limit=None)
                    for message in reversed(messages):
                        cashtags = self._find_cashtag(message.text)
                        if cashtags:
                            for cashtag in cashtags:
                                if self._can_forward(cashtag, "cashtags", cashtag_timer):
                                    for cashtag_destination in cashtag_destinations:
                                        await self._send_message(cashtag_destination, cashtag, False)
                                        logger.info(f"Cashtag forwarded to {cashtag_destination}: {cashtag}")
                                    self._update_forward_time(cashtag, "cashtags")
                    last_message_id = max(last_message_id, message.id)
                await asyncio.sleep(5)

        self.active_tasks.remove(task)

    async def _send_message(self, destination, message_text, is_bot):
        try:
            if is_bot:
                await self.client.send_message(destination, message_text)
            else:
                await self.client.send_message(destination, message_text)
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"An error occurred while forwarding the message: {e}")

    async def stop_forwarding_job(self, job_number):
        try:
            task = self.active_tasks[job_number]
            task.cancel()
            logger.info(f"Forwarding job {job_number + 1} has been stopped.")
        except IndexError:
            logger.error("Invalid job number. Please try again.")

    def _find_solana_contract(self, text):
        import re
        solana_pattern = r'[1-9A-HJ-NP-Za-km-z]{32,44}'
        matches = re.findall(solana_pattern, text)
        return matches[0] if matches else None

    def _find_ethereum_contract(self, text):
        import re
        eth_pattern = r'0x[a-fA-F0-9]{40}'
        matches = re.findall(eth_pattern, text)
        return matches[0] if matches else None

    def _find_cashtag(self, text):
        import re
        cashtag_pattern = r'\$[A-Z]+'
        return re.findall(cashtag_pattern, text)

    def _can_forward(self, content, forward_type, timer):
        current_time = time.time()
        last_forwarded = getattr(self, f'last_forwarded_{forward_type}').get(content, 0)
        if current_time - last_forwarded >= float(timer):
            return True
        return False

    def _update_forward_time(self, content, forward_type):
        getattr(self, f'last_forwarded_{forward_type}')[content] = time.time()

    async def _get_chat_id_from_title(self, chat_title):
        dialogs = await self.client.get_dialogs()
        for dialog in dialogs:
            if dialog.title == chat_title:
                return dialog.id
        raise ValueError(f"Chat with title '{chat_title}' not found")

def read_credentials(phone_number):
    try:
        filename = f"config_{phone_number}.json"
        with open(filename, "r") as file:
            return json.load(file)
    except FileNotFoundError:
        logger.error(f"Config file for {phone_number} not found.")
        return None

def save_config(data, phone_number):
    filename = f"config_{phone_number}.json"
    with open(filename, "w") as file:
        json.dump(data, file)
########
@app.route('/auth', methods=['GET', 'POST'])
def auth():
    if request.method == 'POST':
        api_id = request.form['api_id']
        api_hash = request.form['api_hash']
        phone_number = request.form['phone_number']

        user_logger = setup_user_logging(phone_number)

        config = {
            'api_id': api_id,
            'api_hash': api_hash,
            'phone_number': phone_number
        }
        save_config(config, phone_number)

        async def connect_and_auth():
            client = TelegramClient('session_' + phone_number, api_id, api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                try:
                    result = await client.send_code_request(phone_number)
                    # Save phone_code_hash in the session for later use
                    session['phone_code_hash'] = result.phone_code_hash
                    print(f"Stored phone code hash: {session['phone_code_hash']}")  # Debug statement
                    return False, "Authorization required"
                except Exception as e:
                    user_logger.error(f"Error sending code: {e}")
                    return False, f"Error sending code: {e}"
            return True, None

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            is_authorized, message = loop.run_until_complete(connect_and_auth())

            if not is_authorized:
                return render_template('auth.html', error=message, phone_number=phone_number)

            session['phone_number'] = phone_number
            user_logger.info("User authenticated successfully.")
            return redirect(url_for('verify_code'))  # Redirect to the verification page

        except Exception as e:
            user_logger.error(f"Invalid credentials: {e}")
            return render_template('auth.html', error="Invalid credentials, please try again.")
        finally:
            loop.close()

    return render_template('auth.html')

@app.route('/verify_code', methods=['POST'])
def verify_code():
    phone_number = request.form['phone_number']
    code = request.form['code']
    
    config = read_credentials(phone_number)
    if not config:
        return render_template('auth.html', error="Configuration not found. Please authenticate again.")

    async def sign_in(phone, code, config):
        client = TelegramClient('session_' + phone, config['api_id'], config['api_hash'])
        await client.connect()
        try:
            # Retrieve the stored phone_code_hash
            phone_code_hash = session.get('phone_code_hash')
            if not phone_code_hash:
                return False, "Phone code hash not found. Please restart the authentication process."

            # Use phone, code, and phone_code_hash to sign in
            await client.sign_in(phone, code, phone_code_hash)
            return True, None
        except Exception as e:
            return False, str(e)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success, error = loop.run_until_complete(sign_in(phone_number, code, config))

        if success:
            session['phone_number'] = phone_number
            return redirect(url_for('menu'))
        else:
            return render_template('auth.html', error=f"Failed to verify: {error}", phone_number=phone_number)
    finally:
        loop.close()

########

@app.route('/menu')
def menu():
    return render_template('menu.html')

@app.route('/list_chats')
def list_chats():
    phone_number = session.get('phone_number')
    if not phone_number:
        logger.error("Phone number not found in session.")
        return "Phone number not found in session. Please authenticate first.", 400

    config = read_credentials(phone_number)
    if not config:
        logger.error(f"Configuration not found for phone number: {phone_number}")
        return "Configuration not found for this phone number.", 400

    forwarder = TelegramForwarder(config['api_id'], config['api_hash'], config['phone_number'])

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        chats = loop.run_until_complete(forwarder.list_chats())
        logger.info("Chats retrieved successfully.")
        return render_template('list_chats.html', chats=chats)
    except Exception as e:
        logger.error(f"Error listing chats: {e}")
        return f"Error listing chats: {e}", 500
    finally:
        loop.close()

@app.route('/start_forwarding', methods=['GET', 'POST'])
def start_forwarding():
    phone_number = session.get('phone_number')
    if not phone_number:
        logger.error("Phone number not found in session.")
        return "Phone number not found in session. Please authenticate first.", 400

    if request.method == 'POST':
        source_chats = request.form.getlist('source_chats')
        destinations = request.form.getlist('destinations')
        keywords = request.form['keywords'].split(',')
        keyword_timer = request.form['keyword_timer']

        solana_enabled = 'solana_enabled' in request.form
        solana_source_chats = request.form.getlist('solana_source_chats') if solana_enabled else []
        solana_destinations = request.form.getlist('solana_destinations') if solana_enabled else []
        solana_timer = request.form['solana_timer'] if solana_enabled else None

        eth_enabled = 'eth_enabled' in request.form
        eth_source_chats = request.form.getlist('eth_source_chats') if eth_enabled else []
        eth_destinations = request.form.getlist('eth_destinations') if eth_enabled else []
        eth_timer = request.form['eth_timer'] if eth_enabled else None

        cashtag_enabled = 'cashtag_enabled' in request.form
        cashtag_source_chats = request.form.getlist('cashtag_source_chats') if cashtag_enabled else []
        cashtag_destinations = request.form.getlist('cashtag_destinations') if cashtag_enabled else []
        cashtag_timer = request.form['cashtag_timer'] if cashtag_enabled else None

        config = read_credentials(phone_number)
        if not config:
            logger.error(f"Configuration not found for phone number: {phone_number}")
            return "Configuration not found for this phone number.", 400

        forwarder = TelegramForwarder(config['api_id'], config['api_hash'], config['phone_number'])

        def start_task(forward_function, *args):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(forward_function(*args))

        task_list = []

        if keywords:
            task_id = len(config.get('tasks', [])) + 1
            import threading
            threading.Thread(target=start_task, args=(forwarder.forward_messages_to_channel, phone_number, source_chats, destinations, keywords, keyword_timer), daemon=True).start()
            task_list.append({
                'task_id': task_id,
                'type': 'keywords',
                'source_chats': source_chats,
                'destinations': destinations,
                'keywords': keywords,
                'keyword_timer': keyword_timer
            })
            logger.info(f"Started keyword forwarding task with ID: {task_id}")

        if solana_enabled:
            task_id = len(config.get('tasks', [])) + 1
            threading.Thread(target=start_task, args=(forwarder.forward_messages_to_channel, phone_number, solana_source_chats, solana_destinations, [], True, None, None, solana_timer), daemon=True).start()
            task_list.append({
                'task_id': task_id,
                'type': 'solana',
                'source_chats': solana_source_chats,
                'destinations': solana_destinations,
                'solana_timer': solana_timer
            })
            logger.info(f"Started Solana forwarding task with ID: {task_id}")

        if eth_enabled:
            task_id = len(config.get('tasks', [])) + 1
            threading.Thread(target=start_task, args=(forwarder.forward_messages_to_channel, phone_number, eth_source_chats, eth_destinations, [], False, None, None, None, True, None, None, eth_timer), daemon=True).start()
            task_list.append({
                'task_id': task_id,
                'type': 'ethereum',
                'source_chats': eth_source_chats,
                'destinations': eth_destinations,
                'eth_timer': eth_timer
            })
            logger.info(f"Started Ethereum forwarding task with ID: {task_id}")

        if cashtag_enabled:
            task_id = len(config.get('tasks', [])) + 1
            threading.Thread(target=start_task, args=(forwarder.forward_messages_to_channel, phone_number, cashtag_source_chats, cashtag_destinations, [], False, None, None, None, False, None, None, None, True, None, None, cashtag_timer), daemon=True).start()
            task_list.append({
                'task_id': task_id,
                'type': 'cashtags',
                'source_chats': cashtag_source_chats,
                'destinations': cashtag_destinations,
                'cashtag_timer': cashtag_timer
            })
            logger.info(f"Started Cashtag forwarding task with ID: {task_id}")

        if 'tasks' not in config:
            config['tasks'] = []
        config['tasks'].extend(task_list)
        save_config(config, phone_number)

        return redirect(url_for('menu'))

    return render_template('start_forwarding.html')

@app.route('/stop_forwarding', methods=['GET', 'POST'])
def stop_forwarding():
    phone_number = session.get('phone_number')
    if not phone_number:
        logger.error("Phone number not found in session.")
        return "Phone number not found in session. Please authenticate first.", 400

    config = read_credentials(phone_number)
    if not config:
        logger.error(f"Configuration not found for phone number: {phone_number}")
        return "Configuration not found for this phone number.", 400

    forwarder = TelegramForwarder(config['api_id'], config['api_hash'], config['phone_number'])

    if request.method == 'POST':
        task_id = int(request.form['task_id'])
        task_list = config.get('tasks', [])

        if task_id < 1 or task_id > len(task_list):
            logger.error("Invalid task ID received during stop request.")
            return "Invalid task ID, please try again."

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(forwarder.stop_forwarding_job(task_id - 1))

        task_list.pop(task_id - 1)
        config['tasks'] = task_list
        save_config(config, phone_number)

        logger.info(f"Stopped forwarding task with ID: {task_id}")
        return redirect(url_for('menu'))

    task_list = config.get('tasks', [])
    if not task_list:
        logger.warning("No active forwarding tasks to stop.")
        return "No active forwarding tasks to stop."

    return render_template('stop_forwarding.html', tasks=task_list)

@app.route('/exit')
def exit():
    return render_template('goodbye.html')

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
