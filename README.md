# Kraken Profitable Trading Bot

A production-ready Python trading bot for the Kraken cryptocurrency exchange that **only executes trades when they are profitable after fees**.

## ✨ Key Features

- **Profitability Gate** — Every order is validated against current fees, minimum notional size, and available balance before execution.
- **WebSocket First** — Real-time market data via WebSocket (`ticker`). Automatic fallback to REST polling if the connection drops.
- **Inventory Tracking** — Maintains live view of your account balances.
- **Dynamic Fee Retrieval** — Pulls your actual fee tier from Kraken's `TradeVolume` endpoint (respects your 30-day volume).
- **Secure Signing** — Proper nonce handling and HMAC-SHA512 signing for all private API calls.
- **Clean Architecture** — Easy to extend with your own strategy logic.

## 📋 Requirements

```bash
pip install requests websockets
```

## 🚀 Quick Start

1. **Get API Keys** from Kraken (https://www.kraken.com/u/security/api)
   - Required permissions:
     - Query Funds
     - Query Orders & Trades
     - Create & Modify Orders

2. **Configure the bot**

   Edit `kraken_profitable_trading_bot.py` and set:

   ```python
   API_KEY = "your_api_key"
   API_SECRET = "your_api_secret"
   TRADING_PAIR = "XBT/USD"   # or ETH/USD, SOL/USD, etc.
   ```

3. **Run the bot**

   ```bash
   python kraken_profitable_trading_bot.py
   ```

## 🛡️ Safety & Profitability Logic

The bot will **refuse** to place any order unless **all** of these are true:

- Trade notional ≥ `$50` (configurable)
- Estimated round-trip fees leave room for your defined profit margin
- Sufficient balance exists in the relevant currency (checked from live inventory)
- You have explicitly added strategy logic that calls `place_order()`

You control the strategy. The bot protects your capital.

## 🧠 Adding Your Strategy

Inside the WebSocket message handler you'll find:

```python
# === PLACE YOUR STRATEGY / DECISION LOGIC HERE ===
```

Examples you can implement:

- Simple price threshold / mean reversion
- Order book imbalance
- SMA / EMA crossover
- Funding rate arbitrage (if using futures)
- Custom indicators

The profitability + balance check is **always enforced** inside `place_order()`.

## 📁 Project Structure

```
kraken_profitable_trading_bot.py   # Main bot
README.md
.gitignore
```

## ⚠️ Important Warnings

- **This is real money trading.** Use at your own risk.
- Start with small sizes and `validate=True` (dry-run mode).
- Never commit your API keys.
- Kraken has rate limits — the bot respects them by design.
- Test thoroughly before going live.

## 🔧 Customization Ideas

- Add multiple trading pairs
- Implement private WebSocket feeds (`openOrders`, `fills`)
- Add stop-loss / take-profit / trailing stops
- Integrate with a database for trade logging
- Add Telegram / Discord alerts

## 📜 License

MIT — feel free to use and modify.

---

**Built with ❤️ for profitable, responsible trading on Kraken.**