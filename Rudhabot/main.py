"""Entry point: `DISCORD_TOKEN=... python main.py`. All bot logic lives in the `rudhabot` package."""
from rudhabot.app import RudhaBot
from rudhabot.settings import read_token


def main() -> None:
    RudhaBot().run(read_token())


if __name__ == "__main__":
    main()
