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
BANKICON = "<:bank:1566106240495390750>"
CLAIRVOYANCEICON = "<:Clairvoyance:1566097544558411916>"
PORTFOLIOICON = "<:portfolio:1566239179040628958>"
LEADERBOARDICON = "<:leaderboard:1566239719182958683>"
LOANAUTOICON = "<:loan_auto:1566240694459433010>"
LOANBANKRUPTICON = "<:loan_bankrupt:1566241115542126664>"

# game title icons (blackjack and roulette keep theirs inline)
SLOTSICON = "<:roulette_table:1399759017932361809>"
DICEICON = "<:dicegame:1566101279619416125>"
HIGHLOWICON = "<:highlow:1566102265545101422>"
CRASHICON = "<:crashgame:1566102590511521842>"
BACCARATICON = "<:baccarat:1566102948193509406>"
CRAPSICON = "<:craps:1566100945207693363>"


class TCG:
    """Asgrelia: Cloudfall iconography (game symbols only: cards have no illustrations)."""
    LOGO = "<:Cloudfall:1566111702427238521>"
    CARD_BACK = "<:TCG_cardback:1566112821970731128>"
    PACK = "<:TCG_pack:1566111704364883998>"
    VAPOR = "<:TCG_vapor:1566116502774616195>"
    TAINTED = "<:TCG_tainted:1566246023486119997>"
    CORRUPTED = "<:TCG_corrupted:1566245773044220065>"
    THREAT = "<:TCG_threat:1566117811305054208>"
    RESOLVE_HIGH = "<:TCG_resolvehigh:1566115222425440306>"
    RESOLVE_MID = "<:TCG_resolvemid:1566115206503997510>"
    RESOLVE_LOW = "<:TCG_resolvelow:1566115191601627206>"
    RESOLVE_DEAD = "<:TCG_resolvedead:1566115174564499756>"
    SHARD = "<:TCG_shard:1566241621526315119>"           # Cloud Shards (wish currency)
    DUST = "<:TCG_dust:1566242096879509680>"             # Vapor Dust (crafting currency)
    STAR = "<:TCG_star:1566242729074233515>"             # wish rarity
    GRADES = {1: "<:TCG_cloud1:1566244166181322752>", 2: "<:TCG_cloud2:1566244168874070036>",
              3: "<:TCG_cloud3:1566244171042521098>", 4: "<:TCG_cloud4:1566244173034557480>",
              5: "<:TCG_cloud5:1566244174989099120>"}  # Cloud Grade pips, 1 to 5

    @classmethod
    def stars(cls, n: int) -> str:
        """A pull's rarity as a row of stars."""
        return cls.STAR * n

    @classmethod
    def rarity(cls, n: int) -> str:
        """Compact rarity label for running text, e.g. 5 + star."""
        return f"{n}{cls.STAR}"

    @classmethod
    def resolve(cls, value: int) -> str:
        """Resolve icon by how much is left: 21+ high, 11-20 mid, 1-10 low, 0 dead (the end-of-game board)."""
        return (cls.RESOLVE_HIGH if value >= 21 else cls.RESOLVE_MID if value >= 11
                else cls.RESOLVE_LOW if value >= 1 else cls.RESOLVE_DEAD)


# Components (buttons, select options) keep Unicode emojis: Discord rejects the whole message with
# "Invalid emoji" if a custom emoji there can't be resolved, while text just degrades to :name:.
COMPONENT_FALLBACK = {TCG.LOGO: "⛅"}


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
