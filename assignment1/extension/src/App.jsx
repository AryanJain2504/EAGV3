import { useEffect, useMemo, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000';

const emptyInsight = {
  title: '',
  url: '',
  summary: [],
  relatedContent: [],
  pageIntent: 'research',
  keyTerms: [],
  linkCategories: {},
  selectionUsed: false,
  source: 'fallback',
};

const SUMMARY_BOOKMARK_FOLDER_TITLE = 'PagePilot Summaries';

const trimText = (value, maxLength) => {
  if (!value) {
    return '';
  }

  return value.length > maxLength ? `${value.slice(0, maxLength - 1).trimEnd()}…` : value;
};

const buildSummarySnippet = (summaryItems) => {
  const text = (summaryItems?.length ? summaryItems : ['No summary available yet.']).join(' ');
  return trimText(text.replace(/\s+/g, ' ').trim(), 110);
};

const buildBookmarkTitle = (title, summaryItems) => {
  const snippet = buildSummarySnippet(summaryItems);
  const composed = snippet ? `PagePilot: ${title} — ${snippet}` : `PagePilot: ${title}`;
  return trimText(composed, 140);
};

const searchBookmarks = (query) =>
  new Promise((resolve) => {
    chrome.bookmarks.search(query, resolve);
  });

const createBookmark = (bookmarkInfo) =>
  new Promise((resolve) => {
    chrome.bookmarks.create(bookmarkInfo, resolve);
  });

const createBookmarkFolder = (folderInfo) =>
  new Promise((resolve) => {
    chrome.bookmarks.create(folderInfo, resolve);
  });

const getStorage = (keys) =>
  new Promise((resolve) => {
    chrome.storage.local.get(keys, resolve);
  });

const setStorage = (items) =>
  new Promise((resolve) => {
    chrome.storage.local.set(items, resolve);
  });

function App() {
  const [apiKey, setApiKey] = useState('');
  const [saved, setSaved] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [insight, setInsight] = useState(emptyInsight);
  const [status, setStatus] = useState('Ready to capture the current page.');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [expandedCategories, setExpandedCategories] = useState({});
  const [savedSummaries, setSavedSummaries] = useState([]);
  const [actionFeedback, setActionFeedback] = useState('');
  const [bookmarkedCount, setBookmarkedCount] = useState(0);

  const categoryEntries = useMemo(() => Object.entries(insight.linkCategories || {}), [insight.linkCategories]);
  const shareText = useMemo(() => {
    if (!insight.title) {
      return '';
    }

    const summaryText = (insight.summary?.length ? insight.summary : ['No summary available yet.']).join(' ');

    return [
      `PagePilot summary: ${insight.title}`,
      insight.url,
      '',
      summaryText,
      '',
      `Intent: ${insight.pageIntent}`,
      `Key terms: ${(insight.keyTerms || []).join(', ') || 'None'}`,
    ].join('\n');
  }, [insight]);

  const toggleCategory = (category) => {
    setExpandedCategories((prev) => ({
      ...prev,
      [category]: !prev[category],
    }));
  };

  useEffect(() => {
    chrome.storage.local.get(['pagepilotApiKey', 'pagepilotSavedSummaries'], (result) => {
      if (result.pagepilotApiKey) {
        setApiKey(result.pagepilotApiKey);
        setSaved(true);
      }

      if (Array.isArray(result.pagepilotSavedSummaries)) {
        setSavedSummaries(result.pagepilotSavedSummaries);
        setBookmarkedCount(result.pagepilotSavedSummaries.length);
      }
    });
  }, []);

  const saveApiKey = () => {
    chrome.storage.local.set({ pagepilotApiKey: apiKey.trim() }, () => {
      setSaved(true);
      setTimeout(() => setShowSettings(false), 300);
    });
  };

  const saveSummaryRecord = async ({ bookmarkId, bookmarkFolderId }) => {
    const record = {
      bookmarkId,
      bookmarkFolderId,
      title: insight.title,
      url: insight.url,
      summary: insight.summary,
      summarySnippet: buildSummarySnippet(insight.summary),
      relatedContent: insight.relatedContent,
      pageIntent: insight.pageIntent,
      keyTerms: insight.keyTerms,
      savedAt: new Date().toISOString(),
    };

    const nextSavedSummaries = [record, ...savedSummaries.filter((item) => item.url !== insight.url)].slice(0, 12);

    await setStorage({ pagepilotSavedSummaries: nextSavedSummaries });
    setSavedSummaries(nextSavedSummaries);
    setBookmarkedCount(nextSavedSummaries.length);
  };

  const handleCopySummary = async () => {
    if (!shareText) {
      return;
    }

    await navigator.clipboard.writeText(shareText);
    setActionFeedback('Summary copied to clipboard.');
  };

  const handleWhatsAppShare = async () => {
    if (!shareText) {
      return;
    }

    const shareUrl = `https://wa.me/?text=${encodeURIComponent(shareText)}`;
    await chrome.tabs.create({ url: shareUrl });
    setActionFeedback('Opened WhatsApp share.');
  };

  const handleBookmarkSummary = async () => {
    if (!insight.title) {
      return;
    }

    const [existingFolder] = await searchBookmarks({ title: SUMMARY_BOOKMARK_FOLDER_TITLE });
    let folderId = existingFolder?.id;

    if (!folderId) {
      const createdFolder = await createBookmarkFolder({ title: SUMMARY_BOOKMARK_FOLDER_TITLE });
      folderId = createdFolder.id;
    }

    const bookmark = await createBookmark({
      parentId: folderId,
      title: buildBookmarkTitle(insight.title, insight.summary),
      url: insight.url,
    });

    await saveSummaryRecord({ bookmarkId: bookmark.id, bookmarkFolderId: folderId });
    setActionFeedback(`Saved summary bookmark in ${SUMMARY_BOOKMARK_FOLDER_TITLE}.`);
  };

  useEffect(() => {
    if (!actionFeedback) {
      return undefined;
    }

    const timeoutId = setTimeout(() => setActionFeedback(''), 2500);
    return () => clearTimeout(timeoutId);
  }, [actionFeedback]);

  const capturePage = async () => {
    setLoading(true);
    setError('');
    setStatus('Reading the active tab...');

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab?.id) {
        throw new Error('No active tab found.');
      }

      const [{ result: pageData }] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const text = document.body?.innerText || '';
          const links = Array.from(document.querySelectorAll('a[href]'))
            .slice(0, 80)
            .map((link) => ({
              text: (link.innerText || link.getAttribute('aria-label') || link.title || '').trim(),
              url: link.href,
            }));

          return {
            title: document.title || '',
            url: location.href,
            text: text.replace(/\s+/g, ' ').trim(),
            selection: window.getSelection()?.toString().trim() || '',
            links,
          };
        },
      });

      setStatus('Analyzing with Gemini Flash...');

      const response = await fetch(`${API_BASE}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...pageData, apiKey: apiKey.trim() || undefined }),
      });

      if (!response.ok) {
        throw new Error(`Analysis failed with status ${response.status}`);
      }

      setInsight(await response.json());
      setStatus('Page insight ready.');
    } catch (captureError) {
      setError(captureError instanceof Error ? captureError.message : 'Failed to capture page.');
      setStatus('Capture failed.');
      setInsight(emptyInsight);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="shell">
      <header className="hero">
        <div className="hero-content">
          <div>
            <p className="eyebrow">PagePilot</p>
            <h1>Summarize any page instantly</h1>
            <p className="subtitle">Get AI insights and smart content suggestions</p>
          </div>
          <div className="hero-actions">
            <button className="primary" onClick={capturePage} disabled={loading}>
              <span className="button-icon">{loading ? '⏳' : '📸'}</span>
              {loading ? 'Analyzing...' : 'Capture page'}
            </button>
            <button className="settings-btn" onClick={() => setShowSettings(!showSettings)} title="Settings">
              ⚙️
            </button>
          </div>
        </div>
      </header>

      {showSettings && (
        <section className="card settings-card">
          <div className="settings-header">
            <h3>API Settings</h3>
            <button className="close-btn" onClick={() => setShowSettings(false)}>✕</button>
          </div>
          <label className="field-label" htmlFor="apiKey">Gemini API Key</label>
          <div className="input-group">
            <input
              id="apiKey"
              type={showApiKey ? 'text' : 'password'}
              placeholder="Paste your Gemini Flash key"
              value={apiKey}
              onChange={(event) => {
                setApiKey(event.target.value);
                setSaved(false);
              }}
            />
            <button className="toggle-btn" onClick={() => setShowApiKey(!showApiKey)}>
              {showApiKey ? '🙈' : '👁️'}
            </button>
          </div>
          <button className="save-btn" onClick={saveApiKey}>
            {saved && !apiKey ? '✓ Saved' : 'Save API Key'}
          </button>
          <p className="settings-info">
            {saved ? '✓ Key saved locally in browser' : 'Optional. Get free key from Google AI Studio'}
          </p>
        </section>
      )}

      <div className="status-bar">
        <p className="status">{status}</p>
        {error && <p className="error">⚠️ {error}</p>}
      </div>

      {insight.title && (
        <section className="card insight-panel slide-in">
          <div className="insight-header">
            <div className="insight-title">
              <p className="eyebrow">Current Page</p>
              <h2 className="truncate">{insight.title}</h2>
              <p className="url-text truncate">{insight.url}</p>
            </div>
            <div className="source-badge">
              <span className={`badge-dot ${insight.source}`}></span>
              <span>{insight.source === 'gemini' ? 'AI' : 'Local'}</span>
            </div>
          </div>

          <div className="metadata-row">
            <span className="meta-pill">
              <span className="meta-icon">🎯</span>
              {insight.pageIntent}
            </span>
            <span className="meta-pill">
              <span className="meta-icon">📝</span>
              {insight.keyTerms?.length || 0} terms
            </span>
            {insight.selectionUsed && (
              <span className="meta-pill">
                <span className="meta-icon">✂️</span>
                Selection used
              </span>
            )}
          </div>

          <div className="action-row">
            <button className="action-button secondary" onClick={handleCopySummary} disabled={!shareText}>
              📋 Copy summary
            </button>
            <button className="action-button secondary" onClick={handleWhatsAppShare} disabled={!shareText}>
              💬 WhatsApp
            </button>
            <button className="action-button primary" onClick={handleBookmarkSummary} disabled={!shareText}>
              🔖 Save bookmark
            </button>
          </div>

          {(actionFeedback || savedSummaries.length > 0) && (
            <div className="share-panel">
              {actionFeedback && <p className="share-feedback">{actionFeedback}</p>}
              {savedSummaries.length > 0 && (
                <p className="share-note">
                  {savedSummaries.length} saved summary{savedSummaries.length === 1 ? '' : 's'} stored locally and mirrored in bookmarks.
                </p>
              )}
            </div>
          )}

          <div className="content-grid">
            <div className="content-card">
              <div className="card-header">
                <h3>Summary</h3>
                <span className="card-icon">📄</span>
              </div>
              <ul className="bullet-list">
                {(insight.summary?.length ? insight.summary : ['Capture a page to generate a summary.']).map(
                  (item, idx) => (
                    <li key={idx} className="bullet-item" style={{ '--delay': `${idx * 50}ms` }}>
                      {item}
                    </li>
                  ),
                )}
              </ul>
            </div>

            <div className="content-card">
              <div className="card-header">
                <h3>What to explore</h3>
                <span className="card-icon">🔍</span>
              </div>
              <ul className="bullet-list">
                {(insight.relatedContent?.length ? insight.relatedContent : ['No suggestions yet.']).map((item, idx) => (
                  <li key={idx} className="bullet-item" style={{ '--delay': `${idx * 50}ms` }}>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {categoryEntries.length > 0 && (
            <div className="links-section">
              <div className="section-header">
                <h3>Links on this page</h3>
                <span className="link-count">{categoryEntries.reduce((sum, [, items]) => sum + items.length, 0)}</span>
              </div>
              <div className="link-groups">
                {categoryEntries.map(([category, items], idx) => {
                  const isExpanded = expandedCategories[category];
                  const displayItems = isExpanded ? items : items.slice(0, 3);
                  const hasMore = items.length > 3;

                  return (
                    <div className="link-group" key={category} style={{ '--delay': `${idx * 30}ms` }}>
                      <h4 className="category-name">{category}</h4>
                      <ul className="link-list">
                        {displayItems.map((item) => (
                          <li key={`${item.url}-${item.text}`} className="link-item">
                            <span className="link-text">{item.text || 'Untitled'}</span>
                            <a href={item.url} target="_blank" rel="noreferrer" className="link-action">
                              Open ↗
                            </a>
                          </li>
                        ))}
                      </ul>
                      {hasMore && (
                        <button
                          className="expand-more"
                          onClick={() => toggleCategory(category)}
                        >
                          {isExpanded ? '▼ Show less' : `▶ +${items.length - 3} more`}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </section>
      )}

      {!insight.title && !loading && (
        <section className="empty-state">
          <div className="empty-icon">🚀</div>
          <h2>Ready to analyze</h2>
          <p>Visit any website and click "Capture page" to get AI-powered insights, then copy, WhatsApp, or bookmark the summary.</p>
        </section>
      )}
    </div>
  );
}

export default App;
