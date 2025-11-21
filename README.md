# deepseek-api-trader-mt5
DeepSeek - MT5 Python API Trader

This is just a simple project and see how DeepSeek can manage a trading account using DeepSeek Reasoner model.

What it does

- Scans market every 15 minutes (M15 candle opens)
- Analyzes multiple timeframes (M15, H1, H4) for confluence
- Uses AI to make trading decisions

There are 2 files: 
- Signals only
- Trading on its own

Requirements

pip install MetaTrader5 pandas numpy ta-lib openai

Note: You need MetaTrader 5 installed and running.

PROMPT!

Please, adapt the prompt to your own strategy. Also, if needed, adapt the max_tokens. 
