chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "fill_form") {
        fillForms();
    }
});

function fillForms() {
    const inputs = document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]), textarea');
    
    let filledCount = 0;

    inputs.forEach(input => {
        const name = (input.name || '').toLowerCase();
        const type = (input.type || '').toLowerCase();
        const id = (input.id || '').toLowerCase();
        const placeholder = (input.placeholder || '').toLowerCase();
        
        const combinedString = `${name} ${type} ${id} ${placeholder}`;

        // Basic fake data generators
        const randomInt = Math.floor(Math.random() * 10000);
        
        if (type === 'email' || combinedString.includes('email')) {
            input.value = `spamshield+${randomInt}@example.com`;
        } else if (type === 'tel' || combinedString.includes('phone') || combinedString.includes('mobile') || combinedString.includes('contact')) {
            input.value = `+1555${Math.floor(1000000 + Math.random() * 9000000)}`;
        } else if (combinedString.includes('first')) {
            input.value = "Alex";
        } else if (combinedString.includes('last')) {
            input.value = "Smith";
        } else if (combinedString.includes('name')) {
            input.value = "Alex Smith";
        } else if (combinedString.includes('address')) {
            input.value = "123 Fake Street, Faketown, FK 12345";
        } else if (combinedString.includes('company') || combinedString.includes('organization')) {
            input.value = "SpamShield Solutions";
        } else if (type === 'text' || input.tagName.toLowerCase() === 'textarea') {
            input.value = "Lorem ipsum dolor sit amet, just filling up this required field.";
        } else {
            return; // Skip if we don't know what to put
        }
        
        // Dispatch events so frontend frameworks (React/Vue/Angular) register the change
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        
        filledCount++;
    });
    
    if (filledCount > 0) {
        alert(`SpamShield: Successfully filled ${filledCount} field(s) with fake data!`);
    } else {
        alert("SpamShield: Could not find any suitable form fields to fill.");
    }
}