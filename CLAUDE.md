# Rudhabot: working notes

## House rules
- **`/help` lists commands alphabetically** (case-insensitive), in every section, the full list and the overview samples.
  `alphabetical()` in `Rudhabot/rudhabot/views/help.py` does the sorting, and `tests/test_help.py` enforces it. New
  commands just need an entry in `get_commands_data()` and a section in `SECTIONS`, with no manual ordering.
  `test_every_registered_slash_command_is_documented` fails if a command is missing from help.
- `/help` keeps the bot icon (`BOTICON`) in its title. **Only the main help page (the overview) shows the Digital
  Research Institute banner** (`rudhabot/assets/dri.png`: the wide 960×540 red version with a transparent background).
  It is uploaded as `attachment://dri.png`. Section pages and the full list have no image, and navigation sets the
  message's attachments explicitly (`_show` in `views/help.py`).

## Checks
- Run the tests from `Rudhabot/` (offline, no Discord or OpenAI calls):

  ```bash
  python -m unittest discover -s tests -t .
  ```
