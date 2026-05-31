from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
import json
from pydantic import BaseModel
from typing import List

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TabInfo(BaseModel):
    id: int
    title: str
    url: str
    content: str = ""

class OrganizeRequest(BaseModel):
    tabs: List[TabInfo]
    apiKey: str

@app.post("/api/organize")
async def organize_tabs(req: OrganizeRequest):
    if not req.apiKey:
        raise HTTPException(status_code=400, detail="API Key is required")
        
    # TOON (Token Object Oriented Notation) - highly compressed input to save tokens
    tabs_text = ""
    for t in req.tabs:
        tabs_text += f"ID:{t.id}|T:{t.title}|U:{t.url}|C:{t.content[:500]}\n"
    
    prompt = f"""
I have these browser tabs open:
{tabs_text}

Categorize them logically (max 5 groups). Assign one color to each group: grey, blue, red, yellow, green, pink, purple, cyan. 
Identify junk/duplicate tabs to close.

RESPOND STRICTLY IN THIS COMPACT NOTATION (NO MARKDOWN, NO JSON):
GROUP|Title|Color|id1,id2,id3
CLOSE|id4,id5
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent?key={req.apiKey}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "response_mime_type": "text/plain"
        }
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        
        text = data['candidates'][0]['content']['parts'][0]['text'].strip()
        
        # Parse the TOON response back into the JSON structure expected by React
        parsed_json = {"groups": [], "closeTabIds": []}
        
        for line in text.split('\n'):
            line = line.strip()
            if not line: continue
            
            parts = line.split('|')
            if parts[0] == "GROUP" and len(parts) >= 4:
                title = parts[1]
                color = parts[2]
                tab_ids_str = parts[3].split(',')
                tab_ids = [int(i.strip()) for i in tab_ids_str if i.strip().isdigit()]
                
                parsed_json["groups"].append({
                    "title": title,
                    "color": color,
                    "tabIds": tab_ids
                })
            elif parts[0] == "CLOSE" and len(parts) >= 2:
                tab_ids_str = parts[1].split(',')
                tab_ids = [int(i.strip()) for i in tab_ids_str if i.strip().isdigit()]
                parsed_json["closeTabIds"].extend(tab_ids)
                
        return parsed_json
    except Exception as e:
        print("Error calling Gemini API:", e)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
