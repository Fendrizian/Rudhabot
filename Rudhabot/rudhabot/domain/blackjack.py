"""Blackjack rules: the game state machine and its per-hand results (no Discord, no money formatting)."""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from rudhabot.domain.cards import BlackjackHand, Deck


class HandOutcome(Enum):
    BUST = "bust"
    BLACKJACK = "blackjack"
    TWENTY_ONE = "21"
    DEALER_BUST = "dealer_bust"
    WIN = "win"
    PUSH = "push"
    LOSS = "loss"


@dataclass(frozen=True)
class HandResult:
    outcome: HandOutcome
    amount: int          # winnings for winning outcomes; the bet for bust / push / loss


class BlackjackGame:
    def __init__(self, user_id, bet_amount, sessions, rng=random):
        self.user_id = user_id
        self.bet_amount = bet_amount
        self._sessions = sessions        # anything with start(user_id) / end(user_id)
        self.deck = Deck(rng)
        self.player_hands = [BlackjackHand()]
        self.dealer_hand = BlackjackHand()
        self.current_hand_index = 0
        self.game_over = False
        self.doubled_down = [False]
        self.split_count = 0
        
        # Add player to active games
        sessions.start(user_id)
        
        # Deal initial cards
        self.player_hands[0].add_card(self.deck.deal_card())
        self.dealer_hand.add_card(self.deck.deal_card())
        self.player_hands[0].add_card(self.deck.deal_card())
        self.dealer_hand.add_card(self.deck.deal_card())
    
    def can_double_down(self):
        current_hand = self.player_hands[self.current_hand_index]
        return len(current_hand.cards) == 2 and not self.doubled_down[self.current_hand_index]
    
    def can_split(self):
        current_hand = self.player_hands[self.current_hand_index]
        return (len(self.player_hands) < 4 and 
                current_hand.can_split() and 
                self.split_count < 3)
    
    def hit(self):
        if self.game_over:
            return
            
        current_hand = self.player_hands[self.current_hand_index]
        current_hand.add_card(self.deck.deal_card())
        
        if current_hand.is_bust():
            self.next_hand()
    
    def stand(self):
        if self.game_over:
            return
            
        self.next_hand()
    
    def double_down(self):
        if self.game_over or not self.can_double_down():
            return False
            
        self.doubled_down[self.current_hand_index] = True
        current_hand = self.player_hands[self.current_hand_index]
        current_hand.add_card(self.deck.deal_card())
        
        self.next_hand()
        return True
    
    def split(self):
        if self.game_over or not self.can_split():
            return False
            
        current_hand = self.player_hands[self.current_hand_index]
        # Create new hand with second card
        new_hand = BlackjackHand()
        new_hand.add_card(current_hand.cards.pop())
        new_hand.is_split_hand = True
        
        # Add new cards to both hands
        current_hand.add_card(self.deck.deal_card())
        new_hand.add_card(self.deck.deal_card())
        
        # Insert new hand after current hand
        self.player_hands.insert(self.current_hand_index + 1, new_hand)
        self.doubled_down.insert(self.current_hand_index + 1, False)
        self.split_count += 1
        return True
    
    def next_hand(self):
        if self.game_over:
            return
            
        self.current_hand_index += 1
        if self.current_hand_index >= len(self.player_hands):
            self.dealer_play()
            self.end_game()
    
    def dealer_play(self):
        if self.game_over:
            return
            
        # Check if all player hands busted
        all_busted = all(hand.is_bust() for hand in self.player_hands)
        if all_busted:
            return
            
        # Standard dealer rules: hit on 16 or less, stand on 17 or more
        while self.dealer_hand.get_value() < 17:
            self.dealer_hand.add_card(self.deck.deal_card())
    
    def end_game(self):
        """End the game and remove player from active games"""
        self.game_over = True
        self._sessions.end(self.user_id)
    
    def calculate_winnings(self):
        """Returns (total returned to the player, [HandResult per hand])."""
        total_winnings = 0
        results = []

        for i, hand in enumerate(self.player_hands):
            bet_for_hand = self.bet_amount * (2 if self.doubled_down[i] else 1)

            if hand.is_bust():
                result = HandResult(HandOutcome.BUST, bet_for_hand)
            elif hand.is_blackjack() and not self.dealer_hand.is_blackjack():
                if len(self.player_hands) == 1:
                    winnings = int(bet_for_hand * 2.5)
                    result = HandResult(HandOutcome.BLACKJACK, winnings)
                else:
                    winnings = bet_for_hand * 2
                    result = HandResult(HandOutcome.TWENTY_ONE, winnings)
                total_winnings += winnings
            elif self.dealer_hand.is_bust():
                winnings = bet_for_hand * 2
                total_winnings += winnings
                result = HandResult(HandOutcome.DEALER_BUST, winnings)
            elif hand.get_value() > self.dealer_hand.get_value():
                winnings = bet_for_hand * 2
                total_winnings += winnings
                result = HandResult(HandOutcome.WIN, winnings)
            elif hand.get_value() == self.dealer_hand.get_value():
                total_winnings += bet_for_hand
                result = HandResult(HandOutcome.PUSH, bet_for_hand)
            else:
                result = HandResult(HandOutcome.LOSS, bet_for_hand)

            results.append(result)

        return total_winnings, results
