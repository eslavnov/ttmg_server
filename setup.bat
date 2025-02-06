@echo off

:: Create a virtual environment
python -m venv venv

:: Activate the virtual environment
call venv\scripts\activate

:: Install the requirements
pip install -r requirements.txt

echo Setup complete. Virtual environment created and dependencies installed.
