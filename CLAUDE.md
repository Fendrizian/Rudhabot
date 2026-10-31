# Rudhabot: working notes

## House rules
- **`/help` lists commands alphabetically** (case-insensitive), in every section, the full list and the overview samples.
  `alphabetical()` in `Rudhabot/rudhabot/views/help.py` does the sorting, and `tests/test_help.py` enforces it. New
  commands just need an entry in `get_commands_data()` and a section in `SECTIONS`, with no manual ordering.
  `test_every_registered_slash_command_is_documented` fails if a command is missing from help.
- The `/help` title icon is `HELPICON` (`<:DRI:1566010680799264789>`) in `rudhabot/ui/theme.py`.

## Checks
- Run the tests from `Rudhabot/` (offline, no Discord or OpenAI calls):

  ```bash
  python -m unittest discover -s tests -t .
  ```
