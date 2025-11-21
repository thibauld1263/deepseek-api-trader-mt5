"""
DEEPSEEK-V3.2-Exp
SCALPER: R:R 0.3:1 to 1.5:1 | Quick entries/exits 
Multi-timeframe analysis 
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import talib
import datetime
import time
import json
import os
from openai import OpenAI
from typing import Dict, List, Optional, Tuple
from datetime import timedelta

# ==================== CONFIGURATION ====================
class Config:
    # DeepSeek API
    DEEPSEEK_API_KEY = "sk-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX" # Your DeepSeek API key
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    MODEL = "deepseek-reasoner"  # DeepSeek-R1 ("thinking" mode)
    
    # Trading Hours (Local Time in GMT+2)
    TRADING_START_HOUR = 2
    TRADING_END_HOUR = 21
    
    # Scanner Settings
    SCAN_INTERVAL = 900 # 15 minutes 
    
    # Risk Management
    INITIAL_BALANCE = 200.0  # Starting capital
    RISK_PER_TRADE_MIN = 0.5  # Minimum % risk per trade
    RISK_PER_TRADE_MAX = 2.0  # Maximum % risk per trade
    MAX_POSITIONS = 20
    MAX_DAILY_LOSS = 25.0  # % of account balance (stop trading for the day)
    
    # Auto-Trade
    AUTO_TRADE = False  # SIGNALS ONLY - No execution
    
    # Timeframes for multi-timeframe analysis
    TF_M15 = mt5.TIMEFRAME_M15
    TF_H1 = mt5.TIMEFRAME_H1
    TF_H4 = mt5.TIMEFRAME_H4
    
    BARS_NEEDED = 1000  # Plus d'historique pour meilleure analyse
    BARS_TO_SEND = 50  # Envoyer les 20 dernières bougies à DeepSeek
    
    # Performance tracking
    HISTORY_FILE = "deepseek_trading_history.json"  # Store all trades
    MAX_HISTORY_IN_PROMPT = 50  # Last N trades to send to DeepSeek

# ==================== MT5 CONNECTION MANAGER ====================
class MT5Manager:
    def __init__(self):
        self.connected = False
        self.account_info = None
        
    def connect(self) -> bool:
        """Connect to MetaTrader 5"""
        if not mt5.initialize():
            print(" ERROR: Cannot connect to MT5!")
            return False
        
        self.account_info = mt5.account_info()
        self.connected = True
        
        print("=" * 80)
        print("🏦 MT5 CONNECTION ESTABLISHED")
        print("=" * 80)
        print(f"Account: {self.account_info.login}")
        print(f"Broker: {self.account_info.company}")
        print(f"Balance: ${self.account_info.balance:,.2f}")
        print(f"Equity: ${self.account_info.equity:,.2f}")
        print(f"Currency: {self.account_info.currency}")
        print("=" * 80)
        
        return True
    
    def disconnect(self):
        """Disconnect from MT5"""
        mt5.shutdown()
        self.connected = False
    
    def get_market_watch_symbols(self) -> List[str]:
        """Get all visible symbols from Market Watch"""
        symbols = mt5.symbols_get()
        if symbols is None:
            return []
        return [s.name for s in symbols if s.visible]

# ==================== POSITION MANAGER ====================
class PositionManager:
    def __init__(self, performance_tracker: Optional['PerformanceTracker'] = None):
        self.positions: Dict[str, Dict] = {}
        self.closed_positions_today: List[Dict] = []
        self.daily_pnl = 0.0
        self.performance_tracker = performance_tracker
        
    def update_positions(self):
        """Fetch and update all open positions from MT5"""
        self.positions.clear()
        positions = mt5.positions_get()
        
        if positions:
            for pos in positions:
                pos_type = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                self.positions[pos.symbol] = {
                    'ticket': pos.ticket,
                    'symbol': pos.symbol,
                    'type': pos_type,
                    'type_mt5': pos.type,
                    'volume': pos.volume,
                    'entry': pos.price_open,
                    'current': pos.price_current,
                    'sl': pos.sl,
                    'tp': pos.tp,
                    'profit': pos.profit,
                    'swap': pos.swap,
                    'open_time': datetime.datetime.fromtimestamp(pos.time)
                }
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        """Get position for specific symbol"""
        return self.positions.get(symbol)
    
    def has_position(self, symbol: str) -> bool:
        """Check if symbol has open position"""
        return symbol in self.positions
    
    def get_positions_summary(self) -> str:
        """Get formatted summary of all positions"""
        if not self.positions:
            return "No open positions"
        
        summary = []
        total_pnl = 0.0
        
        for symbol, pos in self.positions.items():
            total_pnl += pos['profit']
            summary.append(
                f"{symbol}: {pos['type']} {pos['volume']} lots @ {pos['entry']:.5f} | "
                f"Current: {pos['current']:.5f} | P&L: ${pos['profit']:+.2f}"
            )
        
        summary.append(f"\nTotal P&L: ${total_pnl:+.2f}")
        return "\n".join(summary)
    
    def close_position(self, symbol: str) -> bool:
        """Close position for symbol"""
        if not self.has_position(symbol):
            return False
        
        pos = self.positions[symbol]
        tick = mt5.symbol_info_tick(symbol)
        
        close_type = mt5.ORDER_TYPE_SELL if pos['type'] == 'BUY' else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if pos['type'] == 'BUY' else tick.ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos['volume'],
            "type": close_type,
            "position": pos['ticket'],
            "price": close_price,
            "deviation": 20,
            "magic": 234000,
            "comment": "AI Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f" Closed {symbol} position | P&L: ${pos['profit']:+.2f}")
            
            # Record trade closure in performance tracker
            if self.performance_tracker:
                self.performance_tracker.close_trade(symbol, close_price, pos['profit'])
            
            # Track closed position
            self.closed_positions_today.append({
                'symbol': symbol,
                'type': pos['type'],
                'profit': pos['profit'],
                'close_time': datetime.datetime.now()
            })
            
            self.daily_pnl += pos['profit']
            del self.positions[symbol]
            return True
        else:
            print(f" Failed to close {symbol}: {result.comment}")
            return False

# ==================== ECONOMIC NEWS MANAGER ====================
class EconomicNewsManager:
    """Manage economic calendar and news events from MT5"""
    
    # Currency mapping for symbols (e.g., EURUSD -> EUR, USD)
    CURRENCY_MAP = {
        'EURUSD': ['EUR', 'USD'],
        'GBPUSD': ['GBP', 'USD'],
        'USDJPY': ['USD', 'JPY'],
        'USDCHF': ['USD', 'CHF'],
        'AUDUSD': ['AUD', 'USD'],
        'USDCAD': ['USD', 'CAD'],
        'NZDUSD': ['NZD', 'USD'],
        'XAUUSD': ['USD'],  # Gold affected by USD
        'US30': ['USD'],
        'US500': ['USD'],
        'USTEC': ['USD'],
        'DE40': ['EUR', 'USD'],
    }
    
    @staticmethod
    def get_currency_codes(symbol: str) -> List[str]:
        """Get currency codes for a symbol"""
        # Remove suffix (e.g., EURUSDm -> EURUSD)
        clean_symbol = symbol.replace('m', '').replace('.', '').upper()
        
        # Try exact match
        if clean_symbol in EconomicNewsManager.CURRENCY_MAP:
            return EconomicNewsManager.CURRENCY_MAP[clean_symbol]
        
        # Try partial match
        for key, currencies in EconomicNewsManager.CURRENCY_MAP.items():
            if key in clean_symbol or clean_symbol in key:
                return currencies
        
        # Default: assume first 3 chars are base, next 3 are quote
        if len(clean_symbol) >= 6:
            return [clean_symbol[:3], clean_symbol[3:6]]
        
        return []
    
    @staticmethod
    def get_recent_news(symbol: str, hours_back: int = 24, hours_ahead: int = 8) -> List[Dict]:
        """
        Get economic news for currencies related to symbol
        
        Args:
            symbol: Trading symbol (e.g., EURUSD)
            hours_back: How many hours back to check
            hours_ahead: How many hours ahead to check
            
        Returns:
            List of news events with impact levels
        """
        try:
            # Check if calendar functions are available
            if not hasattr(mt5, 'calendar_by_time'):
                # Calendar not available in this MT5 version
                return []
            
            # Get currency codes for this symbol
            currencies = EconomicNewsManager.get_currency_codes(symbol)
            if not currencies:
                return []
            
            # Time range
            now = datetime.datetime.now()
            time_from = int((now - timedelta(hours=hours_back)).timestamp())
            time_to = int((now + timedelta(hours=hours_ahead)).timestamp())
            
            # Get calendar events from MT5
            calendar_records = mt5.calendar_by_time(time_from, time_to)
            
            if calendar_records is None or len(calendar_records) == 0:
                return []
            
            # Filter events by currency and importance
            relevant_news = []
            for record in calendar_records:
                # Get country info
                country = mt5.calendar_country_by_id(record.country_id)
                if country is None:
                    continue
                
                country_code = country.code  # e.g., "US", "EU", "GB", "JP"
                
                # Map country code to currency
                currency_match = False
                for currency in currencies:
                    if EconomicNewsManager._country_matches_currency(country_code, currency):
                        currency_match = True
                        break
                
                if not currency_match:
                    continue
                
                # Get event time
                event_time = datetime.datetime.fromtimestamp(record.time)
                time_diff = (event_time - now).total_seconds() / 3600  # Hours
                
                # Determine importance level
                importance = "LOW"
                if record.importance == mt5.CALENDAR_IMPORTANCE_HIGH:
                    importance = "HIGH"
                elif record.importance == mt5.CALENDAR_IMPORTANCE_MODERATE:
                    importance = "MEDIUM"
                
                # Only include MEDIUM and HIGH importance
                if importance in ["MEDIUM", "HIGH"]:
                    relevant_news.append({
                        'name': record.name,
                        'country': country.name,
                        'currency': country_code,
                        'time': event_time.strftime('%Y-%m-%d %H:%M'),
                        'time_diff_hours': round(time_diff, 1),
                        'importance': importance,
                        'has_actual': hasattr(record, 'actual_value') and record.actual_value is not None,
                        'actual': getattr(record, 'actual_value', None),
                        'forecast': getattr(record, 'forecast_value', None),
                        'previous': getattr(record, 'prev_value', None)
                    })
            
            # Sort by time (most recent first)
            relevant_news.sort(key=lambda x: x['time'], reverse=True)
            
            return relevant_news[:10]  # Return max 10 most relevant
            
        except Exception as e:
            # Silently fail - news not critical for trading
            return []
    
    @staticmethod
    def _country_matches_currency(country_code: str, currency: str) -> bool:
        """Match country code to currency"""
        mapping = {
            'US': 'USD',
            'EU': 'EUR',
            'GB': 'GBP',
            'JP': 'JPY',
            'CH': 'CHF',
            'AU': 'AUD',
            'CA': 'CAD',
            'NZ': 'NZD',
            'DE': 'EUR',  # Germany -> EUR
            'FR': 'EUR',  # France -> EUR
            'IT': 'EUR',  # Italy -> EUR
        }
        
        return mapping.get(country_code, '') == currency
    
    @staticmethod
    def format_news_summary(news_list: List[Dict]) -> str:
        """Format news into readable summary for AI"""
        if not news_list:
            return "No major economic news in the past 24h or next 8h (or calendar unavailable)."
        
        lines = ["NEWS & ECONOMIC EVENTS:"]
        
        # Separate past and upcoming
        past_news = [n for n in news_list if n['time_diff_hours'] < 0]
        upcoming_news = [n for n in news_list if n['time_diff_hours'] >= 0]
        
        if past_news:
            lines.append("\nRECENT (Past 24h):")
            for news in past_news[:5]:
                impact = "[HIGH]" if news['importance'] == "HIGH" else "[MED]"
                time_ago = abs(news['time_diff_hours'])
                lines.append(f"   {impact} {news['name']} ({news['currency']}) - {time_ago:.1f}h ago")
                if news['has_actual']:
                    lines.append(f"      Actual: {news['actual']} | Forecast: {news['forecast']} | Prev: {news['previous']}")
        
        if upcoming_news:
            lines.append("\nUPCOMING (Next 8h):")
            for news in upcoming_news[:5]:
                impact = "[HIGH]" if news['importance'] == "HIGH" else "[MED]"
                time_until = news['time_diff_hours']
                lines.append(f"   {impact} {news['name']} ({news['currency']}) - in {time_until:.1f}h")
                if news['forecast']:
                    lines.append(f"      Forecast: {news['forecast']} | Prev: {news['previous']}")
        
        return "\n".join(lines)

# ==================== PERFORMANCE TRACKER ====================
class PerformanceTracker:
    """Track and analyze trading performance for DeepSeek learning"""
    
    def __init__(self, history_file: str = Config.HISTORY_FILE):
        self.history_file = history_file
        self.trades = []
        self.load_history()
    
    def load_history(self):
        """Load trading history from file"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r') as f:
                    data = json.load(f)
                    self.trades = data.get('trades', [])
                    print(f"   Loaded {len(self.trades)} historical trades")
        except Exception as e:
            print(f"   Could not load history: {e}")
            self.trades = []
    
    def save_history(self):
        """Save trading history to file"""
        try:
            data = {
                'trades': self.trades,
                'last_updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'total_trades': len(self.trades)
            }
            with open(self.history_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"   Could not save history: {e}")
    
    def record_trade(
        self,
        symbol: str,
        action: str,
        entry: float,
        sl: float,
        tp: float,
        volume: float,
        strategy: str,
        confidence: int,
        reasoning: str
    ):
        """Record a new trade entry"""
        trade = {
            'id': len(self.trades) + 1,
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': symbol,
            'action': action,
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'volume': volume,
            'strategy': strategy,
            'confidence': confidence,
            'reasoning': reasoning,
            'status': 'OPEN',
            'exit_price': None,
            'pnl': None,
            'pips': None,
            'exit_time': None,
            'duration_minutes': None
        }
        self.trades.append(trade)
        self.save_history()
        return trade['id']
    
    def close_trade(
        self,
        symbol: str,
        exit_price: float,
        pnl: float
    ):
        """Update trade when closed"""
        # Find last open trade for this symbol
        for trade in reversed(self.trades):
            if trade['symbol'] == symbol and trade['status'] == 'OPEN':
                trade['status'] = 'CLOSED'
                trade['exit_price'] = exit_price
                trade['pnl'] = pnl
                
                # Calculate pips
                if trade['action'] == 'BUY':
                    trade['pips'] = (exit_price - trade['entry']) * 10000  # Rough pip calculation
                else:
                    trade['pips'] = (trade['entry'] - exit_price) * 10000
                
                # Calculate duration
                entry_time = datetime.datetime.strptime(trade['timestamp'], '%Y-%m-%d %H:%M:%S')
                exit_time = datetime.datetime.now()
                trade['exit_time'] = exit_time.strftime('%Y-%m-%d %H:%M:%S')
                trade['duration_minutes'] = int((exit_time - entry_time).total_seconds() / 60)
                
                self.save_history()
                break
    
    def get_recent_performance(self, n: int = 10) -> Dict:
        """Get stats for last N trades"""
        if not self.trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'avg_pnl': 0,
                'recent_trades': []
            }
        
        # Get closed trades only
        closed_trades = [t for t in self.trades if t['status'] == 'CLOSED'][-n:]
        
        if not closed_trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'avg_pnl': 0,
                'recent_trades': []
            }
        
        wins = len([t for t in closed_trades if t['pnl'] and t['pnl'] > 0])
        losses = len([t for t in closed_trades if t['pnl'] and t['pnl'] < 0])
        total = wins + losses
        
        win_rate = (wins / total * 100) if total > 0 else 0
        avg_pnl = sum([t.get('pnl', 0) for t in closed_trades]) / len(closed_trades)
        total_pnl = sum([t.get('pnl', 0) for t in closed_trades])
        
        return {
            'total_trades': len(closed_trades),
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'avg_pnl': avg_pnl,
            'total_pnl': total_pnl,
            'recent_trades': closed_trades
        }
    
    def format_performance_summary(self, n: int = Config.MAX_HISTORY_IN_PROMPT) -> str:
        """Format performance summary for DeepSeek"""
        stats = self.get_recent_performance(n)
        
        if stats['total_trades'] == 0:
            return "TRADING HISTORY: No previous trades recorded yet."
        
        lines = [
            "TRADING HISTORY & PERFORMANCE:",
            f"Last {stats['total_trades']} trades: {stats['wins']} wins, {stats['losses']} losses",
            f"Win Rate: {stats['win_rate']:.1f}%",
            f"Total P&L: ${stats['total_pnl']:+.2f} | Avg P&L: ${stats['avg_pnl']:+.2f}",
            "\nRECENT TRADES:"
        ]
        
        for trade in stats['recent_trades'][-5:]:  # Last 5 trades
            result = "WIN" if trade.get('pnl', 0) > 0 else "LOSS"
            result_emoji = "✅" if result == "WIN" else "❌"
            
            lines.append(
                f"   {result_emoji} {trade['symbol']} {trade['action']} | "
                f"Strategy: {trade.get('strategy', 'N/A')} | "
                f"P&L: ${trade.get('pnl', 0):+.2f} ({trade.get('pips', 0):+.1f} pips) | "
                f"Duration: {trade.get('duration_minutes', 0)}min"
            )
            
            # Add reasoning for losses (learn from mistakes)
            if result == "LOSS" and trade.get('reasoning'):
                lines.append(f"      Original reasoning: {trade['reasoning'][:100]}...")
        
        lines.append("\nLEARN FROM MISTAKES:")
        recent_losses = [t for t in stats['recent_trades'] if t.get('pnl', 0) < 0][-3:]
        if recent_losses:
            for loss in recent_losses:
                lines.append(f"   - {loss['symbol']} {loss['strategy']}: Lost ${abs(loss['pnl']):.2f}")
        else:
            lines.append("   - No recent losses! Keep the winning streak!")
        
        return "\n".join(lines)

# ==================== TECHNICAL ANALYSIS ====================
class TechnicalAnalyzer:
    @staticmethod
    def get_multi_timeframe_data(symbol: str) -> Optional[Dict]:
        """Fetch and analyze data from multiple timeframes"""
        try:
            # Fetch ALL candles including current forming candle
            # Position 0 = current candle (may be forming on H1/H4)
            rates_m15 = mt5.copy_rates_from_pos(symbol, Config.TF_M15, 0, Config.BARS_NEEDED)
            rates_h1 = mt5.copy_rates_from_pos(symbol, Config.TF_H1, 0, Config.BARS_NEEDED)
            rates_h4 = mt5.copy_rates_from_pos(symbol, Config.TF_H4, 0, Config.BARS_NEEDED)
            
            if any(r is None or len(r) < 100 for r in [rates_m15, rates_h1, rates_h4]):
                return None
            
            # Convert to DataFrames
            df_m15 = pd.DataFrame(rates_m15)
            df_h1 = pd.DataFrame(rates_h1)
            df_h4 = pd.DataFrame(rates_h4)
            
            # Analyze each timeframe
            analysis = {
                'symbol': symbol,
                'm15': TechnicalAnalyzer._analyze_timeframe(df_m15, 'M15'),
                'h1': TechnicalAnalyzer._analyze_timeframe(df_h1, 'H1'),
                'h4': TechnicalAnalyzer._analyze_timeframe(df_h4, 'H4'),
                'pivots': TechnicalAnalyzer._calculate_pivots(symbol),
                'key_levels': TechnicalAnalyzer._get_key_levels(symbol)
            }
            
            return analysis
            
        except Exception as e:
            print(f" Error analyzing {symbol}: {e}")
            return None
    
    @staticmethod
    def _analyze_timeframe(df: pd.DataFrame, tf_name: str) -> Dict:
        """Analyze single timeframe"""
        close = df['close'].values.astype(np.float64)
        high = df['high'].values.astype(np.float64)
        low = df['low'].values.astype(np.float64)
        open_price = df['open'].values.astype(np.float64)
        volume = df['tick_volume'].values.astype(np.float64)
        
        # Price action
        current_price = close[-1]
        
        # Moving Averages
        ema9 = talib.EMA(close, 9)[-1]
        ema21 = talib.EMA(close, 21)[-1]
        ema50 = talib.EMA(close, 50)[-1]
        ema200 = talib.EMA(close, 200)[-1]
        sma20 = talib.SMA(close, 20)[-1]
        
        # VWAP (approximation with tick volume)
        vwap = np.sum(close * volume) / np.sum(volume)
        
        # Trend determination
        if current_price > ema200 and ema50 > ema200:
            trend = "STRONG BULLISH"
        elif current_price > ema200:
            trend = "BULLISH"
        elif current_price < ema200 and ema50 < ema200:
            trend = "STRONG BEARISH"
        elif current_price < ema200:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
        
        # Momentum indicators
        rsi = talib.RSI(close, 14)[-1]
        macd, signal, hist = talib.MACD(close, 12, 26, 9)
        macd_trend = "BULLISH" if macd[-1] > signal[-1] else "BEARISH"
        macd_hist = hist[-1]
        
        # Volatility
        atr = talib.ATR(high, low, close, 14)[-1]
        bb_upper, bb_mid, bb_lower = talib.BBANDS(close, 20, 2, 2)
        bb_width = (bb_upper[-1] - bb_lower[-1]) / bb_mid[-1] * 100
        
        # Distance to bands
        bb_position = (current_price - bb_lower[-1]) / (bb_upper[-1] - bb_lower[-1]) * 100 if (bb_upper[-1] - bb_lower[-1]) > 0 else 50
        
        # Strength
        adx = talib.ADX(high, low, close, 14)[-1]
        plus_di = talib.PLUS_DI(high, low, close, 14)[-1]
        minus_di = talib.MINUS_DI(high, low, close, 14)[-1]
        
        # Oscillators
        stoch_k, stoch_d = talib.STOCH(high, low, close, 14, 3, 0, 3, 0)
        
        # CCI
        cci = talib.CCI(high, low, close, 14)[-1]
        
        # Williams %R
        willr = talib.WILLR(high, low, close, 14)[-1]
        
        # Volume analysis
        vol_sma = pd.Series(volume).rolling(20).mean().iloc[-1]
        vol_spike = (volume[-1] / vol_sma - 1) * 100 if vol_sma > 0 else 0
        
        # OBV (On Balance Volume)
        obv = talib.OBV(close, volume)[-1]
        
        # Market structure
        swing_highs = pd.Series(high).rolling(10).max()
        swing_lows = pd.Series(low).rolling(10).min()
        structure = "BULLISH STRUCTURE" if high[-1] > swing_highs.iloc[-5] else \
                   "BEARISH STRUCTURE" if low[-1] < swing_lows.iloc[-5] else "CONSOLIDATION"
        
        # Recent bars
        recent_bars = []
        num_bars = min(Config.BARS_TO_SEND, len(df))
        for i in range(num_bars):
            idx = -(num_bars - i)
            bar = {
                'open': df['open'].iloc[idx],
                'high': df['high'].iloc[idx],
                'low': df['low'].iloc[idx],
                'close': df['close'].iloc[idx],
                'volume': df['tick_volume'].iloc[idx]
            }
            recent_bars.append(bar)
        
        # Support/Resistance
        resistance_levels = []
        support_levels = []
        
        for i in range(20, len(high) - 20):
            if high[i] == max(high[i-20:i+20]):
                resistance_levels.append(high[i])
            if low[i] == min(low[i-20:i+20]):
                support_levels.append(low[i])
        
        resistance_levels = sorted(set(resistance_levels))[-3:] if resistance_levels else []
        support_levels = sorted(set(support_levels), reverse=True)[:3] if support_levels else []
        
        # Candlestick Patterns
        patterns = []
        
        if talib.CDLDOJI(open_price, high, low, close)[-1] != 0:
            patterns.append("DOJI")
        
        if talib.CDLENGULFING(open_price, high, low, close)[-1] > 0:
            patterns.append("BULLISH_ENGULFING")
        elif talib.CDLENGULFING(open_price, high, low, close)[-1] < 0:
            patterns.append("BEARISH_ENGULFING")
        
        if talib.CDLHAMMER(open_price, high, low, close)[-1] != 0:
            patterns.append("HAMMER")
        
        if talib.CDLSHOOTINGSTAR(open_price, high, low, close)[-1] != 0:
            patterns.append("SHOOTING_STAR")
        
        if talib.CDLMORNINGSTAR(open_price, high, low, close)[-1] != 0:
            patterns.append("MORNING_STAR")
        if talib.CDLEVENINGSTAR(open_price, high, low, close)[-1] != 0:
            patterns.append("EVENING_STAR")
        
        return {
            'price': current_price,
            'trend': trend,
            'ema9': ema9,
            'ema21': ema21,
            'ema50': ema50,
            'ema200': ema200,
            'sma20': sma20,
            'vwap': vwap,
            'rsi': rsi,
            'macd_trend': macd_trend,
            'macd_hist': macd_hist,
            'atr': atr,
            'bb_width': bb_width,
            'bb_upper': bb_upper[-1],
            'bb_mid': bb_mid[-1],
            'bb_lower': bb_lower[-1],
            'bb_position': bb_position,
            'adx': adx,
            'plus_di': plus_di,
            'minus_di': minus_di,
            'stoch_k': stoch_k[-1],
            'stoch_d': stoch_d[-1],
            'cci': cci,
            'willr': willr,
            'vol_spike': vol_spike,
            'obv': obv,
            'structure': structure,
            'recent_bars': recent_bars,
            'support_levels': support_levels,
            'resistance_levels': resistance_levels,
            'candlestick_patterns': patterns
        }
    
    @staticmethod
    def _calculate_pivots(symbol: str) -> Dict:
        """Calculate daily pivot points"""
        prev_day = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 1, 1)
        if prev_day is None or len(prev_day) == 0:
            return {}
        
        prev = prev_day[0]
        pivot = (prev['high'] + prev['low'] + prev['close']) / 3
        r1 = 2 * pivot - prev['low']
        r2 = pivot + (prev['high'] - prev['low'])
        r3 = prev['high'] + 2 * (pivot - prev['low'])
        s1 = 2 * pivot - prev['high']
        s2 = pivot - (prev['high'] - prev['low'])
        s3 = prev['low'] - 2 * (prev['high'] - pivot)
        
        return {
            'pivot': pivot,
            'r1': r1, 'r2': r2, 'r3': r3,
            's1': s1, 's2': s2, 's3': s3
        }
    
    @staticmethod
    def _get_key_levels(symbol: str) -> Dict:
        """Get key price levels"""
        # Daily levels
        daily = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 5)
        weekly = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_W1, 0, 2)
        
        if daily is None or weekly is None:
            return {}
        
        return {
            'daily_open': daily[0]['open'],
            'daily_high': daily[0]['high'],
            'daily_low': daily[0]['low'],
            'weekly_open': weekly[0]['open'],
            'prev_day_high': daily[1]['high'],
            'prev_day_low': daily[1]['low'],
            'prev_day_close': daily[1]['close']
        }

# ==================== DEEPSEEK-V3.2-Exp TRADING ENGINE ====================
class GPTTradingEngine:
    def __init__(self, api_key: str, base_url: str, performance_tracker: 'PerformanceTracker'):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = Config.MODEL
        self.conversation_history = []
        self.performance_tracker = performance_tracker
        
    def analyze_portfolio(
        self,
        all_analysis: Dict[str, Dict],
        position_manager: PositionManager
    ) -> List[Dict]:
        """Analyze ALL symbols at once - ONE API call for ALL symbols"""
        
        # Build massive portfolio prompt
        prompt = self._build_portfolio_prompt(all_analysis, position_manager)
        
        try:
            print(f"\n[INFO] Sending portfolio analysis to DeepSeek...")
            print(f"   Analyzing {len(all_analysis)} symbols simultaneously")
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=4000
            )
            
            # Handle thinking model response
            message = response.choices[0].message
            decision_text = message.content
            
            # Extract reasoning if available
            reasoning_content = ""
            if hasattr(message, 'reasoning_content') and message.reasoning_content:
                reasoning_content = message.reasoning_content
                print(f"\n{'=' * 80}")
                print(f"🧠 DEEPSEEK FULL REASONING:")
                print(f"{'=' * 80}")
                print(reasoning_content)
                print(f"{'=' * 80}\n")
            
            # Also print content if available
            if decision_text and len(decision_text.strip()) > 0:
                print(f"\n{'=' * 80}")
                print(f"📝 DEEPSEEK CONTENT:")
                print(f"{'=' * 80}")
                print(decision_text)
                print(f"{'=' * 80}\n")
            
            # Try content first, then reasoning
            if not decision_text or len(decision_text.strip()) < 50:
                if reasoning_content:
                    print(f"   📥 Parsing from reasoning_content...")
                    decision_text = reasoning_content
                else:
                    print(f"   ❌ Empty response")
                    return []
            
            # Parse MULTIPLE decisions from response
            decisions = self._parse_portfolio_decisions(decision_text, list(all_analysis.keys()))
            
            print(f"\n[SUCCESS] DeepSeek returned {len(decisions)} trading decisions")
            
            return decisions
            
        except Exception as e:
            print(f" Error calling DeepSeek: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _get_system_prompt(self) -> str:
        """Get the core system prompt for DeepSeek-V3.2-Exp"""
        return """You are an ELITE QUANT TRADER managing a REAL growth challenge.

YOUR ONLY GOAL: Grow your account through MULTI-TIMEFRAME CONFLUENCE trading.

DYNAMIC RISK (YOU DECIDE 0.5-2%):
Choose your risk per trade based on confluence strength:
- 2%: PERFECT 3 TF confluence, multiple indicators aligned
- 1.5%: Strong 2 TF confluence, A Setup
- 1%: Solid 2 TF confluence, B+ Setup
- 0.5%: Weak confluence, B Setup

AVOID TRADING FIRST 15 MIN OF ANY SESSION. YOU ENTER USING THE M15 PRICES.

⚠️ CRITICAL: ALWAYS USE M15 PRICES FOR ENTRY ⚠️
- M15 candle is CLOSED and CONFIRMED
- H1 and H4 candles may STILL BE FORMING (incomplete data)
- You MUST use M15 current price for ENTRY, SL, and TP
- H1/H4 are for ANALYSIS ONLY, NOT for entry prices

📊 DATA YOU RECEIVE:
- You get the last {BARS_TO_SEND} candles for EACH timeframe (M15, H1, H4)
- M15: Last candle is the most recent CLOSED M15 candle
- H1: Current candle may still be forming (not closed yet)
- H4: Current candle may still be forming (not closed yet)
- Use recent bars to spot micro-trends, rejections, wicks, patterns

YOUR TRADING METHOD - MULTI-TIMEFRAME CONFLUENCE:

You analyze M15, H1, H4 timeframes simultaneously. You are a professional trader with a lot of experience. 
You are not afraid to take risks, as long as they are under control and you are confident in your analysis. 
You have no emotional attachment to the market.  
You are active, you are smart, you are patient, you are consistent, you are focused on the long term growth of your account.

WHAT YOU LOOK FOR:
1. MEAN REVERSION SETUPS:
   - Price stretched from mean (EMAs/VWAP), Support/Resistance levels, Pivot points, etc.
   - Multiple TFs showing recent oversold/overbought conditions, exhaustion, mean reversion, etc.
   - Convergence of indicators across timeframes
   - A signal is forming (For example: a bullish or bearish Heikin Ashi reversal candle, stochastic are crossing, etc.)
   - The signal is confirmed by multiple indicators across timeframes.
   - Major trend is in the direction of the signal.
   - A CLEAR SIGNAL TO ENTER A TRADE. YOU ALWAYS TRADE USING THE M15 PRICES. H1 OR H4 CANDLE MIGHT STILL BE FORMING.
   
2. CONFLUENCE INDICATORS (use ANY combination, but solid setups using multiple indicators confluence):
   - Heikin Ashi: Trend exhaustion, reversal candles
   - Stochastic: Recent reversals in overbought/oversold zones on multiple TFs
   - MACD: Divergences, histogram reversals
   - RSI: Extreme levels in overbought/oversold zones + divergences
   - EMAs: Price distance, rejections, alignments
   - ATR: Volatility expansion/contraction
   - Bollinger Bands: Squeezes, breakouts
   - Price actions patterns
   - CANDLESTICK PATTERNS: Doji, Engulfing, Hammer, Shooting Star, Morning/Evening Star
   - SUPPORT/RESISTANCE: Price bouncing off levels, breakouts/breakdowns
   - RECENT CANDLES: Look at last 5 M15 candles for micro-trends, wicks, rejection patterns

YOUR PROCESS:
1. Check EACH timeframe (M15, H1, H4) for EACH symbol
2. Identify indicator states (Stoch, RSI, MACD, HA, etc)
3. Find CONFLUENCE - do 2+ TFs agree on direction?
4. If YES → TRADE
5. If NO confluence → WAIT

RULES - CFD TRADING:
- We trade CFDs (forex, indices, metals, crypto)
- For forex: use price format like 1.31520
- For indices: use price format like 24550
- R:R: 0.3:1 to 1.5:1 (mean reversion intraday / scalping)
- SL: Based on ATR and key levels
- TP: Mean (VWAP/EMA) or next key level
- HIGH news <2h: skip or reduce risk significantly
- ALWAYS ENTER AT M15 PRICES! ALWAYS ENTER IN THE DIRECTION OF THE MAJOR TREND.

CRITICAL OUTPUT FORMAT:

⚠️⚠️⚠️ THIS IS THE MOST IMPORTANT PART ⚠️⚠️⚠️

YOU MUST END YOUR REASONING WITH THE DECISIONS IN THIS EXACT FORMAT:

After your analysis and thinking, you MUST write:

FINAL DECISIONS:
(SYMBOL ACTION ENTRY SL TP RISK)
(SYMBOL ACTION ENTRY SL TP RISK)
...one line per symbol...

DO NOT FORGET TO WRITE "FINAL DECISIONS:" AT THE END OF YOUR RESPONSE!

EXAMPLE OF COMPLETE RESPONSE:

[Your thinking/analysis here...]
I'll analyze each symbol for confluence...
USDJPY shows oversold on M15 RSI 25...
[more analysis...]

Now I'll format my decisions:

FINAL DECISIONS:
(EURUSD BUY 1.0850 SL 1.0820 TP 1.0890 RISK 1.5)
(GBPUSD WAIT)
(USDJPY WAIT)
(XAUUSD BUY 4056.50 SL 4045.0 TP 4075.0 RISK 2.0)
(US30 WAIT)
(US500 WAIT)

⚠️ THE "FINAL DECISIONS:" SECTION IS MANDATORY - DO NOT SKIP IT!

CRITICAL: Output EXACTLY ONE DECISION PER SYMBOL. NOT MORE.

YOUR RESPONSE STRUCTURE:

1. First, write your BRIEF analysis/reasoning (optional, 2-5 lines max)
2. Then write EXACTLY this line: `FINAL DECISIONS:`
3. Then write EXACTLY lines - ONE per symbol

FORMAT AFTER "FINAL DECISIONS:":

SYMBOL_NAME ACTION ENTRY SL TP RISK

ACTIONS:
- BUY: Buy signal (open NEW)
- SELL: Sell signal (open NEW)
- CLOSE_ALL: Close 
- WAIT: No action

EXAMPLE RESPONSE (NO OPEN POSITIONS):

FINAL DECISIONS:
EURUSD BUY 1.0850 SL 1.0820 TP 1.0890 RISK 1.5
GBPUSD BUY 1.2700 SL 1.2670 TP 1.2750 RISK 1.5
US30 WAIT
US500 WAIT
XAUUSD WAIT

EXAMPLE RESPONSE (WITH OPEN POSITIONS):

FINAL DECISIONS:
EURUSD WAIT
GBPUSD CLOSE_ALL
US30 WAIT
US500 BUY 5850.5 SL 5840.0 TP 5870.0 RISK 1.5
XAUUSD WAIT

MANDATORY RULES:

1. Write "FINAL DECISIONS:" before your trading decisions
2. EXACTLY ONE LINE PER SYMBOL after "FINAL DECISIONS:" (lines ONLY)
3. Use REAL prices from the data (not placeholders)
4. ALL symbols MUST appear (even if WAIT)
5. DO NOT give multiple BUY/SELL for same symbol
6. CLOSE_ALL closes ALL positions for that symbol
7. SL MUST be: BUY → SL below entry | SELL → SL above entry
8. TP MUST be: BUY → TP above entry | SELL → TP below entry
9. Risk 0.5-2% per trade
10. If symbol has OPEN position → ONLY use WAIT or CLOSE_ALL (NO BUY/SELL) 

You output: ONE line per symbol

REMEMBER: Write "FINAL DECISIONS:" then EXACTLY N lines (one per symbol, where N = number of symbols analyzed).

ACCUMULATE SMALL WINS. PROTECT CAPITAL. GROW STEADILY. 🚀"""

    def _build_portfolio_prompt(
        self,
        all_analysis: Dict[str, Dict],
        position_manager: PositionManager
    ) -> str:
        """Build MASSIVE portfolio prompt with ALL symbols"""
        
        now = datetime.datetime.now()
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        sections = []
        
        # Account status
        account = mt5.account_info()
        current_balance = account.balance if account else 0
        initial_balance = Config.INITIAL_BALANCE
        growth_pct = ((current_balance - initial_balance) / initial_balance * 100) if initial_balance > 0 else 0
        
        sections.append("=" * 80)
        sections.append("PORTFOLIO ANALYSIS REQUEST")
        sections.append("=" * 80)
        sections.append(f"Time: {time_str} (Local)")
        sections.append(f"\nACCOUNT STATUS:")
        sections.append(f"   Starting Balance: ${initial_balance:.2f}")
        sections.append(f"   Current Balance: ${current_balance:.2f}")
        sections.append(f"   Growth: {growth_pct:+.1f}%")
        sections.append(f"   Equity: ${account.equity:.2f}" if account else "")
        sections.append(f"   Available Margin: ${account.margin_free:.2f}" if account else "")
        sections.append("=" * 80)
        
        # Current positions summary
        position_manager.update_positions()
        sections.append(f"\nCURRENT POSITIONS: {len(position_manager.positions)} open")
        
        if position_manager.positions:
            for symbol, pos in position_manager.positions.items():
                sections.append(f"   {symbol}: {pos['type']} {pos['volume']} @ {pos['entry']:.5f} | P&L: ${pos['profit']:+.2f}")
        else:
            sections.append("   No open positions")
        
        sections.append("=" * 80)
        
        # Multi-symbol analysis
        sections.append(f"\n⚠️⚠️⚠️ CRITICAL INFORMATION ABOUT THE DATA ⚠️⚠️⚠️")
        sections.append(f"\nYou are receiving data for ALL timeframes INCLUDING current forming candles:")
        sections.append(f"")
        sections.append(f"📊 DATA STRUCTURE:")
        sections.append(f"   - M15: LAST {Config.BARS_TO_SEND} candles (last one is the MOST RECENT CLOSED M15 candle)")
        sections.append(f"   - H1: CURRENT candle (may STILL BE FORMING - not closed yet)")
        sections.append(f"   - H4: CURRENT candle (may STILL BE FORMING - not closed yet)")
        sections.append(f"")
        sections.append(f"⚠️ IMPORTANT:")
        sections.append(f"   - The M15 timeframe is your PRIMARY timeframe for ENTRY")
        sections.append(f"   - H1 and H4 are for ANALYSIS and CONFLUENCE only")
        sections.append(f"   - H1/H4 indicators show the CURRENT STATE (including forming candle)")
        sections.append(f"   - You receive the last {Config.BARS_TO_SEND} bars for each timeframe to see recent price action")
        sections.append(f"")
        sections.append(f"💡 USE THIS DATA TO:")
        sections.append(f"   1. Check H4 for major trend direction")
        sections.append(f"   2. Check H1 for intermediate trend and setups")
        sections.append(f"   3. Use M15 for precise ENTRY timing and prices")
        sections.append(f"   4. Look at recent bars to see micro-trends, rejections, patterns")
        sections.append(f"")
        sections.append("=" * 80)
        
        sections.append(f"\nMARKET ANALYSIS - ALL {len(all_analysis)} SYMBOLS\n")
        
        for symbol, analysis in all_analysis.items():
            # Check open positions
            has_position = position_manager.has_position(symbol)
            if has_position:
                pos = position_manager.get_position(symbol)
                sections.append(f"\n[{symbol}] [OPEN]: {pos['type']} {pos['volume']}L @ {pos['entry']:.5f} | P&L: ${pos['profit']:+.2f}")
            else:
                sections.append(f"\n[{symbol}]")
            
            # Multi-timeframe analysis (ALL DATA - COMPACT FORMAT)
            for tf in ['h4', 'h1', 'm15']:
                if tf not in analysis:
                    continue
                    
                d = analysis[tf]
                tf_name = tf.upper()
                
                sections.append(
                    f"  {tf_name}: P={d['price']:.5f} VWAP={d['vwap']:.5f} | "
                    f"EMA9={d['ema9']:.5f} EMA21={d['ema21']:.5f} EMA50={d['ema50']:.5f} EMA200={d['ema200']:.5f} SMA20={d['sma20']:.5f} | "
                    f"RSI={d['rsi']:.1f} Stoch={d['stoch_k']:.1f}/{d['stoch_d']:.1f} CCI={d['cci']:.1f} WillR={d['willr']:.1f} | "
                    f"MACD_Hist={d['macd_hist']:.5f} ADX={d['adx']:.1f} +DI={d['plus_di']:.1f} -DI={d['minus_di']:.1f} | "
                    f"ATR={d['atr']:.5f} BB={d['bb_lower']:.5f}/{d['bb_mid']:.5f}/{d['bb_upper']:.5f} BBPos={d['bb_position']:.0f}% | "
                    f"Vol={d['vol_spike']:+.0f}% OBV={d['obv']:.0f}"
                )
                
                # Support/Resistance on same line if available
                if d.get('support_levels') or d.get('resistance_levels'):
                    sup = ",".join([f"{s:.5f}" for s in d['support_levels']]) if d.get('support_levels') else "-"
                    res = ",".join([f"{r:.5f}" for r in d['resistance_levels']]) if d.get('resistance_levels') else "-"
                    sections.append(f"    S/R: Sup=[{sup}] Res=[{res}]")
                
                # Patterns
                if d.get('candlestick_patterns'):
                    pat = ",".join(d['candlestick_patterns'])
                    sections.append(f"    Patterns: {pat}")
        
        sections.append("\n" + "=" * 80)
        
        # Performance history
        performance_summary = self.performance_tracker.format_performance_summary()
        sections.append(f"\n{performance_summary}")
        
        sections.append("\n" + "=" * 80)
        sections.append("\n⚠️⚠️⚠️ YOUR TASK ⚠️⚠️⚠️")
        sections.append(f"   1. Analyze ALL {len(all_analysis)} symbols for confluence")
        sections.append("   2. For EACH symbol, decide: BUY/SELL/WAIT/CLOSE")
        sections.append(f"   3. END YOUR RESPONSE with exactly {len(all_analysis)} lines in this format:")
        sections.append("")
        sections.append("⚠️ MANDATORY OUTPUT FORMAT (EXACTLY THIS FORMAT):")
        sections.append("")
        sections.append("   [Your brief analysis - max 10 lines, NO PARENTHESES HERE]")
        sections.append("")
        sections.append("   FINAL DECISIONS:")
        
        # Generate example with actual symbols
        symbol_list = list(all_analysis.keys())
        sections.append(f"   ({symbol_list[0]} BUY 1.0850 SL 1.0820 TP 1.0890 RISK 1.5)")
        sections.append(f"   ({symbol_list[1]} WAIT)")
        if len(symbol_list) > 2:
            sections.append(f"   ({symbol_list[2]} SELL 24550.0 SL 24620.0 TP 24480.0 RISK 2.0)")
        if len(symbol_list) > 3:
            sections.append(f"   ({symbol_list[3]} WAIT)")
        sections.append("   ... [one parentheses line per symbol]")
        sections.append("")
        sections.append(f"⚠️ CRITICAL RULES:")
        sections.append(f"   - Write EXACTLY {len(all_analysis)} parentheses lines (one per symbol)")
        sections.append(f"   - Each line MUST start with: (SYMBOL_NAME")
        sections.append(f"   - DO NOT write parentheses in your analysis")
        sections.append(f"   - ONLY write parentheses in the FINAL DECISIONS section")
        sections.append(f"   - Format: (SYMBOL ACTION ...) or (SYMBOL WAIT)")
        sections.append("=" * 80)
        
        return "\n".join(sections)
    
    def _parse_portfolio_decisions(self, response_text: str, symbol_list: List[str]) -> List[Dict]:
        """
        Parse MULTIPLE trading decisions from response
        
        Expected format:
        [Optional reasoning text]
        FINAL DECISIONS:
        (BUY 1.0850 SL 1.0820 TP 1.0890 RISK 1.5)
        (WAIT)
        (CLOSE)
        ... (one line per symbol)
        """
        import re
        
        decisions = []
        
        # Find the "FINAL DECISIONS:" marker
        if "FINAL DECISIONS:" in response_text:
            parts = response_text.split("FINAL DECISIONS:", 1)
            decision_text = parts[1].strip()
            print(f"\n✅ FOUND 'FINAL DECISIONS:' marker")
        else:
            decision_text = response_text
            print(f"\n⚠️ No 'FINAL DECISIONS:' marker found - using fallback parser")
            # Try intelligent fallback
            return self._fallback_parse_decisions(response_text, symbol_list)
        
        # Find ALL parentheses blocks AFTER "FINAL DECISIONS:"
        all_parens = re.findall(r'\(([^)]+)\)', decision_text)
        
        print(f"\n{'=' * 80}")
        print(f"PARSING {len(all_parens)} PARENTHESES BLOCKS AFTER 'FINAL DECISIONS:'")
        print(f"{'=' * 80}")
        
        if all_parens:
            print("\nAll found blocks:")
            for idx, block in enumerate(all_parens[:20], 1):  # Show first 20
                print(f"   [{idx}] ({block[:100]}{'...' if len(block) > 100 else ''})")
            if len(all_parens) > 20:
                print(f"   ... and {len(all_parens) - 20} more blocks")
        
        parsed_count = 0
        skipped_count = 0
        
        for line_num, block in enumerate(all_parens, 1):
            line = block.strip()
            
            # Skip empty
            if not line:
                skipped_count += 1
                print(f"      Line {line_num}: SKIPPED (empty)")
                continue
            
            parts = line.split()
            if len(parts) < 2:
                skipped_count += 1
                print(f"      Line {line_num}: SKIPPED (not enough parts)")
                continue
            
            # Check if format is: SYMBOL ACTION ... or ACTION ...
            # If parts[0] is a symbol and parts[1] is an action, use parts[1]
            # Otherwise, use parts[0]
            if parts[0].upper() in symbol_list:
                # Format: SYMBOL ACTION ENTRY SL TP RISK
                symbol = parts[0].upper()
                action = parts[1].upper()
                action_start_idx = 1
            elif parts[0].upper() in ['BUY', 'SELL', 'WAIT', 'CLOSE', 'CLOSE_ALL']:
                # Format: ACTION ENTRY SL TP RISK (no symbol, will map by order)
                symbol = None
                action = parts[0].upper()
                action_start_idx = 0
            else:
                skipped_count += 1
                print(f"      Line {line_num}: SKIPPED (unknown format '{parts[0]}')")
                continue
            
            # Log what we're checking
            if line_num <= 15:
                if symbol:
                    print(f"      Line {line_num}: {symbol} → {action} - parts: {parts[:5]}")
                else:
                    print(f"      Line {line_num}: {action} - parts: {parts[:5]}")
            
            if action == 'WAIT':
                decisions.append({
                    'action': 'WAIT',
                    'symbol': symbol
                })
                parsed_count += 1
                print(f"      Line {line_num}: {symbol if symbol else '?'} WAIT")
                continue
            
            if action in ['CLOSE', 'CLOSE_ALL']:
                decisions.append({
                    'action': 'CLOSE',
                    'symbol': symbol
                })
                parsed_count += 1
                print(f"      Line {line_num}: {symbol if symbol else '?'} CLOSE")
                continue
            
            if action not in ['BUY', 'SELL']:
                skipped_count += 1
                if line_num <= 15:
                    print(f"      Line {line_num}: SKIPPED (unknown action '{action}')")
                continue
            
            # Parse BUY/SELL with levels
            decision = {
                'action': action,
                'symbol': symbol
            }
            
            # Start parsing from after the action
            i = action_start_idx + 1
            while i < len(parts):
                part = parts[i].upper()
                
                # First number after BUY/SELL = entry
                if re.match(r'^\d+\.?\d*$', parts[i]) and 'entry' not in decision:
                    decision['entry'] = float(parts[i])
                    i += 1
                    continue
                
                # SL keyword
                if part == 'SL' and i + 1 < len(parts):
                    if re.match(r'^\d+\.?\d*$', parts[i + 1]):
                        decision['stop_loss'] = float(parts[i + 1])
                        i += 2
                        continue
                
                # TP keyword
                if part == 'TP' and i + 1 < len(parts):
                    if re.match(r'^\d+\.?\d*$', parts[i + 1]):
                        decision['take_profit_1'] = float(parts[i + 1])
                        i += 2
                        continue
                
                # RISK keyword
                if part == 'RISK' and i + 1 < len(parts):
                    if re.match(r'^\d+\.?\d*$', parts[i + 1]):
                        decision['risk_percent'] = float(parts[i + 1])
                        i += 2
                        continue
                
                i += 1
            
            # Validate decision has all required fields
            if 'entry' in decision and 'stop_loss' in decision and 'take_profit_1' in decision:
                if 'risk_percent' not in decision:
                    decision['risk_percent'] = 2.0  # Default
                
                decisions.append(decision)
                parsed_count += 1
                sym_str = f"{decision.get('symbol', '?')}: " if decision.get('symbol') else ""
                print(f"      Line {line_num}: {sym_str}{action} @ {decision['entry']:.5f} | SL {decision['stop_loss']:.5f} | TP {decision['take_profit_1']:.5f} | Risk {decision.get('risk_percent', 2.0)}%")
            else:
                skipped_count += 1
                print(f"      Line {line_num}: INCOMPLETE ({action} missing levels)")
        
        # Summary
        print(f"\nParsing Summary:")
        print(f"Parsed: {parsed_count} decisions")
        print(f"Skipped: {skipped_count} lines")
        
        return decisions
    
    def _fallback_parse_decisions(self, response_text: str, symbol_list: List[str]) -> List[Dict]:
        """
        Intelligent fallback parser when DeepSeek doesn't use proper format.
        Looks for trading decisions in natural language.
        """
        import re
        
        print(f"\n{'=' * 80}")
        print(f"FALLBACK PARSER: Analyzing text for {len(symbol_list)} symbols")
        print(f"{'=' * 80}")
        
        decisions = []
        text_lower = response_text.lower()
        
        # For each symbol, try to find a decision
        for symbol in symbol_list:
            symbol_lower = symbol.lower()
            print(f"\n🔍 Searching for {symbol} decision...")
            
            # Find section about this symbol
            symbol_section = self._extract_symbol_section(response_text, symbol)
            
            if not symbol_section:
                print(f"   ⚠️ No clear section found for {symbol} - defaulting to WAIT")
                decisions.append({'action': 'WAIT'})
                continue
            
            print(f"   Found section: {symbol_section[:200]}...")
            
            # Look for action keywords
            section_lower = symbol_section.lower()
            
            # Check for clear action signals
            if 'wait' in section_lower or 'no trade' in section_lower or 'skip' in section_lower:
                print(f"   ✅ Action: WAIT")
                decisions.append({'action': 'WAIT'})
                continue
            
            if 'close' in section_lower and 'position' in section_lower:
                print(f"   ✅ Action: CLOSE")
                decisions.append({'action': 'CLOSE'})
                continue
            
            # Look for BUY/SELL with levels
            action = None
            if 'buy' in section_lower and 'sell' not in section_lower[:section_lower.find('buy') + 10]:
                action = 'BUY'
            elif 'sell' in section_lower and 'buy' not in section_lower[:section_lower.find('sell') + 10]:
                action = 'SELL'
            
            if not action:
                print(f"   ⚠️ No clear BUY/SELL signal - defaulting to WAIT")
                decisions.append({'action': 'WAIT'})
                continue
            
            print(f"   ✅ Action: {action}")
            
            # Extract levels using regex
            decision = {'action': action}
            
            # Look for Entry price (various formats)
            entry_patterns = [
                r'entry[:\s]+(\d+\.?\d*)',
                r'enter at[:\s]+(\d+\.?\d*)',
                r'price[:\s]+(\d+\.?\d*)',
                action.lower() + r'\s+at\s+(\d+\.?\d*)',
                action.lower() + r'\s+(\d+\.?\d+)',
            ]
            
            for pattern in entry_patterns:
                match = re.search(pattern, section_lower)
                if match:
                    decision['entry'] = float(match.group(1))
                    print(f"   📍 Entry: {decision['entry']}")
                    break
            
            # Look for SL
            sl_patterns = [
                r'sl[:\s]+(\d+\.?\d*)',
                r'stop[:\s]+(\d+\.?\d*)',
                r'stop loss[:\s]+(\d+\.?\d*)',
            ]
            
            for pattern in sl_patterns:
                match = re.search(pattern, section_lower)
                if match:
                    decision['stop_loss'] = float(match.group(1))
                    print(f"   🛑 SL: {decision['stop_loss']}")
                    break
            
            # Look for TP
            tp_patterns = [
                r'tp[:\s]+(\d+\.?\d*)',
                r'target[:\s]+(\d+\.?\d*)',
                r'take profit[:\s]+(\d+\.?\d*)',
            ]
            
            for pattern in tp_patterns:
                match = re.search(pattern, section_lower)
                if match:
                    decision['take_profit_1'] = float(match.group(1))
                    print(f"   🎯 TP: {decision['take_profit_1']}")
                    break
            
            # Look for Risk
            risk_patterns = [
                r'risk[:\s]+(\d+\.?\d*)%?',
                r'(\d+\.?\d*)%\s+risk',
            ]
            
            for pattern in risk_patterns:
                match = re.search(pattern, section_lower)
                if match:
                    decision['risk_percent'] = float(match.group(1))
                    print(f"   💰 Risk: {decision['risk_percent']}%")
                    break
            
            # Validate we have all required fields
            if 'entry' in decision and 'stop_loss' in decision and 'take_profit_1' in decision:
                if 'risk_percent' not in decision:
                    decision['risk_percent'] = 1.5  # Default conservative
                decisions.append(decision)
                print(f"   ✅ Complete decision extracted")
            else:
                print(f"   ⚠️ Incomplete levels - defaulting to WAIT")
                print(f"   Missing: {[k for k in ['entry', 'stop_loss', 'take_profit_1'] if k not in decision]}")
                decisions.append({'action': 'WAIT'})
        
        print(f"\n{'=' * 80}")
        print(f"FALLBACK PARSER: Extracted {len([d for d in decisions if d['action'] in ['BUY', 'SELL']])} valid trades")
        print(f"{'=' * 80}")
        
        return decisions
    
    def _extract_symbol_section(self, text: str, symbol: str) -> str:
        """Extract the section of text about a specific symbol"""
        lines = text.split('\n')
        
        # Find lines mentioning this symbol
        relevant_lines = []
        in_section = False
        
        for i, line in enumerate(lines):
            if symbol.upper() in line.upper() or symbol.lower() in line.lower():
                # Found symbol mention - include surrounding context
                start = max(0, i - 2)
                end = min(len(lines), i + 10)
                return '\n'.join(lines[start:end])
        
        return ""
    
    def _extract_from_reasoning(self, reasoning_text: str) -> Dict:
        """Extract trading decision from reasoning text intelligently"""
        import re
        
        decision = {
            'action': 'NO_SIGNAL',
            'confidence': 50,
            'reasoning': reasoning_text[:200]
        }
        
        print(f"    DEBUG - Reasoning length: {len(reasoning_text)} chars")
        print(f"    FULL REASONING:")
        print(reasoning_text)
        print(f"    === END OF REASONING ===")
        
        # Find ALL parentheses blocks
        all_parens = re.findall(r'\(([^)]+)\)', reasoning_text)
        
        if not all_parens:
            print(f"   No parentheses found!")
            return decision
        
        print(f"    Found {len(all_parens)} parentheses total")
        
        # Find LAST one starting with BUY/SELL/WAIT/CLOSE/HOLD
        action_keywords = ['BUY', 'SELL', 'WAIT', 'CLOSE', 'HOLD']
        found_block = None
        
        for block in reversed(all_parens):
            parts = block.strip().split()
            if parts and parts[0].upper() in action_keywords:
                found_block = block.strip()
                print(f"   Found action block: ({found_block})")
                break
        
        if not found_block:
            print(f"   No action keyword (BUY/SELL/WAIT) in any parentheses!")
            return decision
        
        parts = found_block.split()
        
        if not parts:
            return decision
        
        action = parts[0].upper()
        print(f"   Action: {action}")
        
        if action == 'WAIT':
            decision['action'] = 'NO_SIGNAL'
            print(f"   WAIT")
            return decision
        
        if action not in ['BUY', 'SELL', 'CLOSE', 'HOLD']:
            print(f"   Unknown: {action}")
            return decision
        
        decision['action'] = action
        
        # Parse: BUY ENTRY SL STOP TP TARGET RISK PERCENT
        i = 1
        while i < len(parts):
            part = parts[i].upper()
            
            # First number = entry (but reject if it contains "..." or text)
            if re.match(r'^\d+\.?\d*$', parts[i]) and 'entry' not in decision:
                decision['entry'] = float(parts[i])
                print(f"   Entry: {decision['entry']}")
                i += 1
                continue
            
            # Reject if we find placeholders
            if '...' in parts[i] or 'etc' in parts[i].lower() or 'price' in parts[i].lower():
                print(f"   REJECTED: Found placeholder '{parts[i]}' - not a valid decision")
                return {'action': 'NO_SIGNAL', 'confidence': 50, 'reasoning': 'Invalid format with placeholders'}
            
            # SL
            if part == 'SL' and i + 1 < len(parts):
                try:
                    if re.match(r'^\d+\.?\d*$', parts[i + 1]):
                        decision['stop_loss'] = float(parts[i + 1])
                        print(f"   SL: {decision['stop_loss']}")
                        i += 2
                        continue
                    else:
                        print(f"   REJECTED: SL has invalid value '{parts[i + 1]}'")
                        return {'action': 'NO_SIGNAL', 'confidence': 50, 'reasoning': 'Invalid SL format'}
                except:
                    pass
            
            # TP
            if part == 'TP' and i + 1 < len(parts):
                try:
                    if re.match(r'^\d+\.?\d*$', parts[i + 1]):
                        decision['take_profit_1'] = float(parts[i + 1])
                        print(f"   TP: {decision['take_profit_1']}")
                        i += 2
                        continue
                    else:
                        print(f"   REJECTED: TP has invalid value '{parts[i + 1]}'")
                        return {'action': 'NO_SIGNAL', 'confidence': 50, 'reasoning': 'Invalid TP format'}
                except:
                    pass
            
            # RISK
            if part == 'RISK' and i + 1 < len(parts):
                try:
                    if re.match(r'^\d+\.?\d*$', parts[i + 1]):
                        decision['risk_percent'] = float(parts[i + 1])
                        print(f"   Risk: {decision['risk_percent']}%")
                        i += 2
                        continue
                    else:
                        print(f"   REJECTED: RISK has invalid value '{parts[i + 1]}'")
                        return {'action': 'NO_SIGNAL', 'confidence': 50, 'reasoning': 'Invalid RISK format'}
                except:
                    pass
            
            i += 1
        
        print(f"   Final: {decision}")
        
        return decision
    
    def _parse_decision(self, response_text: str) -> Dict:
        """Parse AI response into structured decision"""
        try:
            # Strip whitespace
            response_text = response_text.strip()
            
            # Try direct JSON parse first
            if response_text.startswith('{'):
                try:
                    decision = json.loads(response_text)
                    return decision
                except:
                    pass
            
            # Look for JSON block anywhere in text
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            
            if start != -1 and end > start:
                json_str = response_text[start:end]
                
                # Try to parse, handle nested braces
                brace_count = 0
                actual_end = start
                for i in range(start, len(response_text)):
                    if response_text[i] == '{':
                        brace_count += 1
                    elif response_text[i] == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            actual_end = i + 1
                            break
                
                if actual_end > start:
                    json_str = response_text[start:actual_end]
                    try:
                        decision = json.loads(json_str)
                        return decision
                    except json.JSONDecodeError as e:
                        print(f"    JSON parse error: {e}")
                        print(f"    Attempted to parse: {json_str[:200]}...")
            
            # Fallback: parse text response
            return self._parse_text_decision(response_text)
                
        except Exception as e:
            print(f"    Parse error: {e}")
            return self._parse_text_decision(response_text)
    
    def _parse_text_decision(self, text: str) -> Dict:
        """Fallback parser for non-JSON responses"""
        text_upper = text.upper()
        
        decision = {
            'action': 'NO_SIGNAL',
            'confidence': 50,
            'reasoning': text
        }
        
        if 'BUY' in text_upper and 'NO' not in text_upper[:50]:
            decision['action'] = 'BUY'
        elif 'SELL' in text_upper and 'NO' not in text_upper[:50]:
            decision['action'] = 'SELL'
        elif 'CLOSE' in text_upper:
            decision['action'] = 'CLOSE'
        elif 'HOLD' in text_upper:
            decision['action'] = 'HOLD'
        
        # Try to extract levels
        lines = text.split('\n')
        for line in lines:
            if 'ENTRY' in line.upper():
                try:
                    decision['entry'] = float(line.split(':')[1].strip().split()[0])
                except:
                    pass
            elif 'STOP' in line.upper() or 'SL' in line.upper():
                try:
                    decision['stop_loss'] = float(line.split(':')[1].strip().split()[0])
                except:
                    pass
            elif 'TAKE' in line.upper() or 'TP' in line.upper():
                try:
                    decision['take_profit_1'] = float(line.split(':')[1].strip().split()[0])
                except:
                    pass
        
        return decision

# ==================== EXECUTION MANAGER ====================
class ExecutionManager:
    @staticmethod
    def calculate_position_size(
        symbol: str,
        entry: float,
        stop_loss: float,
        risk_percent: float = 2.0  # Default 2% if not specified
    ) -> float:
        """Calculate optimal position size based on risk"""
        account = mt5.account_info()
        if account is None:
            return 0.01
        
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return 0.01
        
        # Calculate risk amount
        risk_amount = account.balance * (risk_percent / 100.0)
        
        # Calculate SL distance in points
        sl_distance = abs(entry - stop_loss) / symbol_info.point
        
        # Prevent division by zero
        if sl_distance < 10:
            return symbol_info.volume_min
        
        # Calculate position size
        tick_value = symbol_info.trade_tick_value
        tick_size = symbol_info.trade_tick_size
        point_value = tick_value / (tick_size / symbol_info.point) if tick_size > 0 else 1
        
        volume = risk_amount / (sl_distance * point_value)
        
        # Round to volume step
        volume = round(volume / symbol_info.volume_step) * symbol_info.volume_step
        volume = max(symbol_info.volume_min, min(volume, symbol_info.volume_max))
        
        return volume
    
    @staticmethod
    def open_position(
        symbol: str,
        action: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        risk_percent: float = 2.0
    ) -> Optional[Dict]:
        """Open a new position"""
        # Calculate volume with DeepSeek's risk decision
        volume = ExecutionManager.calculate_position_size(symbol, entry, stop_loss, risk_percent)
        
        # Get current price
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f" Cannot get tick for {symbol}")
            return None
        
        # Determine order type
        if action == "BUY":
            trade_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        elif action == "SELL":
            trade_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            return None
        
        symbol_info = mt5.symbol_info(symbol)
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": trade_type,
            "price": price,
            "sl": round(stop_loss, symbol_info.digits),
            "tp": round(take_profit, symbol_info.digits),
            "deviation": 20,
            "magic": 234000,
            "comment": "AI Trader",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        print(f"\n Opening {action} {symbol}")
        print(f"   Risk: {risk_percent}% of balance")
        print(f"   Volume: {volume} lots")
        print(f"   Entry: {price:.5f}")
        print(f"   SL: {stop_loss:.5f} | TP: {take_profit:.5f}")
        
        result = mt5.order_send(request)
        
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f" Position opened | Ticket: #{result.order}")
            return {
                'ticket': result.order,
                'symbol': symbol,
                'action': action,
                'volume': volume,
                'entry': price
            }
        else:
            error_msg = result.comment if result else "Unknown error"
            print(f"❌ Failed to open position: {error_msg}")
            return None

# ==================== MAIN TRADING SYSTEM ====================
class ProfessionalTradingSystem:
    def __init__(self):
        self.mt5 = MT5Manager()
        self.performance_tracker = PerformanceTracker()
        self.position_manager = PositionManager(self.performance_tracker)
        self.analyzer = TechnicalAnalyzer()
        self.gpt_engine = GPTTradingEngine(Config.DEEPSEEK_API_KEY, Config.DEEPSEEK_BASE_URL, self.performance_tracker)
        self.execution = ExecutionManager()
        
        self.scan_count = 0
        self.start_time = datetime.datetime.now()
    
    def initialize(self) -> bool:
        """Initialize the signal system"""
        print("\n" + "=" * 80)
        print("📊 DEEPSEEK TRADING SIGNALS - LIVE MODE")
        print("=" * 80)
        
        # Check API key
        if not Config.DEEPSEEK_API_KEY or Config.DEEPSEEK_API_KEY == "YOUR_API_KEY_HERE":
            print(" ERROR: Please set your DeepSeek API key!")
            print("   Edit Config.DEEPSEEK_API_KEY in the script")
            return False
        
        # Connect to MT5
        if not self.mt5.connect():
            return False
        
        # Display configuration
        print(f"\n⚙️  CONFIGURATION:")
        print(f"   Model: {Config.MODEL} (DeepSeek Reasoner)")
        print(f"   Mode: SIGNALS ONLY (No Auto-Trading)")
        print(f"   Strategy: Multi-Timeframe Confluence + Mean Reversion")
        print(f"   Trading Hours: {Config.TRADING_START_HOUR}:00 - {Config.TRADING_END_HOUR}:00 (Local)")
        print(f"   Scan Interval: {Config.SCAN_INTERVAL // 60} minutes (M15 candle opens)")
        print(f"   Symbols: Market Watch ({len(self.mt5.get_market_watch_symbols())} symbols)")
        print("=" * 80)
        
        return True
    
    def is_trading_hours(self) -> bool:
        """Check if current time is within trading hours"""
        now = datetime.datetime.now()
        return Config.TRADING_START_HOUR <= now.hour < Config.TRADING_END_HOUR
    
    def should_stop_trading(self) -> bool:
        """Check if daily loss limit reached"""
        if self.position_manager.daily_pnl < 0:
            account = mt5.account_info()
            daily_loss_pct = abs(self.position_manager.daily_pnl) / account.balance * 100
            if daily_loss_pct >= Config.MAX_DAILY_LOSS:
                print(f"\n DAILY LOSS LIMIT REACHED: {daily_loss_pct:.2f}%")
                return True
        return False
    
    def run_scan(self, symbols: List[str]):
        """Run PORTFOLIO scan cycle - Generate SIGNALS ONLY"""
        self.scan_count += 1
        now = datetime.datetime.now()
        
        print(f"\n{'=' * 80}")
        print(f"📡 SIGNAL SCAN #{self.scan_count} | {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 80}")
        
        # Check trading hours
        if not self.is_trading_hours():
            print(f" Outside trading hours ({Config.TRADING_START_HOUR}:00-{Config.TRADING_END_HOUR}:00)")
            print(f"   Current hour: {now.hour}:00")
            return
        
        # Check daily loss limit
        if self.should_stop_trading():
            print(" Daily loss limit reached. Stopping new trades.")
            return
        
        # Update existing positions
        self.position_manager.update_positions()
        print(f"\n[INFO] Open Positions: {len(self.position_manager.positions)}")
        
        if self.position_manager.positions:
            print(self.position_manager.get_positions_summary())
        
        # === STEP 1: Collect ALL data locally (fast, free) ===
        print(f"\n[INFO] Collecting data for {len(symbols)} symbols...")
        all_analysis = {}
        
        for i, symbol in enumerate(symbols, 1):
            print(f"   [{i}/{len(symbols)}] {symbol}...", end='')
            try:
                analysis = self.analyzer.get_multi_timeframe_data(symbol)
                if analysis:
                    all_analysis[symbol] = analysis
                    print(" [OK]")
                else:
                    print(" [ERROR] No data")
            except Exception as e:
                print(f" [ERROR] Error: {e}")
        
        if not all_analysis:
            print("\n[ERROR] No data collected, skipping scan")
            return
        
        print(f"\n[OK] Collected data for {len(all_analysis)}/{len(symbols)} symbols")
        
        # === STEP 2: Send to DeepSeek for portfolio analysis (1 API call) ===
        print(f"\n🧠 Analyzing ENTIRE portfolio with DeepSeek...")
        decisions = self.gpt_engine.analyze_portfolio(all_analysis, self.position_manager)
        
        if not decisions:
            print("\n[ERROR] No trading decisions from DeepSeek")
            return
        
        # === STEP 3: Display SIGNALS ===
        print(f"\n{'=' * 80}")
        print(f"📊 TRADING SIGNALS - {len(decisions)} SYMBOLS ANALYZED")
        print(f"{'=' * 80}\n")
        
        # Map decisions to symbols
        symbol_list = list(all_analysis.keys())
        
        # Count signal types
        buy_signals = 0
        sell_signals = 0
        close_signals = 0
        wait_signals = 0
        
        for i, decision in enumerate(decisions):
            # Use symbol from decision if available, otherwise use index
            if 'symbol' in decision and decision['symbol']:
                symbol = decision['symbol']
            elif i < len(symbol_list):
                symbol = symbol_list[i]
            else:
                continue
            
            action = decision.get('action', 'WAIT')
            
            # Display signal
            if action == 'WAIT':
                wait_signals += 1
                print(f"⏸️  {symbol:12} → WAIT")
                
            elif action in ['CLOSE', 'CLOSE_ALL']:
                close_signals += 1
                print(f"❌ {symbol:12} → CLOSE ALL POSITIONS")
                
            elif action == 'BUY':
                buy_signals += 1
                entry = decision.get('entry', 0)
                sl = decision.get('stop_loss', 0)
                tp = decision.get('take_profit_1', 0)
                risk = decision.get('risk_percent', 2.0)
                
                print(f"🟢 {symbol:12} → BUY")
                print(f"   Entry: {entry:.5f} | SL: {sl:.5f} | TP: {tp:.5f}")
                print(f"   Risk: {risk}% | R:R: {abs(tp-entry)/abs(entry-sl):.2f}:1" if sl != entry else "")
                print()
                
            elif action == 'SELL':
                sell_signals += 1
                entry = decision.get('entry', 0)
                sl = decision.get('stop_loss', 0)
                tp = decision.get('take_profit_1', 0)
                risk = decision.get('risk_percent', 2.0)
                
                print(f"🔴 {symbol:12} → SELL")
                print(f"   Entry: {entry:.5f} | SL: {sl:.5f} | TP: {tp:.5f}")
                print(f"   Risk: {risk}% | R:R: {abs(entry-tp)/abs(sl-entry):.2f}:1" if sl != entry else "")
                print()
        
        # Summary
        print(f"{'=' * 80}")
        print(f"📈 SIGNALS SUMMARY:")
        print(f"   🟢 BUY:  {buy_signals}")
        print(f"   🔴 SELL: {sell_signals}")
        print(f"   ❌ CLOSE: {close_signals}")
        print(f"   ⏸️  WAIT: {wait_signals}")
        print(f"{'=' * 80}")
        
        # === Summary ===
        print(f"\n{'=' * 80}")
        print(f"✅ SIGNAL SCAN #{self.scan_count} COMPLETE")
        print(f"   Time: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Next scan: {(now + timedelta(minutes=15)).strftime('%H:%M')}")
        print(f"{'=' * 80}")
    
    def _execute_decision(self, symbol: str, decision: Dict):
        """Execute trading decision"""
        action = decision.get('action', 'NO_SIGNAL')
        
        has_position = self.position_manager.has_position(symbol)
        
        if action == 'NO_SIGNAL':
            print(f"   No action for {symbol}")
            return
        
        elif action == 'HOLD':
            if has_position:
                print(f"    Holding {symbol} position")
            else:
                print(f"    No position to hold")
            return
        
        elif action == 'CLOSE':
            if has_position:
                print(f"    Closing {symbol} position (setup broken or TP reached)")
                self.position_manager.close_position(symbol)
            else:
                print(f"    No position to close")
            return
        
        elif action == 'TRAIL_STOP':
            if has_position:
                pos = self.position_manager.get_position(symbol)
                print(f"    TRAIL_STOP requested for {symbol}")
                print(f"      Current P&L: ${pos['profit']:+.2f}")
                print(f"      Position still valid, trailing stop recommended")
                # TODO: Implement trailing stop modification
                # For now, just hold the position
            else:
                print(f"    No position to trail")
            return
        
        elif action == 'PARTIAL_CLOSE':
            if has_position:
                pos = self.position_manager.get_position(symbol)
                print(f"    PARTIAL_CLOSE requested for {symbol}")
                print(f"      Current P&L: ${pos['profit']:+.2f}")
                print(f"      Scalper taking partial profits - good practice!")
                # TODO: Implement partial close (close 50% of position)
                # For now, just acknowledge
            else:
                print(f"    No position for partial close")
            return
        
        elif action in ['BUY', 'SELL']:
            # Check if we can open new position
            if len(self.position_manager.positions) >= Config.MAX_POSITIONS:
                print(f"    Max positions reached ({Config.MAX_POSITIONS})")
                return
            
            # Validate decision has required fields
            if 'entry' not in decision or 'stop_loss' not in decision or 'take_profit_1' not in decision:
                print(f"    Incomplete decision (missing entry/SL/TP)")
                return
            
            # Get levels
            entry = decision['entry']
            sl = decision['stop_loss']
            tp = decision['take_profit_1']
            
            # CRITICAL: Validate and auto-correct SL/TP direction
            if action == 'BUY':
                # BUY: SL must be BELOW entry, TP must be ABOVE entry
                if sl >= entry:
                    print(f"    INVALID BUY: SL ({sl}) >= Entry ({entry})")
                    # If TP is below entry, they gave us SELL levels for a BUY signal
                    if tp < entry:
                        print(f"    DeepSeek confused BUY/SELL - keeping original SELL setup")
                        action = 'SELL'
                        decision['action'] = 'SELL'
                        # SL and TP are already correct for SELL
                    else:
                        print(f"    Cannot auto-correct - rejecting trade")
                        return
                
                elif tp <= entry:
                    print(f"    INVALID BUY: TP ({tp}) <= Entry ({entry})")
                    print(f"    TP must be above entry for BUY - rejecting trade")
                    return
                    
            elif action == 'SELL':
                # SELL: SL must be ABOVE entry, TP must be BELOW entry
                if sl <= entry:
                    print(f"    INVALID SELL: SL ({sl}) <= Entry ({entry})")
                    # If TP is above entry, they gave us BUY levels for a SELL signal
                    if tp > entry:
                        print(f"    DeepSeek confused BUY/SELL - keeping original BUY setup")
                        action = 'BUY'
                        decision['action'] = 'BUY'
                        # SL and TP are already correct for BUY
                    else:
                        print(f"    Cannot auto-correct - rejecting trade")
                        return
                
                elif tp >= entry:
                    print(f"    INVALID SELL: TP ({tp}) >= Entry ({entry})")
                    print(f"    TP must be below entry for SELL - rejecting trade")
                    return
            
            # Calculate distances for pip display
            sl_distance = abs(entry - sl)
            tp_distance = abs(tp - entry)
            
            # Check SL is within scalping range (15-30 pips for forex)
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info:
                # Calculate pips correctly based on symbol type
                point = symbol_info.point
                
                # For forex pairs (5 decimal): 1 pip = 0.0001 = 10 points
                # For JPY pairs (3 decimal): 1 pip = 0.01 = 10 points
                # For indices/metals: 1 pip = point value
                if 'JPY' in symbol:
                    # JPY pairs: point = 0.001, pip = 0.01
                    pip_value = point * 10
                elif symbol_info.digits == 5 or symbol_info.digits == 3:
                    # Standard forex with 5 decimals or JPY with 3
                    pip_value = point * 10
                else:
                    # Indices, metals, crypto
                    pip_value = point
                
                sl_pips = sl_distance / pip_value
                tp_pips = tp_distance / pip_value
                
                print(f"    SL: {sl_pips:.1f} pips | TP: {tp_pips:.1f} pips")
                
                # For forex, informational pip display (no strict limits)
                if symbol_info.digits >= 3 and symbol_info.digits <= 5:
                    if sl_pips > 50:
                        print(f"    📊 Wide SL ({sl_pips:.1f} pips) - swing trade style")
                    elif sl_pips > 35:
                        print(f"    📊 Medium SL ({sl_pips:.1f} pips) - intraday style")
                    else:
                        print(f"    📊 Tight SL ({sl_pips:.1f} pips) - scalping style")
            
            # Close opposite position if exists
            if has_position:
                current_pos = self.position_manager.get_position(symbol)
                if current_pos['type'] != action:
                    print(f"    Reversing position: Closing {current_pos['type']}, Opening {action}")
                    self.position_manager.close_position(symbol)
                else:
                    print(f"    Already in {action} position")
                    return
            
            # Open new position
            # Get risk percent from DeepSeek decision (default 2% if not specified)
            risk_percent = decision.get('risk_percent', 2.0)
            
            # Validate risk percent
            if risk_percent < Config.RISK_PER_TRADE_MIN or risk_percent > Config.RISK_PER_TRADE_MAX:
                print(f"    Invalid risk percent {risk_percent}%. Adjusting to safe range.")
                risk_percent = max(Config.RISK_PER_TRADE_MIN, min(risk_percent, Config.RISK_PER_TRADE_MAX))
            
            print(f"    DeepSeek Risk Decision: {risk_percent}%")
            
            result = self.execution.open_position(symbol, action, entry, sl, tp, risk_percent=risk_percent)
            
            if result:
                # Record trade in performance tracker
                self.performance_tracker.record_trade(
                    symbol=symbol,
                    action=action,
                    entry=entry,
                    sl=sl,
                    tp=tp,
                    volume=result['volume'],
                    strategy=decision.get('strategy', 'UNKNOWN'),
                    confidence=decision.get('confidence', 50),
                    reasoning=decision.get('reasoning', 'No reasoning provided')
                )
                
                # Update position manager
                self.position_manager.update_positions()
    
    def run(self):
        """Main trading loop"""
        if not self.initialize():
            return
        
        # Get symbols
        symbols = self.mt5.get_market_watch_symbols()
        
        if not symbols:
            print(" No symbols in Market Watch!")
            return
        
        print(f"\n📡 Monitoring {len(symbols)} symbols for LIVE SIGNALS:")
        for i in range(0, len(symbols), 6):
            print(f"   {', '.join(symbols[i:i+6])}")
        print(f"\n⏰ Scanning at M15 candle opens (every 15 minutes)")
        print(f"   Trading hours: {Config.TRADING_START_HOUR}:00 - {Config.TRADING_END_HOUR}:00")
        print(f"\n💡 MODE: SIGNALS ONLY - No automatic execution")
        print("\n Press CTRL+C to stop\n")
        
        try:
            while True:
                now = datetime.datetime.now()
                
                # Check if within trading hours
                if self.is_trading_hours():
                    # Calculate next M15 candle open
                    current_minute = now.minute
                    minutes_in_quarter = current_minute % 15
                    
                    if minutes_in_quarter == 0 and now.second < 5:
                        # We're at candle open (within first 5 seconds)
                        print(f"\n⏰ M15 CANDLE OPEN: {now.strftime('%H:%M')}")
                        print(f"   Analyzing market with M15 last closed + H1/H4 forming candles")
                        
                        # Run scan
                        self.run_scan(symbols)
                        
                        # Sleep until next candle (15 min - 5 sec buffer)
                        sleep_seconds = (15 * 60) - 5
                        next_candle = now + timedelta(seconds=sleep_seconds)
                        print(f"\n⏭️  Next candle at {next_candle.strftime('%H:%M')}")
                        time.sleep(sleep_seconds)
                    else:
                        # Wait until next M15 candle open
                        seconds_until_next = ((15 - minutes_in_quarter) * 60) - now.second
                        next_candle = now + timedelta(seconds=seconds_until_next)
                        
                        minutes_left = int(seconds_until_next / 60)
                        seconds_left = seconds_until_next % 60
                        
                        print(f"\n Waiting for next M15 candle open at {next_candle.strftime('%H:%M')}")
                        print(f"   Time remaining: {minutes_left}m {seconds_left}s")
                        
                        time.sleep(seconds_until_next)
                else:
                    # Outside trading hours - sleep until next trading session
                    if now.hour < Config.TRADING_START_HOUR:
                        # Before trading starts today
                        next_start = now.replace(hour=Config.TRADING_START_HOUR, minute=0, second=0, microsecond=0)
                    else:
                        # After trading ends - wait until tomorrow
                        next_start = (now + timedelta(days=1)).replace(hour=Config.TRADING_START_HOUR, minute=0, second=0, microsecond=0)
                    
                    sleep_seconds = (next_start - now).total_seconds()
                    sleep_minutes = int(sleep_seconds / 60)
                    
                    print(f"\n Outside trading hours ({now.hour}:00)")
                    print(f"   Trading resumes at {next_start.strftime('%Y-%m-%d %H:%M')}")
                    print(f"   Sleeping for {sleep_minutes} minutes...")
                    
                    time.sleep(sleep_seconds)
                
        except KeyboardInterrupt:
            print("\n\n" + "=" * 80)
            print("🛑 SIGNAL BOT STOPPED BY USER")
            print("=" * 80)
            
            runtime = datetime.datetime.now() - self.start_time
            print(f"\n📊 SESSION SUMMARY:")
            print(f"   Runtime: {runtime}")
            print(f"   Total Scans: {self.scan_count}")
            print("=" * 80)
        
        finally:
            self.mt5.disconnect()
            print("\n✅ Signal bot shutdown complete")

# ==================== ENTRY POINT ====================
if __name__ == "__main__":
    system = ProfessionalTradingSystem()
    system.run()

