"""Link and attachment analysis from the text and metadata alone: nothing is fetched, opened or downloaded.

For each URL: the host (punycode decoded), its registrable domain, and findings such as a look-alike of a protected
brand ("dlscord-nitro.gift"), punycode/homoglyph hosts, IP-literal hosts, `user@host` tricks, URL shorteners and IP
loggers, server deny/allow lists, and masked links whose visible text names a different domain. Attachments are judged
by filename, size and declared type only.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import urlsplit

from rudhabot.security.text import has_bidi_override, normalise, skeleton

URL_RE = re.compile(r"(?i)\b((?:https?://|www\.)[^\s<>()\[\]\"'`]+)")
MASKED_RE = re.compile(r"\[([^\]]{1,200})\]\(\s*<?(https?://[^\s)>]+)>?\s*\)")
INVITE_RE = re.compile(r"(?i)(?:https?://)?(?:www\.)?(?:discord(?:app)?\.(?:gg|io|me|li|com/invite)|dsc\.gg|invite\.gg|"
                       r"discord\.com/invite)/([a-z0-9-]{2,32})")
BARE_DOMAIN_RE = re.compile(r"(?i)\b([a-z0-9][a-z0-9-]{1,62}(?:\.[a-z0-9-]{1,62})*\.(?:gift|gg|com|net|org|ru|xyz|tk|ml|"
                            r"ga|cf|gq|top|click|link|io|me|shop|site|online|store|app|pw|cc|su|icu|cyou|sbs|buzz)"
                            r"/[^\s<>()]*)")

#: Brands commonly impersonated on Discord, with their official registrable domains.
PROTECTED = {
    "discord": ("discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media", "discord.gift",
                "discord.new", "discordstatus.com", "discord.dev", "discord.co", "dis.gd", "discord.store"),
    "steam": ("steampowered.com", "steamcommunity.com", "steamstatic.com", "steam.tv", "steamgames.com"),
    "roblox": ("roblox.com", "rbxcdn.com"),
    "epicgames": ("epicgames.com", "unrealengine.com"),
    "paypal": ("paypal.com", "paypal.me"),
    "github": ("github.com", "github.io", "githubusercontent.com"),
    "youtube": ("youtube.com", "youtu.be", "ytimg.com"),
    "twitch": ("twitch.tv",),
    "google": ("google.com", "goo.gl", "gstatic.com", "googleusercontent.com", "forms.gle"),
    "microsoft": ("microsoft.com", "live.com", "office.com", "xbox.com", "minecraft.net"),
    "apple": ("apple.com", "icloud.com"),
    "metamask": ("metamask.io",),
    "opensea": ("opensea.io",),
    "binance": ("binance.com",),
    "coinbase": ("coinbase.com",),
    "tenor": ("tenor.com",),
    "spotify": ("spotify.com", "spotify.link"),
}
OFFICIAL = {d for domains in PROTECTED.values() for d in domains}
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "rb.gy", "shorturl.at", "ow.ly", "buff.ly",
              "tiny.cc", "rebrand.ly", "t.ly", "s.id", "v.gd", "bit.do", "shorte.st", "adf.ly", "tiny.one", "u.to",
              "clck.ru", "qr.ae", "bl.ink", "short.io", "surl.li", "shorturl.asia", "linkr.it", "gg.gg"}
IP_LOGGERS = {"grabify.link", "iplogger.org", "iplogger.com", "iplogger.ru", "2no.co", "yip.su", "blasze.tk",
              "ps3cfw.com", "lovebird.guru", "trulove.guru", "dateing.club", "shrekis.life", "headshot.monster",
              "gaming-at-my.best", "progaming.monster", "yourmy.monster", "imageshare.best", "screenshot.best",
              "gamingfun.me", "catsnthing.com", "fortnitechat.site", "fortnight.space", "hondachat.com",
              "bvog.com", "youramonkey.com", "pronosparadise.com", "freegiftcards.co", "stopify.co", "leancoding.co"}
WEAK_TLDS = {"tk", "ml", "ga", "cf", "gq", "zip", "mov", "xyz", "top", "click", "buzz", "icu", "cyou", "sbs", "rest",
             "work", "cam", "monster", "best", "su", "pw"}
MULTI_PART_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.id", "or.id", "web.id", "my.id", "com.br",
                       "co.jp", "co.kr", "com.sg", "com.my", "co.nz", "co.in", "com.tr", "com.mx", "com.ar", "co.za",
                       "com.ph", "com.vn", "com.cn", "com.hk", "com.tw", "eu.org", "pages.dev", "web.app",
                       "github.io", "netlify.app", "vercel.app", "glitch.me", "repl.co", "herokuapp.com"}

LURES = {
    "money": re.compile(r"(?i)\b(free\s*nitro|nitro\s*(gift|free|giveaway|for\s*free)|steam\s*gift|gift\s*card|airdrop|"
                        r"claim\s*(now|your|reward|gift)|giveaway|you(\s*have)?\s*won|\$\s?\d{2,}|\d{2,}\s?\$|"
                        r"free\s*(skins?|robux|v-?bucks|crypto|money)|double\s*your|withdraw|mint\s*now|"
                        r"limited\s*(time|offer)|first\s*\d+\s*(people|users))"),
    "account": re.compile(r"(?i)\b(verify\s*(your)?\s*(account|age|identity)|account\s*(will\s*be\s*)?(disabled|"
                          r"banned|suspended|terminated)|security\s*check|login\s*to\s*(claim|verify)|"
                          r"scan\s*(the|this)?\s*qr|reported\s*your\s*account|accidentally\s*reported)"),
    "adult": re.compile(r"(?i)\b(18\+|nsfw\s*(server|content|leaks?)|onlyfans|leaked\s*(pics|videos?)|nudes?\b|"
                        r"e-?girls?|teen\s*leaks)"),
}

DANGEROUS_EXTENSIONS = {"exe", "scr", "bat", "cmd", "com", "pif", "vbs", "vbe", "js", "jse", "wsf", "wsh", "msi", "msp",
                        "ps1", "psm1", "jar", "apk", "app", "dmg", "pkg", "lnk", "iso", "img", "vhd", "vhdx", "hta",
                        "cpl", "dll", "sys", "reg", "gadget", "appx", "msix", "xll", "docm", "xlsm", "pptm", "sh",
                        "run", "elf", "deb", "rpm", "torrent", "url", "scf", "inf", "chm"}
ARCHIVE_EXTENSIONS = {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "cab", "ace", "arj"}
INNOCENT_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "mp4", "mov", "webm", "mp3", "ogg", "wav", "pdf", "txt",
                       "docx", "xlsx", "pptx", "doc", "xls", "csv", "json", "md", "svg", "heic", "avif", "m4a", "flac"}
MAX_URLS = 12          # a message with more URLs than this is judged on the first 12 (and flagged for volume)


@dataclass
class LinkFinding:
    url: str
    host: str
    domain: str
    codes: list[str] = field(default_factory=list)       # reason codes
    brand: Optional[str] = None
    score: float = 0.0                                   # deterministic risk 0..1 (max over codes)

    @property
    def official(self) -> bool:
        return self.domain in OFFICIAL


@dataclass
class LinkReport:
    links: list[LinkFinding] = field(default_factory=list)
    invites: list[str] = field(default_factory=list)
    lures: list[str] = field(default_factory=list)
    masked_mismatch: bool = False
    url_count: int = 0

    @property
    def score(self) -> float:
        base = max((f.score for f in self.links), default=0.0)
        if self.masked_mismatch:
            base = max(base, 0.7)
        if self.lures and self.links and not all(f.official for f in self.links):
            base = min(1.0, base + 0.25 + 0.1 * (len(self.lures) - 1))
        return base

    def codes(self) -> list[str]:
        out = [c for f in self.links for c in f.codes]
        if self.masked_mismatch:
            out.append("LINK_MASKED_MISMATCH")
        if self.lures and self.links:
            out.extend(f"LURE_{kind.upper()}" for kind in self.lures)
        return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------------------------- helpers
def registrable(host: str) -> str:
    labels = [x for x in host.split(".") if x]
    if len(labels) <= 2:
        return ".".join(labels)
    last2 = ".".join(labels[-2:])
    if last2 in MULTI_PART_SUFFIXES:
        return ".".join(labels[-3:])
    return last2


def decode_host(host: str) -> str:
    try:
        return host.encode("ascii").decode("idna") if "xn--" in host else host
    except (UnicodeError, ValueError):
        return host


def edit_distance(a: str, b: str, limit: int = 3) -> int:
    """Damerau-Levenshtein (optimal string alignment), stopping early once it exceeds `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
            best = min(best, cur[j])
        if best > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


#: Brands whose name inside someone else's domain ("discord-nitro.gift", "steamgift.ru") is itself a red flag.
IMPERSONATED = ("discord", "steam", "roblox", "paypal", "metamask", "opensea", "binance", "coinbase", "epicgames", "nitro")
#: What look-alikes are compared with: every brand name plus the longer official domain labels ("steamcommunity").
_TARGETS = {brand: brand for brand in PROTECTED}
_TARGETS.update({d.split(".")[0]: brand for brand, domains in PROTECTED.items() for d in domains if len(d.split(".")[0]) >= 7})


def _brand_match(domain: str, host: str) -> tuple[Optional[str], list[str]]:
    """Which protected brand this non-official domain imitates, and how."""
    if domain in OFFICIAL:
        return None, []
    label = domain.split(".")[0]
    raw = label.replace("-", "")
    skel = skeleton(label).replace("-", "")
    for target, brand in _TARGETS.items():
        if skel == target:
            return brand, ["LINK_HOMOGLYPH" if raw != target else "LINK_BRAND_OTHER_TLD"]
    for target, brand in _TARGETS.items():
        if len(target) >= 7 and 0 < edit_distance(skel, target, 2) <= (1 if len(target) < 10 else 2):
            return brand, ["LINK_LOOKALIKE"]
    tokens = [t for t in re.split(r"[-.]", skeleton(label)) if t]
    if len(tokens) > 1:                     # "dlscord-gift": judge each word of a hyphenated name on its own too
        for token in tokens:
            for target, brand in _TARGETS.items():
                if token == target:
                    continue                # the real word itself is handled as brand-in-domain below
                if len(target) >= 7 and 0 < edit_distance(token, target, 2) <= (1 if len(target) < 10 else 2):
                    return brand, ["LINK_LOOKALIKE"]
    for brand in IMPERSONATED:
        if brand in tokens or (skel.startswith(brand) and len(skel) > len(brand) + 2) or skel.endswith(brand + "gift"):
            return (brand if brand != "nitro" else "discord"), ["LINK_BRAND_IN_DOMAIN"]
    sub = host[:-len(domain)].rstrip(".") if host.endswith(domain) else ""
    if sub:
        sub_tokens = set(re.split(r"[-.]", skeleton(sub)))
        for brand in IMPERSONATED:
            if brand in sub_tokens:
                return (brand if brand != "nitro" else "discord"), ["LINK_BRAND_SUBDOMAIN"]
        if any(official in host for official in OFFICIAL):
            return "discord", ["LINK_BRAND_SUBDOMAIN"]
    return None, []


SCORES = {"LINK_DENYLIST": 0.97, "LINK_IP_LOGGER": 0.95, "LINK_HOMOGLYPH": 0.9, "LINK_LOOKALIKE": 0.85,
          "LINK_BRAND_IN_DOMAIN": 0.75, "LINK_BRAND_SUBDOMAIN": 0.8, "LINK_BRAND_OTHER_TLD": 0.7, "LINK_USERINFO": 0.8, "LINK_PUNYCODE": 0.55,
          "LINK_IP_HOST": 0.5, "LINK_SHORTENER": 0.3, "LINK_WEAK_TLD": 0.2, "LINK_ODD_PORT": 0.2,
          "LINK_LONG_HOST": 0.15}


def analyse_url(url: str, deny: Iterable[str] = (), allow: Iterable[str] = ()) -> Optional[LinkFinding]:
    raw = url if "://" in url else "http://" + url
    raw = raw.rstrip(".,;:!?*_~")
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").rstrip(".").lower()
        port = parts.port
    except ValueError:
        return None
    if not host:
        return None
    try:
        ascii_host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        ascii_host = host
    shown = decode_host(ascii_host)
    domain = registrable(ascii_host)
    finding = LinkFinding(url=url[:300], host=shown, domain=domain)
    deny, allow = set(deny), set(allow)
    if domain in allow or ascii_host in allow:
        return finding
    codes = []
    if domain in deny or ascii_host in deny:
        codes.append("LINK_DENYLIST")
    if domain in IP_LOGGERS or ascii_host in IP_LOGGERS:
        codes.append("LINK_IP_LOGGER")
    if "@" in (parts.netloc or ""):
        codes.append("LINK_USERINFO")
    if "xn--" in ascii_host:
        codes.append("LINK_PUNYCODE")
    try:
        ipaddress.ip_address(host.strip("[]"))
        codes.append("LINK_IP_HOST")
    except ValueError:
        pass
    if domain in SHORTENERS or ascii_host in SHORTENERS:
        codes.append("LINK_SHORTENER")
    if domain.rsplit(".", 1)[-1] in WEAK_TLDS:
        codes.append("LINK_WEAK_TLD")
    if port not in (None, 80, 443):
        codes.append("LINK_ODD_PORT")
    if ascii_host.count(".") >= 4 or len(ascii_host) > 60:
        codes.append("LINK_LONG_HOST")
    # look-alikes are judged on the decoded host, so "dіscord.gift" (Cyrillic i) compares as written
    brand, brand_codes = _brand_match(registrable(shown), shown)
    if brand:
        codes.extend(brand_codes)
        finding.brand = brand
    finding.codes = list(dict.fromkeys(codes))
    finding.score = max((SCORES.get(c, 0.1) for c in finding.codes), default=0.0)
    return finding


def extract_urls(text: str) -> list[str]:
    urls = [m.group(1) for m in URL_RE.finditer(text)]
    seen = set(urls)
    for m in BARE_DOMAIN_RE.finditer(text):
        candidate = m.group(1)
        if not any(candidate in u for u in seen):
            urls.append(candidate)
            seen.add(candidate)
    return urls


def analyse_text(text: str, deny: Iterable[str] = (), allow: Iterable[str] = (),
                 own_invites: Iterable[str] = ()) -> LinkReport:
    report = LinkReport()
    if not text:
        return report
    own = {c.lower() for c in own_invites}
    clean = text if len(text) <= 4000 else text[:4000]
    report.invites = [code for code in (m.group(1) for m in INVITE_RE.finditer(clean)) if code.lower() not in own]
    urls = extract_urls(clean)
    report.url_count = len(urls)
    for url in urls[:MAX_URLS]:
        if INVITE_RE.match(url):
            continue
        finding = analyse_url(url, deny, allow)
        if finding is not None:
            report.links.append(finding)
    for m in MASKED_RE.finditer(clean):
        shown, target = m.group(1), m.group(2)
        shown_urls = extract_urls(shown) or ([shown] if "." in shown and " " not in shown.strip() else [])
        if shown_urls:
            a = analyse_url(shown_urls[0])
            b = analyse_url(target)
            if a and b and a.domain != b.domain:
                report.masked_mismatch = True
    lowered = normalise(clean)
    report.lures = [kind for kind, pattern in LURES.items() if pattern.search(lowered)]
    return report


# ---------------------------------------------------------------------------------------------- attachments
@dataclass
class AttachmentFinding:
    filename: str
    codes: list[str]
    score: float


def analyse_attachment(filename: str, size: int = 0, content_type: Optional[str] = None) -> Optional[AttachmentFinding]:
    name = filename or ""
    codes = []
    if has_bidi_override(name):
        codes.append("FILE_BIDI_TRICK")
    parts = name.lower().rsplit(".", 2)
    ext = parts[-1] if len(parts) > 1 else ""
    if ext in DANGEROUS_EXTENSIONS:
        codes.append("FILE_EXECUTABLE")
        if len(parts) == 3 and parts[1] in INNOCENT_EXTENSIONS:
            codes.append("FILE_DOUBLE_EXTENSION")
    elif ext in ARCHIVE_EXTENSIONS:
        codes.append("FILE_ARCHIVE")
    if content_type and ext in INNOCENT_EXTENSIONS:
        family = content_type.split("/")[0]
        if content_type in ("application/x-msdownload", "application/x-dosexec", "application/vnd.microsoft.portable-executable",
                            "application/java-archive", "application/vnd.android.package-archive"):
            codes.append("FILE_TYPE_MISMATCH")
        elif ext in ("png", "jpg", "jpeg", "gif", "webp") and family not in ("image", "application"):
            codes.append("FILE_TYPE_MISMATCH")
    if not codes:
        return None
    scores = {"FILE_BIDI_TRICK": 0.9, "FILE_DOUBLE_EXTENSION": 0.9, "FILE_TYPE_MISMATCH": 0.8, "FILE_EXECUTABLE": 0.6,
              "FILE_ARCHIVE": 0.15}
    return AttachmentFinding(name[:120], codes, max(scores[c] for c in codes))
