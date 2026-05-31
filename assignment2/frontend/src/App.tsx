import { useState, useEffect } from 'react'
import './App.css'

function App() {
  const [apiKey, setApiKey] = useState('');
  const [hasKey, setHasKey] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState({ message: '', type: '' });
  const [result, setResult] = useState({ groups: 0, closed: 0 });

  useEffect(() => {
    // Load API key on mount
    if (chrome?.storage?.local) {
      chrome.storage.local.get(['geminiApiKey'], (result) => {
        if (result.geminiApiKey) {
          setApiKey(result.geminiApiKey as string);
          setHasKey(true);
        }
      });
    }
  }, []);

  const saveApiKey = () => {
    if (!apiKey.trim()) return;
    if (chrome?.storage?.local) {
      chrome.storage.local.set({ geminiApiKey: apiKey.trim() }, () => {
        setHasKey(true);
        setStatus({ message: 'API Key saved successfully!', type: 'success' });
        setTimeout(() => setStatus({ message: '', type: '' }), 3000);
      });
    } else {
      setHasKey(true);
    }
  };

  const organizeTabs = async () => {
    setLoading(true);
    setStatus({ message: 'Analyzing tabs with Gemini...', type: '' });
    setResult({ groups: 0, closed: 0 });
    
    try {
      if (!chrome?.tabs) {
        throw new Error("Chrome Tabs API not available. Are you running as an extension?");
      }

      // 1. Get all tabs in current window
      const tabs = await chrome.tabs.query({ currentWindow: true });
      
      const tabData = [];
      for (const t of tabs) {
        if (!t.id || !t.url || t.url.startsWith('chrome://') || t.url.startsWith('edge://') || t.url.startsWith('about:')) continue;
        
        let content = '';
        try {
          const injectionResults = await chrome.scripting.executeScript({
            target: { tabId: t.id },
            func: () => document.body ? document.body.innerText.substring(0, 1000) : ''
          });
          if (injectionResults && injectionResults[0] && injectionResults[0].result) {
            content = injectionResults[0].result;
          }
        } catch (e) {
          console.log("Could not read content for tab:", t.url);
        }

        tabData.push({
          id: t.id,
          title: t.title || '',
          url: t.url,
          content
        });
      }

      if (tabData.length === 0) {
         throw new Error("No valid tabs to organize.");
      }

      // 2. Send to backend
      const response = await fetch('http://127.0.0.1:8000/api/organize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tabs: tabData, apiKey })
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Failed to fetch from backend');
      }

      const data = await response.json();
      
      let groupsCreated = 0;
      let tabsClosed = 0;

      // 3. Close junk tabs
      if (data.closeTabIds && data.closeTabIds.length > 0) {
        await chrome.tabs.remove(data.closeTabIds);
        tabsClosed = data.closeTabIds.length;
      }

      // 4. Group remaining tabs
      if (data.groups && data.groups.length > 0) {
        for (const group of data.groups) {
          if (group.tabIds && group.tabIds.length > 0) {
            // Check which tabIds actually still exist
            const existingTabs = tabData.filter(t => group.tabIds.includes(t.id));
            if (existingTabs.length === 0) continue;
            
            const validTabIds = existingTabs.map(t => t.id);
            const groupId = (await chrome.tabs.group({ tabIds: validTabIds as any })) as number;
            
            // Map the color slightly if Gemini returns an invalid color
            let validColor = group.color?.toLowerCase();
            const allowedColors = ["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan"];
            if (!allowedColors.includes(validColor)) validColor = "grey";
            
            await chrome.tabGroups.update(groupId, {
              title: group.title,
              color: validColor as chrome.tabGroups.Color
            });
            groupsCreated++;
          }
        }
      }

      setResult({ groups: groupsCreated, closed: tabsClosed });
      setStatus({ message: 'Tabs organized successfully!', type: 'success' });
      
    } catch (err: any) {
      console.error(err);
      setStatus({ message: err.message || 'An error occurred', type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <h1>Tab Organizer</h1>
      
      {!hasKey ? (
        <div className="glass-panel">
          <p style={{ fontSize: '14px', margin: '0 0 8px 0' }}>Enter your Gemini API Key to get started:</p>
          <input 
            type="password" 
            placeholder="AIzaSy..." 
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          <button onClick={saveApiKey}>Save API Key</button>
        </div>
      ) : (
        <div className="glass-panel" style={{ alignItems: 'center' }}>
          <p style={{ fontSize: '14px', textAlign: 'center', margin: '0' }}>
            Ready to magically organize your tabs.
          </p>
          
          <button 
            onClick={organizeTabs} 
            disabled={loading}
            style={{ width: '100%', marginTop: '10px' }}
          >
            {loading ? 'Organizing...' : 'Organize My Tabs ✨'}
          </button>
          
          {loading && (
            <div style={{ marginTop: '20px' }}>
              <div className="spinner"></div>
            </div>
          )}

          {result.groups > 0 && (
            <div className="results">
              <p style={{color: '#4ade80'}}>🗂️ Created <strong>{result.groups}</strong> tab groups</p>
              {result.closed > 0 && (
                <p style={{color: '#fbbf24'}}>🗑️ Closed <strong>{result.closed}</strong> junk tabs</p>
              )}
            </div>
          )}
          
          <div style={{marginTop: '20px', width: '100%', display: 'flex', justifyContent: 'center'}}>
             <button 
              style={{
                background: 'transparent', 
                border: '1px solid rgba(255,255,255,0.2)', 
                padding: '6px 12px', 
                fontSize: '12px',
                color: '#94a3b8'
              }} 
              onClick={() => {
                setHasKey(false);
                setApiKey('');
              }}>
               Change API Key
             </button>
          </div>
        </div>
      )}

      {status.message && (
        <div className={`status-message ${status.type}`}>
          {status.message}
        </div>
      )}
    </>
  )
}

export default App
