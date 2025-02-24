import os
import shutil
import subprocess
import re

# Prompt user for inputs
DEVICE_ID = input("Enter your HAVPE device_id: ").strip()
HOST = input("Enter TTMG Server host (e.g., 192.168.201.255): ").strip()
PORT = input("Enter TTMG Server port (e.g., 8888): ").strip()
AUDIO_FORMAT = input('Select audio format.Enter "flac" (default) or "mp3" (experimental, faster): ').strip()

# Define variables
REPO_URL = "https://github.com/esphome/home-assistant-voice-pe"
CLONE_DIR = "home-assistant-voice-pe"
BASE_DEST_DIR = "esphome_config/custom_components"
DEST_DIR = os.path.join(BASE_DEST_DIR, DEVICE_ID)

VA_DIR_SOURCE = os.path.join(CLONE_DIR, "esphome/components/voice_assistant")
VA_DIR_DEST = os.path.join(DEST_DIR, "voice_assistant")

AUDIO_FILE_URL = f"http://{HOST}:{PORT}/play/{DEVICE_ID}.{AUDIO_FORMAT}"

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
shutil.copytree(VA_DIR_SOURCE, VA_DIR_DEST, dirs_exist_ok=True)

# 3. Modify `voice_assistant.cpp`
VOICE_ASSISTANT_FILE = os.path.join(VA_DIR_DEST, "voice_assistant.cpp")

if os.path.isfile(VOICE_ASSISTANT_FILE):
    print("Modifying TTS URL in voice_assistant.cpp...")
    
    with open(VOICE_ASSISTANT_FILE, "r") as file:
        content = file.read()
        
    # Replace timeout value
    print("Increasing speaker-timeout")
    content = content.replace('"speaker-timeout", 5000', '"speaker-timeout", 15000')
    
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

print(f"All done! ESPHome custom components for device {DEVICE_ID} are at: \n{ABSOLUTE_PATH}/{BASE_DEST_DIR}/{DEVICE_ID}/")