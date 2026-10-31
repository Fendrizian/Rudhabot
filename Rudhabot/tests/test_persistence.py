import json
import os

from rudhabot.persistence.json_store import JsonStore
from datetime import datetime, timezone

from tests.helpers import EnvTestCase

NOW_UTC = datetime(2026, 3, 15, 12, tzinfo=timezone.utc)


class JsonStoreTests(EnvTestCase):
    def test_missing_file_is_created_from_default(self):
        store = JsonStore(os.path.join(self.env.directory, "x.json"), lambda: {"a": 1})
        self.assertEqual(store.load(), {"a": 1})
        self.assertTrue(os.path.exists(store.path))

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        store = JsonStore(os.path.join(self.env.directory, "y.json"), dict)
        store.save({"k": "v"})
        with open(store.path) as handle:
            self.assertEqual(json.load(handle), {"k": "v"})
        self.assertFalse(os.path.exists(store.path + ".tmp"))

    def test_corrupt_file_is_backed_up_not_destroyed(self):
        path = os.path.join(self.env.directory, "z.json")
        with open(path, "w") as handle:
            handle.write("{not json")
        store = JsonStore(path, dict)
        self.assertEqual(store.load(), {})
        backups = [f for f in os.listdir(self.env.directory) if f.startswith("z.json.corrupt-")]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.env.directory, backups[0])) as handle:
            self.assertEqual(handle.read(), "{not json")

    def test_empty_file_yields_default_without_rewriting(self):
        path = os.path.join(self.env.directory, "e.json")
        with open(path, "w") as handle:
            handle.write("  \n")
        self.assertEqual(JsonStore(path, dict).load(), {})


class UserRepositoryTests(EnvTestCase):
    def test_get_creates_a_standard_account(self):
        account = self.env.users.get(42)
        self.assertEqual((account["wallet"], account["bank"], account["inventory"], account["loan"]), (0, 0, {}, None))
        self.assertEqual(set(account["crypto"]), {"SCAY", "RDC", "LICH", "WNC"})
        self.assertIn("42", self.env.users.all())

    def test_update_only_changes_the_fields_you_pass(self):
        self.env.users.update(1, wallet=500, loan={"principal": 100})
        self.env.users.update(1, wallet=700)                       # an unrelated update must NOT erase the loan
        account = self.env.users.get(1)
        self.assertEqual(account["wallet"], 700)
        self.assertEqual(account["loan"], {"principal": 100})

    def test_update_can_explicitly_clear_a_loan(self):
        self.env.users.update(1, loan={"principal": 100})
        self.env.users.update(1, loan=None)
        self.assertIsNone(self.env.users.get(1)["loan"])

    def test_update_rejects_unknown_fields(self):
        with self.assertRaises(TypeError):
            self.env.users.update(1, walet=5)

    def test_wallet_and_bank_are_stored_as_integers(self):
        self.env.users.update(1, wallet=10.9, bank="7")
        account = self.env.users.get(1)
        self.assertEqual((account["wallet"], account["bank"]), (10, 7))

    def test_transaction_saves_on_success_and_rolls_back_on_error(self):
        self.env.users.update(1, wallet=100)
        with self.env.users.transaction() as accounts:
            accounts["1"]["wallet"] = 250
        self.assertEqual(self.env.users.get(1)["wallet"], 250)
        with self.assertRaises(RuntimeError):
            with self.env.users.transaction() as accounts:
                accounts["1"]["wallet"] = 999
                raise RuntimeError("boom")
        self.assertEqual(self.env.users.get(1)["wallet"], 250)

    def test_consume_item(self):
        self.env.users.update(1, inventory={"boba": 2})
        self.assertTrue(self.env.users.consume_item(1, "boba", 1))
        self.assertFalse(self.env.users.consume_item(1, "boba", 5))
        self.assertEqual(self.env.users.get(1)["inventory"], {"boba": 1})
        self.assertTrue(self.env.users.consume_item(1, "boba", 1))
        self.assertEqual(self.env.users.get(1)["inventory"], {})

    def test_credit_wallet(self):
        self.env.users.update(1, wallet=10)
        self.assertEqual(self.env.users.credit_wallet(1, 25), 35)
        self.assertEqual(self.env.users.credit_wallet(1, -5), 30)

    def test_monthly_special_purchase_tracking(self):
        self.assertFalse(self.env.users.has_purchased_special(9, "pog juice", 3))
        self.env.users.record_special_purchase(9, "pog juice", 3)
        self.assertTrue(self.env.users.has_purchased_special(9, "pog juice", 3))
        self.assertFalse(self.env.users.has_purchased_special(9, "pog juice", 4))


class LegacyDataCompatibilityTests(EnvTestCase):
    def test_old_cake_log_entries_load_and_are_never_redelivered(self):
        """birthdays.json written by the original bot has no id/send_at/status."""
        legacy = {"scheduled_by": 1, "scheduled_for": 2, "date": "01/01/2026", "time": "10:00",
                  "message": "hi", "scheduled_at": "2025-12-31T00:00:00"}
        self.env.celebrations._store.save([legacy])
        self.assertEqual(self.env.celebrations.all(), [legacy])
        self.assertEqual(self.env.celebrations.due(NOW_UTC), [])             # legacy rows count as already handled
        self.env.celebrations.schedule({"scheduled_by": 3}, NOW_UTC)
        self.assertEqual(len(self.env.celebrations.all()), 2)                # new rows are appended, old ones kept
        self.assertEqual(self.env.celebrations.all()[0], legacy)

    def test_accounts_written_by_the_original_bot_are_read_and_upgraded(self):
        old = {"123": {"wallet": 5.0, "bank": "7", "inventory": {"boba": 2}, "loan": None}}      # no crypto / flags
        self.env.users._store.save(old)
        account = self.env.users.get(123)
        self.assertEqual((account["wallet"], account["bank"], account["inventory"]), (5, 7, {"boba": 2}))
        self.assertIn("crypto", account)
        self.assertFalse(account["loan_warning_sent"])
