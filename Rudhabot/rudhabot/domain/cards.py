"""Playing cards: emoji table, card, deck and blackjack hand."""
from __future__ import annotations

import random


card_emojis = {
    "BACK": "<:card_back:1413016849515417691>",
    
    # Hearts
    "A♥": "<:card_Ah:1413011318121955459>",
    "2♥": "<:card_2h:1413010774472786053>",
    "3♥": "<:card_3h:1413010815933747371>",
    "4♥": "<:card_4h:1413010864575086713>",
    "5♥": "<:card_5h:1413010910972346471>",
    "6♥": "<:card_6h:1413010972813295747>",
    "7♥": "<:card_7h:1413011032422481971>",
    "8♥": "<:card_8h:1413011101037101086>",
    "9♥": "<:card_9h:1413011161338613840>",
    "10♥": "<:card_10h:1413011240028213429>",
    "J♥": "<:card_Jh:1413011393032224840>",
    "Q♥": "<:card_Qh:1413011576923095141>",
    "K♥": "<:card_Kh:1413011480311365692>",
    
    # Diamonds
    "A♦": "<:card_Ad:1413011295044636672>",
    "2♦": "<:card_2d:1413010763953606676>",
    "3♦": "<:card_3d:1413010806303621140>",
    "4♦": "<:card_4d:1413010855133712514>",
    "5♦": "<:card_5d:1413010900583059477>",
    "6♦": "<:card_6d:1413010960548892723>",
    "7♦": "<:card_7d:1413011017931161630>",
    "8♦": "<:card_8d:1413011085568770151>",
    "9♦": "<:card_9d:1413011147623235735>",
    "10♦": "<:card_10d:1413011218557308959>",
    "J♦": "<:card_Jd:1413011371796467712>",
    "Q♦": "<:card_Qd:1413011564914540624>",
    "K♦": "<:card_Kd:1413011458001862766>",
    
    # Clubs
    "A♣": "<:card_Ac:1413011278951088162>",
    "2♣": "<:card_2c:1413010736543826001>",
    "3♣": "<:card_3c:1413010795465412608>",
    "4♣": "<:card_4c:1413010843477610546>",
    "5♣": "<:card_5c:1413010886401982495>",
    "6♣": "<:card_6c:1413010948725411900>",
    "7♣": "<:card_7c:1413011004945731735>",
    "8♣": "<:card_8c:1413011066794938489>",
    "9♣": "<:card_9c:1413011131764572261>",
    "10♣": "<:card_10c:1413011197380395008>",
    "J♣": "<:card_Jc:1413011356365357066>",
    "Q♣": "<:card_Qc:1413011535206289498>",
    "K♣": "<:card_Kc:1413011439165247529>",
    
    # Spades
    "A♠": "<:card_As:1413011335620460695>",
    "2♠": "<:card_2s:1413010784501497926>",
    "3♠": "<:card_3s:1413010831322644500>",
    "4♠": "<:card_4s:1413010875391934605>",
    "5♠": "<:card_5s:1413010929703981166>",
    "6♠": "<:card_6s:1413010984850690148>",
    "7♠": "<:card_7s:1413011043747233882>",
    "8♠": "<:card_8s:1413011114886693024>",
    "9♠": "<:card_9s:1413011177591804004>",
    "10♠": "<:card_10s:1413011261821681736>",
    "J♠": "<:card_Js:1413011413726658650>",
    "Q♠": "<:card_Qs:1413011596761894983>",
    "K♠": "<:card_Ks:1413011503807987712>"
}

class Card:
    def __init__(self, suit, rank):
        self.suit = suit
        self.rank = rank
    
    def __str__(self):
        return f"{self.rank}{self.suit}"
    
    def get_value(self):
        if self.rank in ['J', 'Q', 'K']:
            return 10
        elif self.rank == 'A':
            return 11
        else:
            return int(self.rank)
    
    def get_emoji(self):
        return card_emojis.get(f"{self.rank}{self.suit}", "🎴")

class Deck:
    def __init__(self, rng=random):
        self._rng = rng
        self.cards = []
        self.reset()
    
    def reset(self):
        suits = ['♠', '♥', '♦', '♣']
        ranks = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
        self.cards = [Card(suit, rank) for suit in suits for rank in ranks]
        self._rng.shuffle(self.cards)
    
    # Reshuffle the deck if it has less than 10 cards left
    def deal_card(self):
        if len(self.cards) < 10:
            self.reset()
        return self.cards.pop()

class BlackjackHand:
    def __init__(self):
        self.cards = []
        self.is_split_hand = False
    
    def add_card(self, card):
        self.cards.append(card)
    
    def get_value(self):
        value = 0
        aces = 0
        for card in self.cards:
            if card.rank == 'A':
                aces += 1
                value += 11
            else:
                value += card.get_value()
        
        # Adjust for aces
        while value > 21 and aces > 0:
            value -= 10
            aces -= 1
        
        return value
    
    def get_display_value(self):
        """Get the display value for the hand, showing ace flexibility when applicable"""
        value = 0
        aces = 0
        for card in self.cards:
            if card.rank == 'A':
                aces += 1
                value += 11
            else:
                value += card.get_value()
        
        # If we have aces and they could be 1 or 11, show both values
        if aces > 0 and value > 21:
            # Calculate soft value (aces as 1)
            soft_value = value - (aces * 10)
            if soft_value != value - 10 or aces > 1:
                return f"{soft_value}/{value - 10}" if value - 10 != soft_value else str(soft_value)
            else:
                return str(soft_value)
        elif aces > 0 and value <= 21:
            # Show both values if ace can be 1 or 11
            low_value = value - 10
            if low_value != value and low_value > 0:
                return f"{low_value}/{value}"
            else:
                return str(value)
        else:
            return str(value)
    
    def is_bust(self):
        return self.get_value() > 21
    
    def is_blackjack(self):
        return len(self.cards) == 2 and self.get_value() == 21
    
    def can_split(self):
        return len(self.cards) == 2 and self.cards[0].get_value() == self.cards[1].get_value()
    
    def get_display(self, hide_first=False):
        if hide_first and len(self.cards) > 0:
            display = [card_emojis["BACK"]]
            display.extend([card.get_emoji() for card in self.cards[1:]])
            return " ".join(display)
        else:
            return " ".join([card.get_emoji() for card in self.cards])
    
    def get_visible_value(self):
        """Get value of visible cards only (for dealer's face-up card)"""
        if len(self.cards) <= 1:
            return "?"
        
        # Only count the face-up card (second card)
        visible_card = self.cards[1]
        if visible_card.rank == 'A':
            return "1/11"
        else:
            return str(visible_card.get_value())
