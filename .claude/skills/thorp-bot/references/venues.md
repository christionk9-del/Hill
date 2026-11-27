# Venue rules that change the math

Verify against the venue's current rules. Promos, fees and payout tables change.

## PrizePicks (pick'em DFS)
- Standard Power Play payouts (total return): 2-pick 3x, 3-pick 6x, 4-pick 10x, 5-pick 20x,
  6-pick 37.5x. Break-even per leg if legs are equal: 2-pick 57.7%, 3-pick 55.0%,
  4-pick 56.2%, 5-pick 54.9%, 6-pick 54.7%.
- Flex Play (partial payouts): 2-pick 2x / 0.5x; 3-pick 3x / 1x; 4-pick 6x / 1.5x;
  5-pick 10x / 2x / 0.4x; 6-pick 25x / 2x / 0.4x. Use `kelly.py pickem --type flex`.
- Promo lines (e.g. "Taco Tuesday", discounted lines) are the main edge; they usually cap
  the entry ($25) and are often More-only.
- "Goblin/demon" lines pay less or more than the standard table. Read the payout shown
  in the app and use `kelly.py multi` with the exact table.
- If a player doesn't play, the pick is removed and the entry drops a level. A 2-pick
  usually refunds. Check before counting injury risk as a loss.
- Legs from the same game are correlated, and the payout table assumes independence.
  Positively correlated legs on the same side raise the chance all hit.
- Legal status and availability vary by state.

## Kalshi (CFTC-regulated exchange, official API)
- Taker fee ≈ 0.07 × P × (1 − P) per contract, rounded up to the cent per order (hurts
  tiny orders). Maker fee, where charged, is about a quarter of that. Use
  `kelly.py kalshi --p … --price …`.
- A YES contract at price P pays $1, so the payout multiple is 1 / (P + fee).
- Sub-cent ticks below 10¢ and above 90¢; whole cents in between.
- Combos are priced by market makers through RFQs and already include correlation.
- 15-minute crypto markets settle on the 60-second average of CF Benchmarks' index before
  close versus before open. Index markets settle on the index value at the stated time.
- API: REST + WebSocket with RSA-signed keys. No webhooks.

## Sportsbooks
- Convert American odds with `kelly.py american --p … --odds=…` (use "=" for negative odds).
- Remove the vig from a two-way market with `kelly.py devig --odds=-130,+110` to get the
  market's fair probabilities. Sharp books (Pinnacle) are the best reference for fair odds.
- Books void "palpable error" prices and limit winning accounts. Offshore books
  (Betsafe, Betsson, 1xBet) are unlicensed in most US states and carry legal and
  withdrawal risk.
- Arbitrage needs both legs filled at the quoted price and identical settlement rules.
  Check void and DNP rules on both sides.

## DraftKings Predictions
- Runs on DraftKings' own exchange (DKeX, built on Railbird). No public trading API as of
  late 2026, so automating it likely breaks the terms of use.
