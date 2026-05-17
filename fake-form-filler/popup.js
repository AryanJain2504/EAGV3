document.getElementById('fillFormBtn').addEventListener('click', async () => {
    // Query the currently active tab
    let [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    
    // Send a message to the content script loaded in the active tab
    chrome.tabs.sendMessage(tab.id, { action: "fill_form" });
});