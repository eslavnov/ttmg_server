import os
import shutil
import subprocess
import re

# Prompt user for inputs
DEVICE_ID = input("Enter your HAVPE device_id: ").strip()
HOST = input("Enter TTMG Server host (e.g., 127.0.0.1): ").strip()
PORT = input("Enter TTMG Server port (e.g., 8888): ").strip()

# Define variables
REPO_URL = "https://github.com/esphome/home-assistant-voice-pe"
CLONE_DIR = "home-assistant-voice-pe"
BASE_DEST_DIR = "esphome_config/custom_components"  # Change this to the actual base destination
DEST_DIR = os.path.join(BASE_DEST_DIR, DEVICE_ID)

NABU_DIR_SOURCE = os.path.join(CLONE_DIR, "esphome/components/nabu")
VA_DIR_SOURCE = os.path.join(CLONE_DIR, "esphome/components/voice_assistant")

NABU_DIR_DEST = os.path.join(DEST_DIR, "nabu")
VA_DIR_DEST = os.path.join(DEST_DIR, "voice_assistant")

AUDIO_FILE_URL = f"http://{HOST}:{PORT}/play/{DEVICE_ID}.flac"

# Create the destination directory if it doesn't exist
os.makedirs(DEST_DIR, exist_ok=True)

# Remove the existing clone directory if it exists
if os.path.isdir(CLONE_DIR):
    print(f"Removing existing directory {CLONE_DIR}...")
    shutil.rmtree(CLONE_DIR)

# 1. Clone the GitHub repository
print("Cloning repository...")
try:
    subprocess.run(["git", "clone", REPO_URL], check=True)
except subprocess.CalledProcessError:
    print("Failed to clone repository")
    exit(1)

# 2. Copy the required directories
shutil.copytree(NABU_DIR_SOURCE, NABU_DIR_DEST, dirs_exist_ok=True)
shutil.copytree(VA_DIR_SOURCE, VA_DIR_DEST, dirs_exist_ok=True)

# 4. Modify `audio_reader.cpp`
AUDIO_READER_FILE = os.path.join(NABU_DIR_DEST, "audio_reader.cpp")

print(f"Checking path: {AUDIO_READER_FILE}")
if os.path.isfile(AUDIO_READER_FILE):
    print("Modifying timeout in audio_reader.cpp...")
    with open(AUDIO_READER_FILE, "r") as file:
        content = file.read()
    
    # Replace timeout value
    content = content.replace("client_config.timeout_ms = 5000;", "client_config.timeout_ms = 15000;")
    
    with open(AUDIO_READER_FILE, "w") as file:
        file.write(content)
else:
    print(f"File {AUDIO_READER_FILE} not found!")
    exit(1)

# 5. Modify `voice_assistant.cpp`
VOICE_ASSISTANT_FILE = os.path.join(VA_DIR_DEST, "voice_assistant.cpp")

if os.path.isfile(VOICE_ASSISTANT_FILE):
    print("Modifying TTS URL in voice_assistant.cpp...")
    
    with open(VOICE_ASSISTANT_FILE, "r") as file:
        content = file.read()
    
    # Regex to replace the URL assignment block
    pattern = r"""
      for\s*\(auto\s*arg\s*:\s*msg\.data\s*\)\s*\{  # Matches 'for (auto arg : msg.data) {'
        \s*if\s*\(arg\.name\s*==\s*"url"\)\s*\{   # Matches 'if (arg.name == "url") {'
          \s*url\s*=\s*std::move\(arg\.value\);   # Matches 'url = std::move(arg.value);'
        \s*\}                                      # Matches '}'
      \s*\}                                        # Matches '}'
    """

    # Replace the block with a new URL assignment
    replacement = f"""
      url = "{AUDIO_FILE_URL}";
    """

    content = re.sub(pattern, replacement, content, flags=re.MULTILINE | re.VERBOSE)

    with open(VOICE_ASSISTANT_FILE, "w") as file:
        file.write(content)

else:
    print(f"File {VOICE_ASSISTANT_FILE} not found!")
    exit(1)

# Get the absolute path of the current working directory
ABSOLUTE_PATH = os.getcwd()

print(f"Script execution completed successfully! Files are modified at: {ABSOLUTE_PATH}/{BASE_DEST_DIR}")
