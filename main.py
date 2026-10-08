import os
from pathlib import Path

import yaml
from dotenv import dotenv_values
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULTS = {
    "port": 8000,
    "workers": 1,
    "debug": False,
    "log_level": "info",
    "api_key": "default-secret-000",
}

def load_config():
    config = DEFAULTS.copy()

    # config.development.yaml
    path = Path("config.development.yaml")
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
            config.update(data)

    # .env
    env = dotenv_values(".env")

    for key, value in env.items():
        if value is None:
            continue

        key = {
            "NUM_WORKERS": "workers",
            "APP_DEBUG": "debug",
            "APP_LOG_LEVEL": "log_level",
            "APP_API_KEY": "api_key",
        }.get(key, key.lower())

        config[key] = value

    # OS environment variables
    aliases = {
        "APP_LOG_LEVEL": "log_level",
        "APP_API_KEY": "api_key",
        "APP_DEBUG": "debug",
        "NUM_WORKERS": "workers",
    }

    for env_key, config_key in aliases.items():
        if env_key in os.environ:
            config[config_key] = os.environ[env_key]

    # Type coercion
    config["port"] = int(config["port"])
    config["workers"] = int(config["workers"])

    if isinstance(config["debug"], str):
        config["debug"] = config["debug"].lower() in {
            "true", "1", "yes", "on"
        }

    config["log_level"] = str(config["log_level"])

    return config


@app.get("/effective-config")
def effective_config(
    set: list[str] = Query(default=[])
):
    config = load_config()

    # CLI query parameters have highest precedence
    for override in set:
        if "=" not in override:
            continue

        key, value = override.split("=", 1)

        if key == "workers":
            config[key] = int(value)
        elif key == "port":
            config[key] = int(value)
        elif key == "debug":
            config[key] = value.lower() in {"true", "1", "yes", "on"}
        else:
            config[key] = value

    # Never expose the actual API key
    config["api_key"] = "****"

    return config
