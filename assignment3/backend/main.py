import os
import json
import asyncio
import yfinance as yf
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from agent import run_agent_loop

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

alert_queue = asyncio.Queue()

async def monitor_prices():
    while True:
        try:
            if os.path.exists("alerts.json"):
                with open("alerts.json", "r") as f:
                    try:
                        alerts = json.load(f)
                    except json.JSONDecodeError:
                        alerts = []
                
                triggered = []
                remaining = []
                
                for alert in alerts:
                    ticker = alert["ticker"]
                    target = alert["target_price"]
                    direction = alert["direction"]
                    
                    try:
                        # Fetch live price
                        stock = yf.Ticker(ticker)
                        hist = await asyncio.to_thread(stock.history, period="1d")
                        if hist.empty:
                            remaining.append(alert)
                            continue
                            
                        price = hist['Close'].iloc[-1]
                        
                        is_triggered = False
                        if direction == "above" and price >= target:
                            is_triggered = True
                        elif direction == "below" and price <= target:
                            is_triggered = True
                            
                        if is_triggered:
                            msg = {
                                "ticker": ticker, 
                                "price": round(price, 2), 
                                "target": target, 
                                "direction": direction,
                                "type": "price_alert"
                            }
                            triggered.append(msg)
                            await alert_queue.put(msg)
                        else:
                            remaining.append(alert)
                    except Exception as e:
                        print(f"Error checking price for {ticker}: {e}")
                        remaining.append(alert)
                
                # If we triggered alerts, rewrite the file to remove them (one-shot alerts)
                if triggered:
                    with open("alerts.json", "w") as f:
                        json.dump(remaining, f, indent=4)
        except Exception as e:
            print(f"Monitor loop error: {e}")
            
        await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_prices())

@app.get("/api/analyze")
async def analyze(query: str):
    return EventSourceResponse(run_agent_loop(query))

@app.get("/api/alerts/stream")
async def stream_alerts(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await asyncio.wait_for(alert_queue.get(), timeout=2.0)
                yield json.dumps(msg)
            except asyncio.TimeoutError:
                # Keep connection alive with heartbeat
                yield json.dumps({"type": "ping"})
    return EventSourceResponse(event_generator())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
