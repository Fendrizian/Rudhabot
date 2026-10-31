"""Entry point: `python main.py`. Settings come from the environment, or from a `.env` file next to this script
(see .env.example). All bot logic lives in the `rudhabot` package."""
import logging
import os

from rudhabot.settings import load_env_file, read_token


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    loaded = load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")) + load_env_file(".env")
    if loaded:
        logging.getLogger("rudhabot").info("loaded from .env: %s", ", ".join(sorted(set(loaded))))
    from rudhabot.app import RudhaBot             # imported after .env so settings see those variables
    RudhaBot().run(read_token(), log_handler=None)


if __name__ == "__main__":
    main()
