import yfinance as yf
import json
import os
from datetime import datetime

# Local mock databases
PORTFOLIO_FILE = "portfolio.json"
ALERTS_FILE = "alerts.json"

def _load_db(filename, default):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return default

def _save_db(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def get_market_data(ticker: str) -> str:
    """Fetches current stock price and key metrics using yfinance."""
    try:
        stock = yf.Ticker(ticker)
        # Using fast_info or info (info can be slow, but comprehensive)
        info = stock.info
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        
        if not current_price:
            # Fallback to history
            hist = stock.history(period="1d")
            if len(hist) > 0:
                current_price = hist['Close'].iloc[-1]
            else:
                return json.dumps({"error": f"Could not fetch price for {ticker}"})
                
        metrics = {
            "ticker": ticker,
            "current_price": round(current_price, 2),
            "day_high": info.get("dayHigh"),
            "day_low": info.get("dayLow"),
            "pe_ratio": info.get("trailingPE"),
            "market_cap": info.get("marketCap")
        }
        return json.dumps(metrics)
    except Exception as e:
        return json.dumps({"error": str(e)})

def search_company_news(ticker: str) -> str:
    """Fetches the latest news headlines for the company."""
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        if not news:
            return json.dumps({"error": f"No news found for {ticker}"})
            
        headlines = []
        for article in news[:5]: # top 5
            headlines.append({
                "title": article.get("title"),
                "publisher": article.get("publisher"),
                "link": article.get("link")
            })
        return json.dumps({"news": headlines})
    except Exception as e:
        return json.dumps({"error": str(e)})

def execute_paper_trade(ticker: str, action: str, amount: float) -> str:
    """Execute a BUY or SELL paper trade."""
    action = action.upper()
    if action not in ["BUY", "SELL"]:
        return json.dumps({"error": "Action must be BUY or SELL"})
        
    portfolio = _load_db(PORTFOLIO_FILE, {"positions": [], "cash": 100000.0})
    
    # Simple logic
    trade = {
        "ticker": ticker,
        "action": action,
        "amount": amount,
        "timestamp": datetime.now().isoformat()
    }
    portfolio["positions"].append(trade)
    _save_db(PORTFOLIO_FILE, portfolio)
    
    return json.dumps({"status": "Success", "message": f"Successfully executed {action} of {amount} USD on {ticker}", "trade": trade})

def set_price_alert(ticker: str, target_price: float, direction: str) -> str:
    """Set an alert for a stock price."""
    direction = direction.lower()
    if direction not in ["above", "below"]:
        return json.dumps({"error": "Direction must be 'above' or 'below'"})
        
    alerts = _load_db(ALERTS_FILE, [])
    
    alert = {
        "ticker": ticker,
        "target_price": target_price,
        "direction": direction,
        "timestamp": datetime.now().isoformat()
    }
    alerts.append(alert)
    _save_db(ALERTS_FILE, alerts)
    
    return json.dumps({"status": "Success", "message": f"Alert set for {ticker} going {direction} {target_price}"})

def get_technical_analysis(ticker: str) -> str:
    """Calculates SMAs, RSI, MACD, and statistical momentum for future price prediction."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if len(hist) < 200:
            return json.dumps({"error": "Not enough historical data for full analysis"})
            
        close = hist['Close']
        current_price = close.iloc[-1]
        
        # SMAs
        sma_50 = close.rolling(window=50).mean().iloc[-1]
        sma_200 = close.rolling(window=200).mean().iloc[-1]
        
        # 14-day RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs)).iloc[-1]
        
        # MACD
        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd = (ema_12 - ema_26).iloc[-1]
        signal = (ema_12 - ema_26).ewm(span=9, adjust=False).mean().iloc[-1]
        
        # Trend Analysis
        signals = []
        bullish_score = 0
        
        if sma_50 > sma_200:
            signals.append("Long-term Uptrend")
            bullish_score += 1
        else:
            signals.append("Long-term Downtrend")
            bullish_score -= 1
            
        if rsi < 30:
            signals.append("Oversold (Reversal Expected)")
            bullish_score += 2
        elif rsi > 70:
            signals.append("Overbought (Pullback Expected)")
            bullish_score -= 2
            
        if macd > signal:
            signals.append("Bullish MACD Cross")
            bullish_score += 1
        else:
            signals.append("Bearish MACD Cross")
            bullish_score -= 1
            
        # Statistical Projection (Drift + Volatility)
        returns = close.pct_change().dropna()
        daily_drift = returns.mean()
        daily_volatility = returns.std()
        
        # Project 21 trading days (1 month)
        # We adjust the drift based on our bullish score to give the AI's "prediction"
        adjusted_drift = daily_drift + (bullish_score * 0.001) 
        projected_1mo = current_price * (1 + adjusted_drift)**21
        
        analysis = {
            "ticker": ticker,
            "current_price": round(current_price, 2),
            "indicators": {
                "50_day_sma": round(sma_50, 2),
                "200_day_sma": round(sma_200, 2),
                "14_day_rsi": round(rsi, 2),
                "macd": round(macd, 2),
                "macd_signal": round(signal, 2)
            },
            "market_signals": signals,
            "1_month_price_prediction": round(projected_1mo, 2)
        }
        return json.dumps(analysis)
    except Exception as e:
        return json.dumps({"error": str(e)})

# Register all tools
TOOLS = {
    "get_market_data": get_market_data,
    "search_company_news": search_company_news,
    "execute_paper_trade": execute_paper_trade,
    "set_price_alert": set_price_alert,
    "get_technical_analysis": get_technical_analysis
}
