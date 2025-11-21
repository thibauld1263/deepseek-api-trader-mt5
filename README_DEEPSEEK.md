# DeepSeek AI Trading Bot

**Automated AI-powered trading system using DeepSeek Reasoner model.**

## What it does

- Scans market every 15 minutes (M15 candle opens)
- Analyzes multiple timeframes (M15, H1, H4) for confluence
- Uses AI to make trading decisions
- **Automatically executes trades** on MT5

## Requirements

```bash
pip install MetaTrader5 pandas numpy ta-lib openai
```

**Note:** You need MetaTrader 5 installed and running.

## Setup

1. Open the file and set your DeepSeek API key:
   ```python
   DEEPSEEK_API_KEY = "your-api-key-here"
   ```

2. Make sure MT5 is running and logged in

3. Add symbols to Market Watch in MT5

4. Run:
   ```bash
   python DEEPSEEK.py
   ```

## Configuration

- `TRADING_START_HOUR`: Start trading hour (default: 2)
- `TRADING_END_HOUR`: End trading hour (default: 21)
- `RISK_PER_TRADE_MIN/MAX`: Risk per trade 0.5-2%
- `MAX_POSITIONS`: Maximum open positions (default: 20)
- `MAX_DAILY_LOSS`: Daily loss limit 25%

## Strategy

- **Multi-timeframe confluence** (M15/H1/H4)
- **Mean reversion** setups
- **Dynamic risk management** (AI decides 0.5-2% per trade)
- **Scalping/Intraday** with R:R 0.3:1 to 1.5:1

## Warning

⚠️ **This bot trades real money automatically. Use at your own risk!**

Start with a small account or demo account to test.

