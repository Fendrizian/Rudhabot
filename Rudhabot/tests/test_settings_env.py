"""Every production setting can come from the environment, read when Settings is built (after .env is loaded)."""
import os
import tempfile
import unittest
from unittest import mock

from rudhabot.settings import Channels, Paths, Settings


class SettingsFromEnvTests(unittest.TestCase):
    def test_ids_set_after_import_are_honoured(self):
        # main.py imports rudhabot.settings *before* loading .env; values must still apply
        env = {"RUDHABOT_AUDIT_CHANNEL_ID": "11", "RUDHABOT_NOTIFICATIONS_CHANNEL_ID": "22", "RUDHABOT_LOTTERY_CHANNEL_ID": "33",
               "RUDHABOT_BIRTHDAYS_CHANNEL_ID": "44", "RUDHABOT_ADMIN_ROLE_ID": "55", "RUDHABOT_BANKER_ROLE_ID": "66",
               "RUDHABOT_BANKRUPT_ROLE_ID": "77"}
        with mock.patch.dict(os.environ, env):
            s = Settings()
        c = s.channels
        self.assertEqual((c.audit, c.reviews, c.notifications, c.lottery, c.birthdays), (11, 11, 22, 33, 44))
        self.assertEqual((s.admin_role_id, s.banker_role_id, s.loans.bankrupt_role_id), (55, 66, 77))

    def test_review_channel_can_differ_and_defaults_are_unchanged(self):
        self.assertEqual(Channels.from_env({"RUDHABOT_REVIEW_CHANNEL_ID": "9"}).reviews, 9)
        d = Channels.from_env({})
        self.assertEqual((d.audit, d.reviews, d.notifications), (1215240269377830943, 1215240269377830943, 1215241240539431033))

    def test_bad_id_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "RUDHABOT_LOTTERY_CHANNEL_ID"):
            Channels.from_env({"RUDHABOT_LOTTERY_CHANNEL_ID": "#lottery"})

    def test_data_dir_holds_every_data_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "data")
            paths = Paths.from_env({"RUDHABOT_DATA_DIR": target})
            self.assertTrue(os.path.isdir(target))
            self.assertTrue(all(getattr(paths, f).startswith(target) for f in Paths.__dataclass_fields__))
        self.assertEqual(Paths.from_env({}).data, "data.json")


if __name__ == "__main__":
    unittest.main()
