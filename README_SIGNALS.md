# DeepSeek Trading Signals (Live)

**AI-powered trading signals generator - No automatic execution.**

## What it does

- Scans market every 15 minutes (M15 candle opens)
- Analyzes multiple timeframes (M15, H1, H4) for confluence
- Uses AI to generate trading signals
- **Displays signals in real-time** (no auto-trading)

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
   python "DEEPSEEK SIGNALS.py"
   ```

## Output Example

```
📊 TRADING SIGNALS - 14 SYMBOLS ANALYZED

🟢 USDJPY      → BUY
   Entry: 156.69600 | SL: 156.43172 | TP: 156.96028
   Risk: 1.5% | R:R: 1.00:1

🔴 AUDCAD      → SELL
   Entry: 0.90645 | SL: 0.90900 | TP: 0.90400
   Risk: 2.0% | R:R: 0.96:1

⏸️  GBPJPY      → WAIT

📈 SIGNALS SUMMARY:
   🟢 BUY:  3
   🔴 SELL: 2
   ❌ CLOSE: 0
   ⏸️  WAIT: 9
```

## Configuration

- `TRADING_START_HOUR`: Start analysis hour (default: 2)
- `TRADING_END_HOUR`: End analysis hour (default: 21)
- `SCAN_INTERVAL`: Scan every 15 minutes (M15 candles)

## Strategy

- **Multi-timeframe confluence** (M15/H1/H4)
- **Mean reversion** setups
- **Dynamic risk suggestions** (0.5-2% per trade)
- **Scalping/Intraday** with R:R 0.3:1 to 1.5:1

## Note

💡 **This is a signal generator only. You need to execute trades manually.**

Perfect for traders who want AI analysis but prefer manual execution.

