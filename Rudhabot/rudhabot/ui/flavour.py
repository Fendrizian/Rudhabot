"""Randomised replies for /work and /crime. `{amount}` is the money earned, `{fine}` the fine; both are filled in
bold with the currency symbol. The first 40 /work lines are the server's own (kept as written)."""
from __future__ import annotations

import random

from rudhabot.ui.theme import CURRENCY

WORK = (
    # the server's own list
    "You worked as a factory worker in Baruwarnapura and earned {amount}",
    "You successfully invented wormholes and received {amount} for your amazing work.",
    "You worked as a government intern in Rudharta and got paid {amount}",
    "You dug straight down and actually found diamonds without falling into a lava pool. You sold the diamonds for {amount}",
    "You worked as Archie's personal bodyguard (not canon) and earned {amount}",
    "You became a rice farmer in Sawahsari and earned {amount}",
    "You found aliens at the sun and traded with them to get {amount}",
    "You went to Mars before Elon did. You got {amount}",
    "You learned the history of Rudharta and became a historian. You earned {amount} for the work you did.",
    "You served in the Rudhanese Army and got paid {amount}",
    "You were born a child prodigy. You studied at Harvard at the age of 3, invented gravity before Newton did, and "
    "became a doctor at the age of 12. You can also cook rice without the rice. You got {amount} for all of that.",
    "You made your own Rudhanese flag out of a calendar. You got {amount} when your work didn't go unnoticed.",
    "You teleported to the fourth dimension and saw what Rudharta would look like there. You earned {amount} when you "
    "published your sightings to the public.",
    "You collected {amount} after a long day of walking around the streets and picking up any cash you saw.",
    "You transformed into a bear and destroyed a comet in space. You also found {amount} from that comet you just "
    "destroyed.",
    "You walked on hell's highway and found {amount} before you arrived at the gates of hell.",
    "You worked as a full-time Discord mod and got paid {amount}. You should consider starting a diet or working out.",
    "You invented the flamethrower and became a flammenwerfer yourself during the Great War. You came home with {amount}",
    "You nuked North Korea and looted {amount} from the ruins.",
    "You worked as a clown and got called a copycat of Pennywise, but still got {amount} from all of that.",
    "You played as a midfielder for the Rudhanese National Football Team and received {amount} for your performance.",
    "You found {amount} in the dense Rudhanese jungle.",
    "You worked as Archie's personal clothes wearer and got paid {amount}",
    "You made and proposed a new flag for Rudharta. Your work didn't go unnoticed, but your flag was still rejected. "
    "They gave you {amount} as compensation, though.",
    "The sky is cloudy today, so you decided to inhale the clouds to clear the sky. People thanked you and gave you "
    "{amount}",
    "You helped with the production of PHANTASTIC TREASURE, the best game Rudharta has ever made! (because it's the only "
    "game ever made) You were rewarded with {amount} for your amazing work.",
    "You walked uphill both ways to go to school. You found {amount} in Siberia on your way, too.",
    "You tried out at JOOT CAFE and got paid {amount}. You were also given a free coffee from JOOT CAFE. You considered "
    "working at JOOT CAFE and also told other people to come to JOOT CAFE. (This is 100% totally not a promotion of "
    "JOOT CAFE)",
    "You literally sued Phoenix Wright himself; you somehow won and came out of court with {amount}",
    "You became a supermodel and got paid {amount}. Anyways, there's a weird man with a camera behind you.",
    "You finally managed to determine which is the true canon event, so you got {amount}",
    "You receive a doctorate degree in chemistry. You discovered a brand new element and receive {amount} for your "
    "incredible work.",
    "A politician hired you to be his *cough* campaign assistant. You helped him with his *cough* motivational speech. "
    "He promised *cough* to give prosperity *cough* to the people, so you were paid {amount}",
    "You worked at Subway and got paid {amount}",
    "You were hired to be a professional statue but got fired a day after. You still got paid {amount}, though.",
    "You ate so much rice that you ran out of rice in your house. Fortunately, you found {amount} in the now-empty rice "
    "bag, so you can buy more rice!",
    "You planted the flag of Rudharta in your backyard. The government noticed it and gave you {amount}",
    "You got straight A+s on your report card! You became a doctor and got paid {amount}",
    "You made music for Rudharta and got {amount} in music royalties!",
    "You somehow traveled to the year 1940, and you were hired as a bomber! Good thing you came back to your current "
    "time with the {amount}. But be thankful you were alive, ya little weiner.",
    # more in the same spirit
    "You delivered coffee for JOOT CAFE across all of Baruwarnapura before a single cup went cold. The tips added up to "
    "{amount}",
    "You restocked the shelves at the National Shop and found a better way to stack rice sacks. Your manager paid you "
    "{amount}",
    "You guided tourists through the old streets of Rudharta and earned {amount} in tips.",
    "You fixed the royal palace's Wi-Fi, so Princess Archie could finally watch her shows again. She paid you {amount}",
    "You caught fish in the river near Sawahsari and sold the whole catch for {amount}",
    "You moderated a Rudhanese debate where nobody shouted. A historic first! You were paid {amount}",
    "You translated the national anthem into 12 languages and one made-up one. You earned {amount}",
    "You built a sandcastle so impressive that the Ministry of Culture listed it as a monument. They gave you {amount}",
    "You taught a parrot to say \"Long live Rudharta\" and sold tickets to hear it. You made {amount}",
    "You worked the night shift as a lighthouse keeper and spotted three ships and one very confused whale. You got paid "
    "{amount}",
    "You drew the official map of Rudharta and only forgot one village. They still paid you {amount}",
    "You tested every chair in the National Shop for comfort. It was hard work, but someone had to do it. You earned "
    "{amount}",
    "You streamed yourself studying for 10 hours straight. Your viewers donated {amount}",
    "You coached the Rudhanese National Football Team's goalkeeper, and he finally saved a penalty. You received {amount}",
    "You entered a Cloudfall tournament and beat a player who swore they had never lost. The prize was {amount}",
)

CRIME_SUCCESS = (
    "You snuck into JOOT CAFE after closing and drank every last cup of coffee. You also found {amount} in the tip jar.",
    "You sold \"authentic\" Rudhanese flags that were actually printed on napkins. You made {amount}",
    "You pickpocketed a tourist in Baruwarnapura and walked away with {amount}",
    "You smuggled premium rice out of Sawahsari and sold it on the black market for {amount}",
    "You jaywalked across the busiest street in Rudharta and found {amount} on the other side. Crime pays, apparently.",
    "You hacked the National Shop's discount system and resold the items for {amount}",
    "You forged Archie's signature on a check and cashed in {amount}",
    "You ran a fake fortune-telling stall and told everyone they'd come into money. You did: {amount}",
    "You stole the hat off a statue in the town square and sold it to a collector for {amount}",
    "You sold counterfeit Cloudfall cards to unsuspecting collectors and made {amount}",
    "You rigged the town raffle so you'd win. The prize was {amount}",
    "You snuck into the Rudhanese Army's canteen and sold their pudding cups for {amount}",
    "You started a pyramid scheme selling invisible shoes. You made {amount} before anyone noticed they couldn't see "
    "them.",
    "You \"borrowed\" a delivery scooter and made {amount} doing deliveries before you returned it.",
    "You picked the lock on a vending machine and collected {amount} in coins.",
    "You sold tickets to a concert that never existed and pocketed {amount}",
    "You stole the secret recipe for JOOT CAFE's best latte and sold it to a rival for {amount}",
    "You smuggled durians onto a plane and sold them at the other end for {amount}",
    "You hid in the National Shop until closing and walked out with {amount} worth of goods.",
    "You painted a fake masterpiece and sold it to a museum as \"a lost Rudhanese classic\" for {amount}",
    "You ran an illegal betting ring on snail races and took home {amount}",
    "You hijacked the town's ice cream truck and sold every popsicle for {amount}",
    "You scalped tickets to the Rudhanese National Football Team's final and made {amount}",
    "You sold \"holy water\" that came straight from your tap. Believers paid {amount}",
    "You swiped a briefcase from a suspicious man in sunglasses. Inside was {amount}",
    "You counted cards at the casino and walked out {amount} richer.",
    "You sold a \"map to the lost treasure of Rudharta\" that you drew last night. You made {amount}",
    "You snuck a cat into a dog show, and it won. You collected the {amount} prize.",
    "You cut the line at the bank and somehow walked out with {amount}",
    "You stole a sleeping security guard's sandwich and found {amount} tucked inside the wrapper.",
    "You sold your neighbour's lawn gnomes as \"rare collectibles\" for {amount}",
    "You emptied the coins out of the city fountain. Wishes are cancelled now, but you got {amount}",
    "You set up a fake toll booth on a country road near Sawahsari and collected {amount}",
    "You claimed to be Archie's long-lost cousin and received {amount} in \"family support\"",
    "You ran a \"guaranteed\" LICH coin scheme and cashed out {amount} right before it crashed.",
    "You smuggled extra-spicy sambal past customs and sold it for {amount}",
    "You swapped the price tags at the National Shop and pocketed the difference: {amount}",
    "You stole a goose from the park. It turned out to be golden. Sort of. You sold it for {amount}",
    "You hacked a billboard to show cat videos and sold the ad space for {amount}",
    "You \"found\" a wallet before its owner even lost it. It held {amount}",
    "You raided the cookie jar at the Rudhanese embassy and sold the cookies for {amount}",
    "You sold fake VIP passes to JOOT CAFE and made {amount}",
    "You smuggled a crate of bananas across the border and sold them for {amount}",
    "You charged people to see a \"real dragon\" that was just your iguana. You made {amount}",
    "You copied someone's homework and sold the answers to the whole class for {amount}",
    "You snuck into a wedding, ate the cake, and left with {amount} from the gift table.",
    "You rewrote the history books to say you invented rice. Royalties so far: {amount}",
    "You ran off with the collection box from the school bake sale. Shame on you, but you got {amount}",
    "You pretended to haunt an abandoned house and charged people for ghost tours. You earned {amount}",
    "You sold the same Cloudfall card to five different people on the same day. You made {amount}",
)

CRIME_CAUGHT = (
    "You tried to empty JOOT CAFE's tip jar, but the barista was watching. You were fined {fine}",
    "You got caught selling napkin flags. The flag committee fined you {fine}",
    "You tried to pickpocket a police officer. Bold. You were fined {fine}",
    "You slipped on a banana peel during your getaway. The police fined you {fine}",
    "You tried to smuggle rice out of Sawahsari, but the farmers chased you down. You were fined {fine}",
    "Your disguise was a fake mustache that fell off mid-heist. You were fined {fine}",
    "You tried to forge Princess Archie's signature but spelled her name wrong. You were fined {fine}",
    "You got stuck in the vending machine you were robbing. The firefighters who freed you also fined you {fine}",
    "You tried to sell counterfeit Cloudfall cards to the game's own designer. You were fined {fine}",
    "You sneezed while hiding from the guards. You were fined {fine}",
    "You left your ID card at the crime scene. You were fined {fine}",
    "You tried to rig the town raffle, but your name was on every single ticket. You were fined {fine}",
    "You bragged about your crime on Discord. A moderator reported you, and you were fined {fine}",
    "You tried to steal the Rudhanese Army's pudding cups. The army was not amused. You were fined {fine}",
    "Your getaway vehicle was a bicycle with a flat tire. You were fined {fine}",
    "You tried to hijack the ice cream truck, but it was a police van. You were fined {fine}",
    "You were caught swapping price tags at the National Shop. You were fined {fine}",
    "You tried to scam a fortune teller, who saw it coming. You were fined {fine}",
    "You tripped the museum alarm while taking a selfie with the painting you came to steal. You were fined {fine}",
    "You tried to sneak a cat into a dog show, but it meowed. You were fined {fine}",
    "You tried to take the coins from the city fountain and fell in. You were fished out and fined {fine}",
    "You set up your fake toll booth right in front of the police station. You were fined {fine}",
    "You claimed to be Archie's long-lost cousin, in front of Archie. You were fined {fine}",
    "You tried to steal a goose from the park. The goose won. You were also fined {fine}",
    "You copied someone's homework, including their name. You were fined {fine}",
    "You snuck into a wedding but caught the bouquet, and everyone noticed. You were fined {fine}",
    "You hid in the National Shop after closing and fell asleep. Security found you in the morning and fined you {fine}",
    "You tried to counterfeit ra with crayons. You were fined {fine}",
    "You ran from the police straight into a bigger police officer. You were fined {fine}",
    "You tried to hack a billboard, but forgot to turn your computer on. You were fined {fine}",
    "You tried to steal a statue's hat, but the statue was a street performer. You were fined {fine}",
    "You tried to sell holy tap water to a priest. You were fined {fine}",
    "Your accomplice was an undercover agent the whole time. You were fined {fine}",
    "You broke into JOOT CAFE for the secret latte recipe. It's just coffee and milk. You were fined {fine} for "
    "trespassing anyway.",
    "You planned a heist for a power outage, then remembered the cameras run on batteries. You were fined {fine}",
    "You jaywalked right in front of a traffic officer. You were fined {fine}",
    "You signed your ransom note with your real name. You were fined {fine}",
    "You tried to scalp football tickets to the referee. You were fined {fine}",
    "You smuggled durians onto a plane, and the whole cabin smelled them. You were fined {fine}",
    "You ran a snail-racing betting ring, but the snails ratted you out. You were fined {fine}",
    "You tried to rob the bank on a public holiday. It was closed, and the guard fined you {fine} for loitering.",
    "You wore a ski mask in the middle of summer. Very suspicious. You were fined {fine}",
    "You tried to steal a parked car, but it was your own. Somehow, you were still fined {fine}",
    "Your getaway driver stopped at every red light. You were fined {fine}",
    "You tried to bribe a judge with JOOT CAFE vouchers. You were fined {fine}",
    "You tried to sell your \"lost treasure map of Rudharta\" to a historian, who knew better. You were fined {fine}",
    "You forgot that Rudharta has cameras on every corner. You were fined {fine}",
    "You tried to pickpocket a mime, who mimed calling the police. You were fined {fine}",
    "You hid the loot in your pocket, but your pocket had a hole in it. You were fined {fine}",
    "You crawled through the air vents, sneezed from the dust, and fell straight into the security office. You were "
    "fined {fine}",
)


def money(amount: int) -> str:
    return f"**{CURRENCY}{amount:,}**"


def work_line(rng: random.Random, amount: int) -> str:
    return rng.choice(WORK).format(amount=money(amount))


def crime_line(rng: random.Random, amount: int) -> str:
    return rng.choice(CRIME_SUCCESS).format(amount=money(amount))


def caught_line(rng: random.Random, fine: int) -> str:
    return rng.choice(CRIME_CAUGHT).format(fine=money(fine))
