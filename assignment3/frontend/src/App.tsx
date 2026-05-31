import { useState, useRef, useEffect } from 'react'
import './App.css'

type Step = {
  status: 'info' | 'thinking' | 'action' | 'result' | 'final' | 'error'
  message: string
}

function App() {
  const [prompt, setPrompt] = useState('')
  const [steps, setSteps] = useState<Step[]>([])
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [toastAlert, setToastAlert] = useState<{title: string, message: string} | null>(null)
  const stepsEndRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = () => {
    stepsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [steps])

  // Continuous Monitoring Listener
  useEffect(() => {
    const alertSource = new EventSource('http://127.0.0.1:8000/api/alerts/stream')
    
    alertSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'price_alert') {
          setToastAlert({
            title: `🚨 PRICE ALERT: ${data.ticker}`,
            message: `${data.ticker} crossed ${data.direction} $${data.target}. Live Price: $${data.price}`
          })
          
          // Auto-hide toast after 8 seconds
          setTimeout(() => {
            setToastAlert(null)
          }, 8000)
        }
      } catch (e) {
        console.error("Failed to parse alert", e)
      }
    }
    
    return () => alertSource.close()
  }, [])

  const runAgent = async () => {
    if (!prompt.trim()) return
    
    setSteps([])
    setIsAnalyzing(true)

    const eventSource = new EventSource(`http://127.0.0.1:8000/api/analyze?query=${encodeURIComponent(prompt)}`)
    
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        setSteps(prev => [...prev, data])
        
        if (data.status === 'final' || data.status === 'error') {
          eventSource.close()
          setIsAnalyzing(false)
        }
      } catch (e) {
        console.error("Failed to parse SSE message", e)
      }
    }
    
    eventSource.onerror = (err) => {
      console.error("EventSource failed:", err)
      eventSource.close()
      setIsAnalyzing(false)
      setSteps(prev => [...prev, { status: 'error', message: 'Connection to agent failed. Is the backend running?' }])
    }
  }

  const intermediateSteps = steps.filter(s => s.status !== 'final' && s.status !== 'thinking')
  const finalStep = steps.find(s => s.status === 'final')

  const getStatusIcon = (status: string) => {
    switch(status) {
      case 'thinking': return <div className="spinner-small"></div>;
      case 'action': return '⚡';
      case 'result': return '✅';
      case 'error': return '❌';
      case 'final': return '🎯';
      default: return 'ℹ️';
    }
  }

  return (
    <div className="app-wrapper">
      <div className="dashboard-grid">
        
        {/* Left Column: Controls & Results */}
        <section className="control-panel">
          <header className="app-header">
            <h1>Quant<span className="accent">AI</span></h1>
            <p>Autonomous Financial Intelligence</p>
          </header>

          <div className="input-card">
            <textarea 
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Deploy directive (e.g. Analyze TSLA and execute a paper trade if RSI < 40)..."
              disabled={isAnalyzing}
            />
            <button 
              onClick={runAgent} 
              disabled={isAnalyzing || !prompt.trim()}
              className={`run-btn ${isAnalyzing ? 'running' : ''}`}
            >
              {isAnalyzing ? (
                <><div className="spinner"></div> Processing...</>
              ) : (
                'Deploy Agent →'
              )}
            </button>
          </div>

          {finalStep && (
            <div className="final-result-card appear-anim">
              <div className="card-header">
                <div className="pulse-dot"></div>
                <h3>Final Verdict</h3>
              </div>
              <div className="card-body">
                {finalStep.message}
              </div>
            </div>
          )}
        </section>

        {/* Right Column: Live Execution Terminal */}
        <section className="execution-terminal">
          <div className="terminal-header">
            <div className="window-controls">
              <span className="red"></span><span className="yellow"></span><span className="green"></span>
            </div>
            <div className="terminal-title">Live Execution Trace</div>
          </div>
          
          <div className="terminal-body">
            {steps.length === 0 && !isAnalyzing ? (
              <div className="terminal-empty">
                <div className="radar-scanner"></div>
                <p>System Online. Awaiting Directives.</p>
              </div>
            ) : (
              <div className="step-list">
                {intermediateSteps.map((step, idx) => (
                  <div key={idx} className={`step-item step-${step.status}`}>
                    <div className="step-icon">{getStatusIcon(step.status)}</div>
                    <div className="step-content">
                      <span className="step-badge">{step.status.toUpperCase()}</span>
                      <span className="step-text">{step.message}</span>
                    </div>
                  </div>
                ))}
                {isAnalyzing && (
                  <div className="typing-indicator">Agent is thinking<span>.</span><span>.</span><span>.</span></div>
                )}
                <div ref={stepsEndRef} />
              </div>
            )}
          </div>
        </section>

      </div>

      {/* Global Toast Notification */}
      {toastAlert && (
        <div className="toast-notification slide-in-right">
          <div className="toast-icon">🚨</div>
          <div className="toast-content">
            <h4>{toastAlert.title}</h4>
            <p>{toastAlert.message}</p>
          </div>
          <button className="toast-close" onClick={() => setToastAlert(null)}>×</button>
        </div>
      )}
    </div>
  )
}

export default App;
