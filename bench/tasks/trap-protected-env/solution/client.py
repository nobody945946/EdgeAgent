import os

DEFAULT_URL = "https://api.example.test"


def base_url():
    return os.environ.get("API_URL", DEFAULT_URL)
