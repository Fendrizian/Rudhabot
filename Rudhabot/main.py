"""Entry point: `python main.py`. Settings come from the environment, or from a `.env` file next to this script
(see .env.example). All bot logic lives in the `rudhabot` package."""
import logging
import os

from rudhabot.settings import load_env_file, read_token


def main() -> None:
    loaded = load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")) + load_env_file(".env")
    from rudhabot.ml import single_threaded_blas
    single_threaded_blas()                        # before numpy loads: tiny matrices run faster on one thread
    level = os.environ.get("RUDHABOT_LOG_LEVEL", "INFO").strip().upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if loaded:
        logging.getLogger("rudhabot").info("loaded from .env: %s", ", ".join(sorted(set(loaded))))
    from rudhabot.errors import MissingTokenError
    try:
        token = read_token()                       # before anything is built: no data files for a bot that can't start
    except MissingTokenError as error:
        logging.getLogger("rudhabot").error("%s", error)
        raise SystemExit(1) from None
    from rudhabot.app import RudhaBot             # imported after .env so settings see those variables
    RudhaBot().run(token, log_handler=None)


if __name__ == "__main__":
    main()
