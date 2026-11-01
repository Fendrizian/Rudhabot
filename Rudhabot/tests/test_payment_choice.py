"""Every purchase can be paid from the wallet or on the credit card (never from the bank balance)."""
from rudhabot.services import gacha
from rudhabot.views.cafe import CafeBuyView, CafeQuantityModal
from rudhabot.views.payment import PayToggle
from rudhabot.views.shop import BuyButton, ItemRow
from tests.cog_case import CogTestCase
from tests.fakes import FakeInteraction


class PaymentChoiceTests(CogTestCase):
    def _owed(self, user):
        return self.s.bank.account(user.id).get("credit", {}).get("balance", 0)

    async def test_shop_toggle_switches_buy_buttons_to_the_card(self):
        self.fund(self.alice, 0, bank=10_000)
        view = ItemRow(self.s, [("baseball cap", 50)], 0, 1, self.alice.id)
        toggle = next(c for c in view.children if isinstance(c, PayToggle))
        await toggle.callback(FakeInteraction(self.bot, self.alice))
        self.assertEqual(view.pay_with, "card")
        self.assertIn("Credit card", toggle.label)
        button = next(c for c in view.children if isinstance(c, BuyButton))
        it = FakeInteraction(self.bot, self.alice)
        await button.callback(it)
        self.assertIn("credit card", it.text())
        account = self.account(self.alice)
        self.assertEqual((account["wallet"], account["bank"]), (0, 10_000))     # neither wallet nor bank paid
        self.assertEqual(account["inventory"], {"baseball cap": 1})
        self.assertEqual(self._owed(self.alice), 50)

    async def test_shop_buttons_still_default_to_the_wallet(self):
        self.fund(self.alice, 100)
        view = ItemRow(self.s, [("baseball cap", 50)], 0, 1, self.alice.id)
        await next(c for c in view.children if isinstance(c, BuyButton)).callback(FakeInteraction(self.bot, self.alice))
        self.assertEqual(self.account(self.alice)["wallet"], 50)
        self.assertEqual(self._owed(self.alice), 0)

    async def test_cafe_order_on_the_card_and_a_declined_card(self):
        view = CafeBuyView(self.s, self.alice.id, "food", pay_with="card")
        modal = CafeQuantityModal(self.s, "toast", view.pay_with)
        modal.quantity._value = "3"
        it = FakeInteraction(self.bot, self.alice)
        await modal.on_submit(it)
        self.assertIn("credit card", it.text())
        self.assertEqual(self._owed(self.alice), 30)
        too_much = CafeQuantityModal(self.s, "pancakes", "card")
        too_much.quantity._value = "1000"                                  # 80,000 > the red card's 2,000 limit
        it = FakeInteraction(self.bot, self.alice)
        await too_much.on_submit(it)
        self.assertNotIn("You bought", it.text())
        self.assertEqual(self._owed(self.alice), 30)

    async def test_menu_switch_keeps_the_payment_choice(self):
        from rudhabot.views.cafe import MenuSwitchButton
        view = CafeBuyView(self.s, self.alice.id, "food", pay_with="card")
        switch = next(c for c in view.children if isinstance(c, MenuSwitchButton) and c.menu_type == "beverages")
        it = FakeInteraction(self.bot, self.alice)
        await switch.callback(it)
        self.assertEqual(it.log[-1][1]["view"].pay_with, "card")

    async def test_tcg_pack_on_the_card(self):
        pulled = self.s.tcg.open_pack(self.alice.id, "card")
        self.assertEqual(len(pulled), self.s.tcg.terms.pack_size)
        self.assertEqual(self._owed(self.alice), self.s.tcg.terms.pack_price)
        self.assertEqual(self.account(self.alice)["wallet"], 0)

    def test_shards_cost_ten_ra(self):
        self.assertEqual(gacha.SHARD_PRICE, 10)
