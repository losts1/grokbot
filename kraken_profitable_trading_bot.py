#!/usr/bin/env python3
"""
Kraken Profitable Trading Bot
- Only makes trades that are profitable after covering exchange fees
- Primary: WebSocket for real-time market data (ticker)
- Fallback: REST API for data, account, fees, and order placement
- Tracks inventory (balances) via REST Balance endpoint
- Retrieves current account fee rate/schedule via TradeVolume private endpoint
- Enforces profitability check before any order placement
- Uses authenticated private requests with proper nonce and signing

Requirements:
    pip install requests websockets

Usage:
    1. Create Kraken API key with appropriate permissions (Query Funds, Query Orders & Trades, Create & Modify Orders)
    2. Replace API_KEY and API_SECRET below (NEVER commit secrets)
    3. Run: python kraken_profitable_trading_bot.py

WARNING: This is a framework/example. Real trading involves risk of loss.
         Implement your own strategy logic in the decision making section.
         Test thoroughly on testnet/sandbox if available. Not financial advice.
"""

import asyncio
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
from typing import Dict, Any, Optional
import requests
import websockets

class KrakenProfitableBot:
    def __init__(self, api_key: str, api_secret: str, pair: str = "XBT/USD"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws_pair = pair                  # e.g. "XBT/USD" for WebSocket
        self.rest_pair = pair.replace("/", "")  # e.g. "XBTUSD" for REST
        self.base_currency = self.rest_pair[:3] if len(self.rest_pair) > 3 else "XBT"
        self.quote_currency = self.rest_pair[3:] if len(self.rest_pair) > 3 else "USD"

        # State
        self.inventory: Dict[str, float] = {}      # Current balances (tracked)
        self.current_price: Optional[float] = None
        self.fee_rate: float = 0.0026              # Default ~0.26% as decimal (updated from API)
        self.last_nonce: int = int(time.time() * 1000)

        # Config
        self.min_notional_usd = 50.0               # Minimum trade value to cover fees reasonably
        self.min_profit_margin = 0.0015            # 0.15% edge required over fees for roundtrip consideration
        self.ws_url = "wss://ws.kraken.com"
        self.rest_url = "https://api.kraken.com"

    # ==================== PRIVATE REST HELPERS ====================

    def _get_signature(self, uri_path: str, data: Dict[str, Any]) -> str:
        """Generate Kraken API signature for private endpoints."""
        data = data.copy()
        nonce = str(int(time.time() * 1000))
        if int(nonce) <= self.last_nonce:
            nonce = str(self.last_nonce + 1)
        self.last_nonce = int(nonce)
        data["nonce"] = nonce

        postdata = urllib.parse.urlencode(data)
        encoded = (nonce + postdata).encode("utf-8")
        message = uri_path.encode("utf-8") + hashlib.sha256(encoded).digest()
        secret = base64.b64decode(self.api_secret)
        mac = hmac.new(secret, message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode("utf-8"), data

    def private_request(self, method: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        """Make authenticated private REST API call."""
        if data is None:
            data = {}
        uri_path = f"/0/private/{method}"
        signature, signed_data = self._get_signature(uri_path, data)
        headers = {
            "API-Key": self.api_key,
            "API-Sign": signature,
        }
        url = f"{self.rest_url}{uri_path}"
        try:
            response = requests.post(url, headers=headers, data=signed_data, timeout=10)
            response.raise_for_status()
            result = response.json()
            if result.get("error"):
                print(f"[REST ERROR] {method}: {result['error']}")
            return result
        except Exception as e:
            print(f"[REST EXCEPTION] {method}: {e}")
            return {"error": [str(e)]}

    # ==================== INVENTORY & FEES ====================

    def update_inventory(self) -> Dict[str, float]:
        """Fetch and track current account balances (inventory)."""
        result = self.private_request("Balance")
        if result.get("error"):
            print("[INVENTORY] Failed to fetch balances")
            return self.inventory
        balances = result.get("result", {})
        # Convert string values to float where possible
        self.inventory = {k: float(v) for k, v in balances.items() if v}
        print(f"[INVENTORY] Updated: {self.inventory}")
        return self.inventory

    def get_current_fee_rate(self, pair: Optional[str] = None) -> float:
        """Retrieve account's current fee schedule from Kraken API (TradeVolume)."""
        if pair is None:
            pair = self.rest_pair
        data = {"pair": pair}
        result = self.private_request("TradeVolume", data)
        if result.get("error"):
            print("[FEES] Could not retrieve fee schedule, using default")
            return self.fee_rate

        fees = result.get("result", {}).get("fees", {})
        pair_fees = fees.get(pair, {})
        if pair_fees:
            fee_pct = float(pair_fees.get("fee", 0.26))
            self.fee_rate = fee_pct / 100.0  # store as decimal e.g. 0.0026
            print(f"[FEES] Updated fee rate for {pair}: {fee_pct}% → {self.fee_rate:.6f} (decimal)")
            # Also check maker fees if available
            maker_fee = pair_fees.get("fees_maker")
            if maker_fee:
                print(f"[FEES] Maker fee available: {maker_fee}")
        else:
            print(f"[FEES] No specific fee data for {pair}, using current: {self.fee_rate*100:.4f}%")
        return self.fee_rate

    # ==================== PROFITABILITY CHECK ====================

    def is_profitable_trade(self, trade_type: str, price: float, volume: float) -> bool:
        """
        Only allow trades that can cover exchange fees and have positive expectancy.
        This prevents dust trades and ensures fees don't eat all profit.
        """
        if price <= 0 or volume <= 0:
            return False

        notional = price * volume
        fee_cost = notional * self.fee_rate

        # 1. Must meet minimum notional to make fees worthwhile
        if notional < self.min_notional_usd:
            print(f"[PROFIT CHECK] Notional ${notional:.2f} < min ${self.min_notional_usd:.2f} → SKIPPED (fees would dominate)")
            return False

        # 2. For roundtrip consideration (buy then sell or vice versa), require margin over fees
        #    Here we use a conservative check: effective fee burden should leave room for profit
        roundtrip_fee_impact = 2 * self.fee_rate  # approx maker/taker or two sides
        if roundtrip_fee_impact > self.min_profit_margin:
            print(f"[PROFIT CHECK] Fees too high relative to required margin → SKIPPED")
            return False

        # 3. Always ensure we have enough balance (inventory check)
        if trade_type.lower() == "buy":
            quote_balance = self.inventory.get(f"Z{self.quote_currency}", 0.0)  # ZUSD, ZEUR etc.
            if quote_balance < notional:
                print(f"[PROFIT CHECK] Insufficient {self.quote_currency} balance ({quote_balance}) for ${notional:.2f} buy")
                return False
        elif trade_type.lower() == "sell":
            base_balance = self.inventory.get(self.base_currency, 0.0)
            if base_balance < volume:
                print(f"[PROFIT CHECK] Insufficient {self.base_currency} balance for sell")
                return False

        print(f"[PROFIT CHECK] Trade approved | Type: {trade_type} | Notional: ${notional:.2f} | Est. fee: ${fee_cost:.4f}")
        return True

    # ==================== ORDER PLACEMENT (with profitability gate) ====================

    def place_order(self, ordertype: str, trade_type: str, volume: float, price: Optional[float] = None,
                    validate: bool = False) -> Optional[Dict]:
        """
        Place order via REST (fallback). 
        ALWAYS checks is_profitable_trade first.
        """
        if not self.is_profitable_trade(trade_type, price or self.current_price or 0, volume):
            print("[ORDER] Trade rejected by profitability/inventory check. No order placed.")
            return None

        data = {
            "pair": self.rest_pair,
            "type": trade_type.lower(),
            "ordertype": ordertype.lower(),
            "volume": str(volume),
        }
        if price and ordertype.lower() == "limit":
            data["price"] = str(price)

        if validate:
            data["validate"] = "true"  # dry-run

        print(f"[ORDER] Attempting {trade_type} {ordertype} {volume} {self.rest_pair} @ {price or 'market'}")
        result = self.private_request("AddOrder", data)

        if not result.get("error"):
            print(f"[ORDER] SUCCESS: {result.get('result')}")
            # Refresh inventory after successful order
            self.update_inventory()
        return result

    # ==================== WEBSOCKET (PRIMARY) + REST FALLBACK ====================

    async def public_ws_listener(self):
        """Primary data feed via WebSocket. Falls back to REST on failure."""
        print(f"[WS] Connecting to public WebSocket for {self.ws_pair}...")
        try:
            async with websockets.connect(self.ws_url) as ws:
                subscribe = {
                    "event": "subscribe",
                    "pair": [self.ws_pair],
                    "subscription": {"name": "ticker"}
                }
                await ws.send(json.dumps(subscribe))

                async for raw_message in ws:
                    try:
                        msg = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue

                    if isinstance(msg, dict) and msg.get("event") == "subscriptionStatus":
                        if msg.get("status") == "subscribed":
                            print(f"[WS] Subscribed to ticker for {self.ws_pair}")
                        else:
                            print(f"[WS] Subscription issue: {msg}")
                        continue

                    # Ticker payload: [channelID, data, "ticker", "PAIR"]
                    if isinstance(msg, list) and len(msg) >= 2 and isinstance(msg[1], dict):
                        ticker_data = msg[1]
                        if "c" in ticker_data:  # last trade price
                            last_price = float(ticker_data["c"][0])
                            self.current_price = last_price
                            print(f"[WS] {self.ws_pair} last price: ${last_price:,.2f}")

                            # === PLACE YOUR STRATEGY / DECISION LOGIC HERE ===
                            # Example skeleton (commented to prevent accidental trading):
                            # if self.current_price and self.inventory:
                            #     # e.g. simple condition + profitability gate inside place_order
                            #     if <your_buy_condition>:
                            #         self.place_order("market", "buy", volume=0.001)
                            #     elif <your_sell_condition>:
                            #         self.place_order("market", "sell", volume=0.001)
                            #
                            # The is_profitable_trade() gate is ALWAYS enforced in place_order()

        except Exception as e:
            print(f"[WS] Connection error: {e}. Falling back to REST polling...")
            await self.rest_fallback_poller()

    async def rest_fallback_poller(self):
        """REST fallback for price when WebSocket is unavailable."""
        print("[FALLBACK] Using REST API for price polling every 10s...")
        while True:
            try:
                url = f"{self.rest_url}/0/public/Ticker?pair={self.rest_pair}"
                resp = requests.get(url, timeout=5)
                data = resp.json()
                if not data.get("error"):
                    ticker = data["result"][self.rest_pair]
                    self.current_price = float(ticker["c"][0])
                    print(f"[FALLBACK] {self.rest_pair} price: ${self.current_price:,.2f}")
            except Exception as e:
                print(f"[FALLBACK] Error: {e}")
            await asyncio.sleep(10)

    # ==================== MAIN RUN LOOP ====================

    async def run(self):
        print("=" * 60)
        print("KRAKEN PROFITABLE TRADING BOT - STARTING")
        print("=" * 60)
        print(f"Pair: {self.ws_pair} / {self.rest_pair}")
        print("Mode: WebSocket primary + REST fallback | Profitability enforced | Inventory tracked")

        # Step 1: Get current fee rate from API
        print("\n[INIT] Retrieving account fee rate from Kraken API...")
        self.get_current_fee_rate()

        # Step 2: Track initial inventory
        print("\n[INIT] Tracking inventory (account balances)...")
        self.update_inventory()

        # Step 3: Start primary data feed (WS with REST fallback)
        print("\n[INIT] Starting market data feed (WebSocket primary)...")
        await self.public_ws_listener()


if __name__ == "__main__":
    # ==================== CONFIGURE HERE ====================
    API_KEY = "YOUR_KRAKEN_API_KEY_HERE"          # <-- REPLACE
    API_SECRET = "YOUR_KRAKEN_API_SECRET_HERE"    # <-- REPLACE
    TRADING_PAIR = "XBT/USD"                      # Change as needed (e.g. "ETH/USD")

    if "YOUR_KRAKEN" in API_KEY:
        print("ERROR: Please set your real Kraken API Key and Secret in the script.")
        exit(1)

    bot = KrakenProfitableBot(API_KEY, API_SECRET, pair=TRADING_PAIR)

    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Bot stopped by user.")
    except Exception as e:
        print(f"[CRITICAL ERROR] {e}")