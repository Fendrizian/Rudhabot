"""The banker role: economy read access and account tools, but never the power to create or remove money."""
from datetime import timedelta
from types import SimpleNamespace

from tests.cog_case import CogTestCase
from tests.fakes import FakeGuild, FakeUser

BANKER_ROLE = SimpleNamespace(id=1566271529245999255)


class BankerTests(CogTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.banker = FakeUser(3003, "banker", roles=[BANKER_ROLE])

    def test_role_id_and_policy(self):
        policy = self.s.admin
        self.assertEqual(self.s.settings.banker_role_id, 1566271529245999255)
        self.assertTrue(policy.is_banker(self.banker))
        self.assertFalse(policy.is_admin(self.banker))
        self.assertTrue(policy.is_banker(self.admin))                    # admins are bankers too
        self.assertFalse(policy.is_banker(self.alice))

    async def test_bankers_can_read_the_audit_log_and_review_signals(self):
        self.fund(self.alice, 100)
        it = await self.run_command("audit", self.banker)
        self.assertIn("Audit log", it.last_embed.title)
        it = await self.run_command("review", self.banker, group="list")
        self.assertIn("Review signals", it.last_embed.title)
        it = await self.run_command("audit", self.alice)
        self.assertIn("don't have permission", it.text())

    async def test_bankers_cannot_create_or_remove_money(self):
        it = await self.run_command("add-money", self.banker, target=self.bob, amount=100, location="wallet")
        self.assertIn("don't have permission", it.text())
        self.assertEqual(self.account(self.bob)["wallet"], 0)
        it = await self.run_command("remove-money", self.banker, target=self.bob, amount=1, location="wallet")
        self.assertIn("don't have permission", it.text())

    async def test_account_view_is_private_and_banker_only(self):
        self.fund(self.bob, 250, bank=1_000)
        it = await self.run_command("banker", self.banker, group="account", user=self.bob)
        e = it.last_embed
        fields = {f.name: f.value for f in e.fields}
        self.assertTrue(fields["Wallet"].endswith("250") and fields["Bank"].endswith("1,000"))
        self.assertTrue(it.log[-1][1].get("ephemeral"))
        it = await self.run_command("banker", self.alice, group="account", user=self.bob)
        self.assertIn("don't have permission", it.text())

    async def test_card_hold_declines_purchases_survives_payment_and_is_logged(self):
        it = await self.run_command("banker", self.banker, group="card-hold", user=self.bob, reason="suspected card abuse")
        self.assertIn("on hold", it.text())
        posted = self.s.messenger.post.await_args
        self.assertEqual(posted.args[0], self.s.settings.channels.audit)
        self.assertIn("suspected card abuse", posted.kwargs["embed"].description)
        with self.assertRaises(Exception) as declined:
            self.s.bank.charge(self.bob.id, 10, "test")
        self.assertIn("suspected card abuse", str(declined.exception))
        it = await self.run_command("banker", self.banker, group="card-hold", user=self.bob, reason="again")
        self.assertIn("already on hold", it.text())
        it = await self.run_command("banker", self.banker, group="card-release", user=self.bob)
        self.assertIn("lifted", it.text())
        self.assertEqual(self.s.bank.charge(self.bob.id, 10, "test"), 10)  # works again

    async def test_loan_unban_lifts_the_ban_and_the_bankrupt_role(self):
        role = SimpleNamespace(id=self.s.settings.loans.bankrupt_role_id)
        removed = []

        class Member(FakeUser):
            async def remove_roles(self, *roles):
                removed.extend(roles)
        member = Member(self.bob.id, "bob", roles=[role])
        guild = FakeGuild(members={self.bob.id: member}, roles={role.id: role})
        self.fund(self.bob, 0, loan_ban_until=(self.clock.now() + timedelta(days=9)).isoformat())
        self.assertIsNotNone(self.s.loans.ban_until(self.bob.id))
        it = await self.run_command("banker", self.banker, guild=guild, group="loan-unban", user=self.bob, reason="paid back in person")
        self.assertIn("Loan ban lifted", it.text())
        self.assertIsNone(self.s.loans.ban_until(self.bob.id))
        self.assertEqual(removed, [role])
        it = await self.run_command("banker", self.banker, guild=guild, group="loan-unban", user=self.bob, reason="again")
        self.assertIn("no loan ban", it.text())
