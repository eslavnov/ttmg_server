@echo off

:: Create a virtual environment
python -m venv venv

:: Activate the virtual environment
call venv\scripts\activate

:: Install the requirements
pip install -r requirements.txt

:: Create config
if not exist "configuration.json" (
  copy "configuration_example.json" "configuration.json"
)

echo Setup complete. Virtual environment created and dependencies installed.
