"""Pay-with toggle for button-driven shops: wallet or credit card. The bank balance never pays for purchases."""
from __future__ import annotations

from discord import ButtonStyle, Interaction

from rudhabot.views.base import ServiceButton

PAY_LABELS = {"wallet": ("Paying with: Wallet", "👛"), "card": ("Paying with: Credit card", "💳")}


def other(pay_with: str) -> str:
    return "card" if pay_with == "wallet" else "wallet"


class PayToggle(ServiceButton):
    """Flips the owning view's `pay_with` between wallet and card; purchases made from that view use it."""

    def __init__(self, pay_with: str = "wallet"):
        label, emoji = PAY_LABELS[pay_with]
        super().__init__(label=label, emoji=emoji, style=ButtonStyle.secondary)

    async def callback(self, interaction: Interaction):
        view = self.view
        view.pay_with = other(view.pay_with)
        self.label, self.emoji = PAY_LABELS[view.pay_with]
        await interaction.response.edit_message(view=view)


async def refuse_if_in_game(services, interaction: Interaction) -> bool:
    """Shop and café buttons outlive the command that showed them, so they repeat its "not during a game" check:
    spending while a bet is open would let a lost bet go uncollected. True if refused."""
    if interaction.user.id in services.sessions:
        from rudhabot.ui.messages import MSG_ONGAME
        from rudhabot.ui.replies import error_embed
        await interaction.response.send_message(embed=error_embed(MSG_ONGAME, interaction.user), ephemeral=True)
        return True
    return False


def paid_suffix(pay_with: str) -> str:
    return " on your credit card 💳" if pay_with == "card" else ""
