# SpamShield: Fake Form Filler 🛡️

A Chrome extension designed to protect your privacy and help you avoid spam. Stop giving away your real phone number and email to websites you don't trust! With a single click, this extension injects random, fake details into the web forms on your current page.

## Features
- Intelligently detects names, emails, phone numbers, addresses, and companies.
- Fills text areas with placeholder text.
- Triggers browser events automatically so modern frameworks like React/Vue recognize the input.

## How to Install and Use in Development Mode

1. Clone or download this repository.
2. Open Google Chrome and go to `chrome://extensions/`.
3. In the top right corner, turn on **Developer mode**.
4. Click **Load unpacked** in the top left.
5. Select the folder containing these extension files (e.g., `fake-form-filler`).
6. Pin the extension to your toolbar.
7. Go to any website with a form, click the extension icon, and click **Fill Fake Data**!

## Demo Video

> **[REQUIRED FOR SUBMISSION]**
> Watch the demo video below to see SpamShield in action!
> 
> [**CLICK HERE TO WATCH THE DEMO VIDEO**](#link-to-your-video-here) *(Replace this line with your actual YouTube/Loom/OBS video link!)*

## Technical Details (Code Submitted)
- `manifest.json`: Configuration file explicitly requesting `activeTab` permissions.
- `popup.html` / `popup.js`: The extension's user interface and script to trigger form injection.
- `content.js`: The script that runs on the web page to select DOM nodes and insert fake data securely. 