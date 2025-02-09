#!/bin/bash

# Create a virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Install the requirements
pip install -r requirements.txt

# Create config
if [ ! -f configuration.json ]; then
  cp configuration_example.json configuration.json
fi

echo "Setup complete. Virtual environment created and dependencies installed."
