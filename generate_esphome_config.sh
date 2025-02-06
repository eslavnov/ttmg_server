#!/bin/bash

# Prompt the user for the device ID, HOST, and PORT
read -p "Enter your HAVPE device_id: " DEVICE_ID
read -p "Enter TTMG Server host (e.g., 127.0.0.1): " HOST
read -p "Enter TTMG Server port (e.g., 8888): " PORT

# Define variables
REPO_URL="https://github.com/esphome/home-assistant-voice-pe"
CLONE_DIR="home-assistant-voice-pe"
BASE_DEST_DIR="esphome_config/custom_components"  # Change this to the actual base destination
DEST_DIR="${BASE_DEST_DIR}/${DEVICE_ID}"  # Append device ID to the destination path

NABU_DIR_SOURCE="esphome/components/nabu"
VA_DIR_SOURCE="esphome/components/voice_assistant"

NABU_DIR_DEST="esphome_config/custom_components/${DEVICE_ID}/nabu"
VA_DIR_DEST="esphome_config/custom_components/${DEVICE_ID}/voice_assistant"
AUDIO_FILE_URL="http:\/\/${HOST}:${PORT}\/play\/${DEVICE_ID}.flac"

# Create the destination directory if it doesn't exist
mkdir -p "$DEST_DIR"

# Remove the existing clone directory if it exists
if [ -d "$CLONE_DIR" ]; then
    echo "Removing existing directory $CLONE_DIR..."
    rm -rf "$CLONE_DIR"
fi

# 1. Clone the GitHub repository
echo "Cloning repository..."
git clone "$REPO_URL" || { echo "Failed to clone repo"; exit 1; }

# 2. Copy the required directories
cp -r "$CLONE_DIR/$NABU_DIR_SOURCE" "$NABU_DIR_DEST"
cp -r "$CLONE_DIR/$VA_DIR_SOURCE" "$VA_DIR_DEST"

# 4. Modify `audio_reader.cpp`
AUDIO_READER_FILE="$NABU_DIR_DEST/audio_reader.cpp"

echo "Checking path: $AUDIO_READER_FILE"
ls -larth

if [[ -f "$AUDIO_READER_FILE" ]]; then
    echo "Modifying timeout in audio_reader.cpp..."
    sed -i 's/client_config.timeout_ms = 5000;/client_config.timeout_ms = 15000;/g' "$AUDIO_READER_FILE"
else
    echo "File $AUDIO_READER_FILE not found!"
    exit 1
fi

# 5. Modify `voice_assistant.cpp`
VOICE_ASSISTANT_FILE="$VA_DIR_DEST/voice_assistant.cpp"

if [[ -f "$VOICE_ASSISTANT_FILE" ]]; then
    echo "Modifying TTS URL in voice_assistant.cpp..."

    # Use Perl for multi-line replacement, ensuring proper whitespace handling
    perl -0777 -i -pe "s/
      for \(auto arg : msg\.data\) \{
        if \(arg\.name == \"url\"\) \{
          url = std::move\(arg\.value\);
        \}
      \}/
      url = \"$AUDIO_FILE_URL\";
/gs" "$VOICE_ASSISTANT_FILE"

else
    echo "File $VOICE_ASSISTANT_FILE not found!"
    exit 1
fi

# Get the absolute path of the current working directory
ABSOLUTE_PATH=$(pwd)

echo "Script execution completed successfully! Files are modified at: $ABSOLUTE_PATH/$BASE_DEST_DIR"