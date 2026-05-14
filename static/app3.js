/* global Office */

let currentItem = null;

Office.onReady((info) => {
    if (info.host === Office.HostType.Outlook) {
        currentItem = Office.context.mailbox.item;
        
        document.getElementById("btnGenerate").onclick = generateDraft;
        document.getElementById("btnSend").onclick = sendReply;
        document.getElementById("btnCancel").onclick = () => {
            document.getElementById("editorSection").style.display = "none";
            document.getElementById("generateSection").style.display = "block";
        };
    }
});

async function generateDraft() {
    const btnText = document.getElementById("btnGenerateText");
    const loader = document.getElementById("generateLoader");
    const errorAlert = document.getElementById("errorAlert");
    
    errorAlert.style.display = "none";
    btnText.style.display = "none";
    loader.style.display = "block";

    try {
        const sender = currentItem.from.emailAddress;
        const subject = currentItem.subject;
        
        // Modern Outlook (Web/New) natively supports restId. Fallback safely.
        let email_id = "";
        try { email_id = currentItem.restId || currentItem.itemId; } catch(e){}

        if (!email_id) {
            // Hard fallback if the property is masked
            try { 
                email_id = Office.context.mailbox.convertToRestId(currentItem.itemId, Office.MailboxEnums.RestVersion.v2_0);
            } catch(e) {}
        }

        // Get the body asynchronously
        currentItem.body.getAsync(Office.CoercionType.Text, async (result) => {
            if (result.status === Office.AsyncResultStatus.Failed) {
                throw new Error("Failed to get email body");
            }
            
            const body = result.value;

            // Collect attachment names from the frontend metadata so the backend can perfectly match the thread item
            const attachmentNames = [];
            if (currentItem.attachments && currentItem.attachments.length > 0) {
                currentItem.attachments.forEach(att => {
                    attachmentNames.push(att.name);
                });
            }

            // Make API call to our local FastAPI server
            const response = await fetch("/api/generate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ 
                    sender, 
                    subject, 
                    body, 
                    email_id, 
                    attachment_names: attachmentNames 
                })
            });

            if (!response.ok) throw new Error("API Request Failed");

            const data = await response.json();
            
            // Show Draft Editor
            document.getElementById("draftText").value = data.draft;
            document.getElementById("generateSection").style.display = "none";
            document.getElementById("editorSection").style.display = "block";
            
            btnText.style.display = "block";
            loader.style.display = "none";
        });
    } catch (e) {
        console.error(e);
        errorAlert.innerText = "⚠️ Failed to generate: " + e.message;
        errorAlert.style.display = "flex";
        btnText.style.display = "block";
        loader.style.display = "none";
    }
}

async function sendReply() {
    const loader = document.getElementById("sendLoader");
    loader.style.display = "block";
    
    try {
        // For sending heavily customized emails, we use standard MS Graph on backend.
        // We pass the email_id to the backend. We can get Graph Item ID using:
        let email_id = currentItem.restId || currentItem.itemId;
        
        // Outlook Web Add-ins sometimes need to convert REST ID to Graph ID format,
        // but EWS itemId is often interoperable or easily translated on backend.
        // For local development, we send itemId as is.
        const draftBody = document.getElementById("draftText").value;

        const response = await fetch("/api/send", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email_id: email_id, draft_body: draftBody })
        });

        if (!response.ok) throw new Error("Send Failed");

        // Close the taskpane or show success
        alert("✅ Reply Sent Successfully!");
        document.getElementById("editorSection").style.display = "none";
        document.getElementById("generateSection").style.display = "block";
        
    } catch (e) {
        console.error(e);
        alert("⚠️ Failed to send: " + e.message);
    } finally {
        loader.style.display = "none";
    }
}
