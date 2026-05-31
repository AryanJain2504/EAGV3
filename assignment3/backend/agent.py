import os
import json
import re
import asyncio
from typing import AsyncGenerator
import google.generativeai as genai
from tools import TOOLS

genai.configure(api_key=os.getenv("GEMINI_API_KEY") or "dummy")
model = genai.GenerativeModel('gemini-flash-lite-latest')

SYSTEM_PROMPT = """You are an Autonomous Financial Agent. You act like a highly decisive, authoritative hedge fund manager. You research stocks, predict trends, and autonomously execute paper trades.

You MUST NOT be vague or wishy-washy. If asked for advice, you must give a definitive "STRONG BUY", "BUY", "HOLD", "SELL", or "STRONG SELL" rating based on your analysis. 

You have access to these tools:
1. get_market_data(ticker: str)
2. search_company_news(ticker: str)
3. get_technical_analysis(ticker: str) - Calculates 50/200 day SMAs, trend signals, and a 1-month future price prediction. MUST use this if asked for a future prediction.
4. execute_paper_trade(ticker: str, action: str, amount: float) - action must be "BUY" or "SELL"
5. set_price_alert(ticker: str, target_price: float, direction: str) - direction must be "above" or "below"

If you need to use a tool, reply in this EXACT format:
```json
{"tool_name": "<name>", "tool_arguments": {"arg1": "val1"}}
```

If you have the final answer and have finished executing actions, reply in this EXACT format. Your answer MUST be highly structured, decisive, and authoritative.
```json
{"answer": "<your final analysis, definitive rating, and exact summary of actions taken>"}
```

Always respond with ONLY the JSON, nothing else. No extra commentary outside the JSON block.
"""

def parse_llm_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(lines).strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError(f"Could not parse: {text}")

async def run_agent_loop(user_query: str) -> AsyncGenerator[str, None]:
    yield json.dumps({"status": "info", "message": f"Starting research for: '{user_query}'"})
    
    messages = [
        {"role": "user", "parts": [SYSTEM_PROMPT + "\n\nUser: " + user_query]}
    ]
    
    max_iters = 6
    for i in range(max_iters):
        yield json.dumps({"status": "thinking", "message": "Agent is thinking..."})
        
        # In google-generativeai, we can pass the messages list directly, but it requires alternating user/model
        # Actually it's simpler to just stringify the conversation if we don't use strict ChatSession
        prompt = ""
        for m in messages:
            role = m["role"]
            content = m["parts"][0]
            if role == "user":
                prompt += f"{content}\n\n"
            else:
                prompt += f"Assistant: {content}\n\n"
                
        # To avoid blocking the event loop
        response = await asyncio.to_thread(model.generate_content, prompt)
        response_text = response.text
        
        # Add to history
        messages.append({"role": "model", "parts": [response_text]})
        
        try:
            parsed = parse_llm_response(response_text)
        except Exception as e:
            yield json.dumps({"status": "error", "message": f"Parse error. Forcing retry."})
            messages.append({"role": "user", "parts": ["Parse error. Please respond ONLY with valid JSON."]})
            continue
            
        if "answer" in parsed:
            yield json.dumps({"status": "final", "message": parsed["answer"]})
            return
            
        if "tool_name" in parsed:
            tool_name = parsed["tool_name"]
            args = parsed.get("tool_arguments", {})
            
            # Format arguments beautifully e.g. ticker="AAPL", action="BUY"
            formatted_args = ", ".join([f'{k}="{v}"' if isinstance(v, str) else f'{k}={v}' for k, v in args.items()])
            
            yield json.dumps({"status": "action", "message": f"Calling tool: {tool_name}({formatted_args})"})
            
            if tool_name not in TOOLS:
                err_msg = json.dumps({"error": f"Tool {tool_name} not found"})
                messages.append({"role": "user", "parts": [f"Tool Result: {err_msg}"]})
                continue
                
            # Execute tool
            try:
                # Async sleep to simulate delay and yield to event loop
                await asyncio.sleep(0.5)
                tool_result = await asyncio.to_thread(TOOLS[tool_name], **args)
                yield json.dumps({"status": "result", "message": f"Got result from {tool_name}"})
                messages.append({"role": "user", "parts": [f"Tool Result: {tool_result}"]})
            except Exception as e:
                err_msg = json.dumps({"error": str(e)})
                messages.append({"role": "user", "parts": [f"Tool Result: {err_msg}"]})
                
    yield json.dumps({"status": "error", "message": "Max iterations reached without a final answer."})
