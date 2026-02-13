# deepseek-api-trader-mt5
DeepSeek - MT5 Python API Trader

This is just a simple project and see how DeepSeek can manage a trading account using DeepSeek Reasoner model.

What it does

- Scans market every 15 minutes (M15 candle opens)
- Analyzes multiple timeframes (M15, H1, H4) for confluence
- Uses AI to make trading decisions
- Uses all symbols in your Market Watch

There are 2 files: 
- Signals: Send signal only
- DEEPSEEK: Trading on its own through MT5, fully independant

Requirements

pip install MetaTrader5 pandas numpy openai ta-lib

Note: You need MetaTrader 5 installed and running.

Please, adapt the prompt to your own strategy. Also, if needed, adapt the max_tokens. 

**Claude Version, and full insight: https://medium.com/@thibauld1263/table-of-contents-f92f9ae840de**
