"""Raw item tables: shop/cafe/special menus and prices, emojis, descriptions and aliases.

Pure data. Behaviour lives in `rudhabot.domain.catalog.ItemCatalog`."""


# Shop Items
shop_items = {
    "baseball cap": 50,
    "sandals": 65,
    "traditional niraya mug": 100,
    "regular lottery ticket": 100,
    "rudhanese playing cards": 125,
    "cliché romance novel": 130,
    "rudhanese flag": 150,
    "seashorts": 150,
    "sun hat": 160,
    "hibiscus beanie": 170,
    "cargo pants": 200,
    "flat cap": 220,
    "mystery box": 300,
    "beginner painter kit": 340,
    "archie fumo": 350,
    "rudhastan-lee tumbler": 400,
    "fend sunglasses": 500,
    "old fedora": 500,
    "special birthday cake": 500,
    "archie t-shirt": 500,
    "black trousers": 600,
    "pulu pulu polo": 650,
    "sketchers sneakers": 700,
    "fiona hoodie": 750,
    "sing-a-song phone": 800,
    "mikey shoes": 875,
    "british coat": 900,
    "golden lottery ticket": 1000,
    "pear arcbook air": 1000,
    "random badge": 1500,
    "painting by virtuoso chen": 1900,
    "signari pottery vase": 3000,
    "certificate of rich": 10000000
}

# Cafe Items
specialty_drink = {
    "pog juice": 200
}

cafe_food_items = {
    "biscuit": 5,
    "toast": 10,
    "sorbet": 15,
    "omelet": 20,
    "muffin": 25,
    "salad": 30,
    "grilled cheese": 30,
    "ice cream": 35,
    "bagel": 40,
    "croissant": 40,
    "cinnamon roll": 45,
    "sandwich": 50,
    "waffles": 50,
    "tiramisu": 60,
    "cheesecake": 70,
    "breakfast wrap": 75,
    "pancakes": 80,
    "fortune cookie": 100
}

cafe_beverage_items = {
    "milk": 10,
    "tea": 15,
    "boba": 20,
    "beer": 25,
    "coffee": 30,
    "latte": 40
}

monthly_specials = {
    1: {  # January
        "food": {"winter pudding": 55},
        "beverage": {"hot cocoa deluxe": 30}
    },
    2: {  # February
        "food": {"valentine cupcake": 45},
        "beverage": {"strawberry milkshake": 25}
    },
    3: {  # March
        "food": {"spring rolls": 25},
        "beverage": {"green tea": 35}
    },
    4: {  # April
        "food": {"flower cake": 90},
        "beverage": {"sake": 50}
    },
    5: {  # May
        "food": {"curry": 65},
        "beverage": {"cranberry juice": 40}
    },
    6: {  # June
        "food": {"cold fruit soup": 50},
        "beverage": {"tropical punch": 35}
    },
    7: {  # July
        "food": {"bbq burger": 85},
        "beverage": {"watermelon soda": 30}
    },
    8: {  # August
        "food": {"beach nachos": 50},
        "beverage": {"fresh coconut water": 20}
    },
    9: {  # September
        "food": {"autumn pie": 60},
        "beverage": {"apple cider": 30}
    },
    10: {  # October
        "food": {"pumpkin bread": 40},
        "beverage": {"spiced tea": 50}
    },
    11: {  # November
        "food": {"kebab": 55},
        "beverage": {"iced coffee supreme": 45}
    },
    12: {  # December
        "food": {"candy canes": 20},
        "beverage": {"eggnog": 40}
    }
}


# Item Emojis
item_emojis = {
    # Cafe
    "pog juice": "<:cafe_pog:1401411470901907687>",
    
    "milk": "<:cafe_milk:1399315562395467848>",
    "tea": "<:cafe_tea:1399315635007262761>",
    "boba": "<:cafe_boba:1399315470536151071>",
    "beer": "<:cafe_beer:1399315110027202590>",
    "coffee": "<:cafe_coffee:1399315496670593114>",
    "latte": "<:cafe_latte:1399315540815908954>",
    "biscuit": "<:cafe_biscuit:1399321271774220409>",
    "toast": "<:cafe_toast:1399316790915498054>",
    "sorbet": "<:cafe_sorbet:1399316730131775488>",
    "omelet": "<:cafe_omelet:1399316633717182495>",
    "muffin": "<:cafe_muffin:1399316612515106857>",
    "salad": "<:cafe_salad:1399316681784037397>",
    "grilled cheese": "<:cafe_grilledCheese:1399316564678938714>",
    "ice cream": "<:cafe_iceCream:1399316584572522496>",
    "bagel": "<:cafe_bagel:1399316383048798281>",
    "croissant": "<:cafe_croissant:1399548435928977499>",
    "cinnamon roll": "<:cafe_cinnamonRoll:1399316444826566745>",
    "sandwich": "<:cafe_sandwich:1399548801949241475>",
    "waffles": "<:cafe_waffles:1399316822859186207>",
    "tiramisu": "<:cafe_tiramisu:1399316761395990681>",
    "cheesecake": "<:cafe_cheesecake:1399547850622243016>",
    "breakfast wrap": "<:cafe_breakfastWrap:1399316405421342770>",
    "pancakes": "<:cafe_pancakes:1399316659944030348>",
    "fortune cookie": "<:cafe_fortuneCookie:1399316545863290880>",
    
    "winter pudding": "<:cafe_food1:1401398139750977718>",
    "hot cocoa deluxe": "<:cafe_drink1:1401398052677353542>",
    "valentine cupcake": "<:cafe_food2:1401398150559957084>",
    "strawberry milkshake": "<:cafe_drink2:1401398063540473967>",
    "spring rolls": "<:cafe_food3:1401398162618585259>",
    "green tea": "<:cafe_drink3:1401398067382718497>",
    "flower cake": "<:cafe_food4:1401398174945640479>",
    "sake": "<:cafe_drink4:1401398074315640952>",
    "curry": "<:cafe_food5:1401398191366213806>",
    "cranberry juice": "<:cafe_drink5:1401398080900698245>",
    "cold fruit soup": "<:cafe_food6:1401398205475848192>",
    "tropical punch": "<:cafe_drink6:1401398087045484669>",
    "bbq burger": "<:cafe_food7:1401398221091111035>",
    "watermelon soda": "<:cafe_drink7:1401398093597114469>",
    "beach nachos": "<:cafe_food8:1401398230503133265>",
    "fresh coconut water": "<:cafe_drink8:1401398100396085409>",
    "autumn pie": "<:cafe_food9:1401398244185083965>",
    "apple cider": "<:cafe_drink9:1401398107572273224>",
    "pumpkin bread": "<:cafe_food10:1401398260223967242>",
    "spiced tea": "<:cafe_drink10:1401398115025555529>",
    "kebab": "<:cafe_food11:1401398273486491789>",
    "iced coffee supreme": "<:cafe_drink11:1401398121623453776>",
    "candy canes": "<:cafe_food12:1401398287323369494>",
    "eggnog": "<:cafe_drink12:1401398128933998726>",

     # Shop
    "baseball cap": "<:baseball_cap:1418086100014071829>",
    "sandals": "<:sandals:1418086167051636736>",
    "traditional niraya mug": "<:mug:1418086585118756945>",
    "regular lottery ticket": "<:lottery_ticket_R:1417887222258077797>",
    "rudhanese playing cards": "<:card_back:1413016849515417691>",
    "cliché romance novel": "<:novel:1418099076775280720>",
    "rudhanese flag": "<:Flag:953660196566224946>",
    "seashorts": "<:shorts:1418086204963819621>",
    "sun hat": "<:sun_hat:1418086242620280913>",
    "hibiscus beanie": "<:beanie:1418086278586437692>",
    "cargo pants": "<:cargo_pants:1418086370039173161>",
    "flat cap": "<:flat_cap:1418086501878595704>",
    "mystery box": "<:mystery_box:1418910336265879613>",
    "beginner painter kit": "<:painter:1418098978008076319>",
    "archie fumo": "<:Archie_fumo:998412903138996304>",
    "rudhastan-lee tumbler": "<:tumbler:1418782866346606592>",
    "fend sunglasses": "<:sunglasses:1418086526599823431>",
    "old fedora": "<:fedora:1418086622435737710>",
    "special birthday cake": "<:birthday_cake:1417887324754284656>",
    "archie t-shirt": "<:shirt:1418762296901111888>",
    "black trousers": "<:trousers:1418086649753239622>",
    "pulu pulu polo": "<:polo:1418086720569741312>",
    "sketchers sneakers": "<:sneakers:1418086798252310538>",
    "fiona hoodie": "<:hoodie:1418086815327326228>",
    "sing-a-song phone": "<:smartphone:1418099019930013817>",
    "mikey shoes": "<:shoes:1418086836353368115>",
    "british coat": "<:coat:1418086853516726463>",
    "golden lottery ticket": "<:lottery_ticket_G:1417887243393306816>",
    "pear arcbook air": "<:laptop:1418098953815068672>",
    "random badge": "<:random_badge:1417887307817811988>",
    "painting by virtuoso chen": "<:painting:1418762229171490836>",
    "signari pottery vase": "<:vase:1418240862424662016>",
    "certificate of rich": "<:rich_certificate:1417887261441261598>",
    
    # Bank
    "red card": "<:bankcard_red:1417887131510116483>",
    "silver card": "<:bankcard_silver:1417887151156236480>",
    "gold card": "<:bankcard_gold:1417887174895861792>",
    "platinum card": "<:bankcard_platinum:1417887196500983950>"
}

# Item Descriptions
item_descriptions = {
    # Cafe
    "pog juice": "The iconic drink from Rudharta.",
    
    "biscuit": "A small baked good, perfect for a quick snack.",
    "toast": " A sliced bread toasted to perfection, cheap snack when you're broke.",
    "sorbet": "A refreshing frozen dessert made from sweetened water with flavoring.",
    "omelet": "A dish made from beaten eggs, simple but full of protein for your dietary program.",
    "muffin": "A small, round cake that is perfect for breakfast or a snack.",
    "salad": "A healthy mix of fresh vegetables, you don't have to be a vegan to eat this.",
    "grilled cheese": "A slice or two melted cheese sandwiched with toast, can't go wrong with this.",
    "ice cream": "A classic frozen dessert, sweet and creamy.",
    "bagel": "A dense, chewy bread roll with a hole in the middle, perfect for breakfast.",
    "croissant": "A famous buttery, flaky pastry that's also culturally significant in Rudharta.",
    "cinnamon roll": "A comforting sweet pastry topped with icing you simply can't hate.",
    "sandwich": "A versatile meal that's actually has complete dietary needs.",
    "waffles": "A leavened batter cooked in a waffle iron served with syrup.",
    "tiramisu": "An Italian dessert made of layers of coffee-soaked ladyfingers and mascarpone cheese.",
    "cheesecake": "A rich dessert made with cream cheese as its main ingredient, made even better in Joot Cafe.",
    "breakfast wrap": "A wrap with meat, vegetables, and Joot Cafe special sauce to start your day.",
    "pancakes": "A stack of batter cooked on a pan as its name suggests, a considerably filling meal.",
    "fortune cookie": "A crisp cookie with a fortune inside, eat and see if what you get it is truly clairvoyant.",
    
    "milk": "A fresh cow's milk, no additives so it's fantastic for your health.",
    "tea": "A drink for any occasion, the leaves are harvested from the highlands of Southern Rudharta.",
    "boba": "A sweet drink with chewy tapioca balls to enhance the experience.",
    "beer": "A fabulous alcoholic drink, the most iconic and best-selling item in Joot Cafe.",
    "coffee": "A strong black coffee to boost your performance.",
    "latte": "A coffee mixed with milk and dairy creamer, perfect when paired with a pastry.",
    
    "winter pudding": "A warm dessert that's soft and is topped with dried fruits ideal for cold days.",
    "hot cocoa deluxe": "A rich and creamy hot drink from imported cocoa that'd energize you.",
    "valentine cupcake": "A sweet treat decorated with hearts, perfect for sharing with a loved one.",
    "strawberry milkshake": "A creamy and fruity milkshake made with imported strawberries",
    "spring rolls": "A meal of crispy rolls filled with vegetables, a light and tasty appetizer.",
    "green tea": "A soothing beverage made from authentic green tea leaves, perfect for relaxation.",
    "flower cake": "A flavorful cake themed with the world famous cherry blossoms tree.",
    "sake": "A traditional Japanese rice wine, smooth and slightly sweet.",
    "curry": "A delicious culinary marvel from South Asia, made with a blend of spices and herbs.",
    "cranberry juice": "A tart and refreshing drink made from hand-picked cranberries.",
    "cold fruit soup": "A chilled soup made with a blend of fresh fruits, perfect for hot days.",
    "tropical punch": "A vibrant and fruity drink made with a mix of fruits from Rudhanese farmers.",
    "bbq burger": "A juicy burger topped with barbecue sauce, a hearty meal for any time of the day.",
    "watermelon soda": "A fizzy yet refreshing drink, with watermelons from the outer islands.",
    "beach nachos": "A bowl of crispy tortillas with classic toppings but fine-tuned for snacking while tanning.",
    "fresh coconut water": "A hydrating yet very healthy drink straight on a young coconut from the stunning Rudhanese beaches.",
    "autumn pie": "A warm pie primarily made with pecan and apple to enhance the vibes of new autumn.",
    "apple cider": "An apple-based drink that's on par with the iconic Joot Cafe beer.",
    "pumpkin bread": "A spiced bread made with pumpkin puree, still made for the October spirit.",
    "spiced tea": "A warm and aromatic tea infused with spices, all ingredients are sourced locally.",
    "kebab": "A traditional dish of skewered and grilled meat, filling and nutritionally complete.",
    "iced coffee supreme": "A cold coffee drink with premium coffee beans and special brewing techniques.",
    "candy canes": "A sweet treat that always come with December, perfect for the holiday season.",
    "eggnog": "A creamy and spiced drink made with milk, cream, sugar, whipped egg whites, and egg yolks.",
    
    # Shop
    "baseball cap": "Your go-to headwear for most occasions.",
    "sandals": "Southeast Asian classic.",
    "traditional niraya mug": "A mug with traditional Rudhanese patterns from the province of Niraya.",
    "regular lottery ticket": "Test your luck for up to 1,000,000ra! Prizes start at 10ra, but most tickets win nothing. Use it to draw instantly.",
    "rudhanese playing cards": "Standard 56-cards deck with jokers. For casual games, or gambling.",
    "cliché romance novel": "A predictable love story that follows the usual tropes.",
    "rudhanese flag": "The flag representing the culture and heritage of Rudhanese people.",
    "seashorts": "Comfortable shorts with oceanic colors perfect for the beach.",
    "sun hat": "A wide-brimmed hat to protect you from the sun.",
    "hibiscus beanie": "A cozy beanie featuring a hibiscus design.",
    "cargo pants": "Stylish and practical pants with multiple pockets.",
    "flat cap": "A classic cap to increase your style points.",
    "mystery box": "Open and see what's inside! Could be anything from money, cheap items, or something valuable.",
    "beginner painter kit": "Everything you need to start your painting journey.",
    "archie fumo": "Plush toy of the beloved princess from Baruharta.",
    "rudhastan-lee tumbler": "Not a Stanley cup, but close enough.",
    "fend sunglasses": "The design is inspired from a renowned public figure.",
    "old fedora": "Whether you're playing detective or a gangster, this fedora will complete your look.",
    "special birthday cake": "Tell everyone it's your birthday and celebrate with this cake!",
    "archie t-shirt": "A must-have piece of clothing for Archie fans.",
    "black trousers": "Formal black trousers for special occasions.",
    "pulu pulu polo": "A lightweight polo shirt with traditional designs.",
    "sketchers sneakers": "Sneakers for everyday wear, also improves your art skills.",
    "fiona hoodie": "A cozy hoodie with colors inspired by a famous politician.",
    "sing-a-song phone": "The true made in Rudharta smartphone!",
    "mikey shoes": "The check logo might look odd, but it's an exquisite pair of shoes!",
    "british coat": "A stylish coat with a classic British design.",
    "golden lottery ticket": "Test your luck for up to 100,000,000ra! Prizes start at 100ra, but most tickets win nothing. Use it to draw instantly.",
    "pear arcbook air": "An original local-brand laptop perfect for on-the-go productivity.",
    "random badge": "Obtain a random role from the server. Collect them all!",
    "painting by virtuoso chen": "A replica of the masterpiece by legendary Rudhanese artist, Azure Chen.",
    "signari pottery vase": "A beautiful vase handcrafted by Signari artisans with the finest clay from the province.",
    "certificate of rich": "Get your rich status officialized!"
}

# Item Aliases: extra names /buy, /sell, /use and /look accept. The full item name always works, and matching ignores
# case, accents, hyphens and a trailing plural "s" (so "Cliche Romance Novels" finds the novel). An alias belongs to
# one item only (tests/test_catalog_aliases.py checks it), so ambiguous words like "hat", "ticket" or "tea" are left out.
item_aliases = {
    # Shop
    "baseball cap": ["cap", "baseball"],
    "sandals": ["slippers", "flip flops"],
    "traditional niraya mug": ["niraya mug", "mug", "niraya"],
    "regular lottery ticket": ["lottery ticket", "regular ticket", "lottery"],
    "rudhanese playing cards": ["playing cards", "cards", "deck"],
    "cliché romance novel": ["romance novel", "novel", "book", "romance"],
    "rudhanese flag": ["flag"],
    "seashorts": ["shorts", "sea shorts"],
    "sun hat": ["sunhat"],
    "hibiscus beanie": ["beanie", "hibiscus"],
    "cargo pants": ["pants", "cargo"],
    "flat cap": ["flatcap"],
    "mystery box": ["box", "mystery", "mbox"],
    "beginner painter kit": ["painter kit", "paint kit", "painter", "paint"],
    "archie fumo": ["fumo", "archie plush", "plush"],
    "rudhastan-lee tumbler": ["tumbler", "stanley", "cup"],
    "fend sunglasses": ["sunglasses", "glasses", "shades"],
    "old fedora": ["fedora"],
    "special birthday cake": ["birthday cake", "bday cake", "cake"],
    "archie t-shirt": ["t-shirt", "tshirt", "shirt", "tee"],
    "black trousers": ["trousers"],
    "pulu pulu polo": ["polo", "polo shirt"],
    "sketchers sneakers": ["sneakers", "sketchers"],
    "fiona hoodie": ["hoodie"],
    "sing-a-song phone": ["phone", "smartphone"],
    "mikey shoes": ["shoes", "mikey"],
    "british coat": ["coat"],
    "golden lottery ticket": ["golden ticket", "gold ticket", "golden lottery"],
    "pear arcbook air": ["arcbook", "laptop", "pear arcbook"],
    "random badge": ["badge"],
    "painting by virtuoso chen": ["painting", "chen painting", "chen"],
    "signari pottery vase": ["vase", "pottery vase", "pottery"],
    "certificate of rich": ["certificate", "rich certificate"],

    # Cafe
    "pog juice": ["pog", "juice"],

    "toast": ["bread"],
    "omelet": ["omelette"],
    "salad": ["veggies"],
    "grilled cheese": ["cheese sandwich"],
    "ice cream": ["icecream"],
    "cinnamon roll": ["roll", "cinnamon"],
    "cheesecake": ["cheese cake"],
    "breakfast wrap": ["wrap", "breakfast"],
    "fortune cookie": ["cookie", "fortune"],
    "boba": ["bubble tea", "boba tea", "milk tea"],
    "coffee": ["black coffee"],
    "latte": ["cafe latte"],

    "winter pudding": ["pudding"],
    "hot cocoa deluxe": ["hot cocoa", "cocoa", "hot chocolate"],
    "valentine cupcake": ["cupcake", "valentine"],
    "strawberry milkshake": ["milkshake", "shake", "strawberry"],
    "spring rolls": ["rolls"],
    "green tea": ["matcha"],
    "flower cake": ["sakura cake"],
    "sake": ["rice wine"],
    "cranberry juice": ["cranberry"],
    "cold fruit soup": ["fruit soup", "soup"],
    "tropical punch": ["punch"],
    "bbq burger": ["burger", "barbecue burger"],
    "watermelon soda": ["soda", "watermelon"],
    "beach nachos": ["nachos"],
    "fresh coconut water": ["coconut water", "coconut"],
    "autumn pie": ["pie"],
    "apple cider": ["cider"],
    "pumpkin bread": ["pumpkin"],
    "spiced tea": ["spice tea", "chai"],
    "kebab": ["kabob", "kebap"],
    "iced coffee supreme": ["iced coffee"],
    "candy canes": ["candy"],
    "eggnog": ["nog", "egg nog"],
}

# Item lore for /look: longer in-world entries, keyed by item name. Empty until the lore is written; /look shows
# an item's lore when it has one and its shop description otherwise.
item_lore: dict[str, str] = {}
