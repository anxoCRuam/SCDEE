"""
Paths and environment reader shared by all settings modules.
"""

from pathlib import Path

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# Read .env file if it exists (it won't in production containers
# where env vars are injected directly).
env_file = BASE_DIR / ".env"
if env_file.exists():
    env.read_env(str(env_file))
