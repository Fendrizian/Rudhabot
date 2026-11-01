"""The explicit list of cogs the bot loads, in order."""
from rudhabot.cogs.admin import AdminCog
from rudhabot.cogs.assistant import AssistantCog
from rudhabot.cogs.audit import AuditCog
from rudhabot.cogs.bank import BankCog
from rudhabot.cogs.banker import BankerCog
from rudhabot.cogs.birthdays import BirthdayCog
from rudhabot.cogs.crypto import CryptoCog
from rudhabot.cogs.economy import EconomyCog
from rudhabot.cogs.gambling import GamblingCog
from rudhabot.cogs.help import HelpCog
from rudhabot.cogs.loans import LoansCog
from rudhabot.cogs.lottery import LotteryCog
from rudhabot.cogs.clairvoyance import ClairvoyanceCog
from rudhabot.cogs.shop import ShopCog
from rudhabot.cogs.stocks import StocksCog
from rudhabot.cogs.tcg import TcgCog

ALL_COGS = (EconomyCog, ShopCog, LoansCog, CryptoCog, GamblingCog, LotteryCog, BirthdayCog, AdminCog, HelpCog,
            TcgCog, AssistantCog, ClairvoyanceCog, BankCog, StocksCog, AuditCog, BankerCog)
