# Rudhabot: working notes

## House rules
- **`/help` lists commands alphabetically** (case-insensitive), in every section, the full list and the overview samples.
  `alphabetical()` in `Rudhabot/rudhabot/views/help.py` does the sorting, and `tests/test_help.py` enforces it. New
  commands just need an entry in `get_commands_data()` and a section in `SECTIONS`, with no manual ordering.
  `test_every_registered_slash_command_is_documented` fails if a command is missing from help.
- `/help` keeps the bot icon (`BOTICON`) in its title. Every help page embeds the Digital Research Institute emblem
  (`rudhabot/assets/dri.png`) as a full-size image, uploaded with the message as `attachment://dri.png`.

## Checks
- Run the tests from `Rudhabot/` (offline, no Discord or OpenAI calls):

  ```bash
  python -m unittest discover -s tests -t .
  ```
