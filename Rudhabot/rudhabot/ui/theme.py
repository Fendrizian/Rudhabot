"""Presentation constants: emoji/icons and embed colours."""

CURRENCY = "<:Rudhanese_Arch:1053507684797780039>"
BOTICON = "<:rudhabot:1401456647825653802>"
CAFEICON = "<:cafe:1399370260347355198>"
SHOPICON = "<:shop:1399027355409973361>"
INVICON = "<:inventory:1418141016262971442>"
BOXICON = "<:mystery_box:1418910336265879613>"
STOCKSICON = "<:stocks:1401378607368241273>"
CAKEICON = "<:birthday_cake:1417887324754284656>"
LOANDUEICON = "<:loan_due:1401358370191183902>"
COINICON = "<:coinF_heads:1398951529955852328>"
CARDBACKICON = "<:card_back:1413016849515417691>"


class Colors:
    """The house embed palette. Every embed colour comes from here (`tests/test_colors.py` enforces it)."""
    # economy
    STOCK_MARKET = 0x1ABC9C
    PORTFOLIO = 0x11806A
    CRYPTO_MARKET = 0xFEE75C
    BANK = 0x1F8B4C
    DEPOSIT = 0x206694
    WITHDRAW = 0x3498DB
    BALANCE = 0xAD1457
    INCOME = 0xF1C40F
    LEADERBOARD = 0xE91E63
    # loans
    LOAN = 0x71368A
    EXTEND = 0x9B59B6
    LOAN_STATUS = 0xE74C3C
    LOAN_PARTIAL = 0x2ECC71
    # shop, cafe, items
    SHOP = 0xEB459E
    CAFE = 0xE67E22
    INVENTORY = 0xA84300
    SEARCH_ITEM = 0xC27C0E
    MYSTERY_BOX = 0x7289DA
    BIRTHDAY = 0x7289DA
    # outcomes and states
    SUCCESS = 0x57F287          # purchase & success
    LOSS = 0xED4245             # fine & loss
    FAIL = 0x992D22
    COOLDOWN = 0x979C9F         # cooldown / timeout
    UNAVAILABLE = 0x546E7A      # someone else's menu, no permission
    NOTIFICATIONS = 0x95A5A6
    GAMBLING = 0x99AAB5
    HELP = 0x607D8B
    CLAIRVOYANCE = 0x5865F2     # technical matters only: model status, review signals (and /ask for now)
    # Asgrelia: Cloudfall, darkest to brightest
    TCG_DEEP = 0x0A288A
    TCG_BLUE = 0x1653C4
    TCG_SKY = 0x7F93DA
    TCG_MIST = 0xA0A9DF
    TCG_CLOUD = 0xEDEEF8



ROULETTE_RED = 0xDC143C     # the wheel's own colours: thematic, outside the palette on purpose
ROULETTE_BLACK = 0x2C2C2C
ROULETTE_GREEN = 0x00FF00
