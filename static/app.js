/* global Office */

let currentItem = null;

function setWorkspaceMode(mode) {
    const outlookEl = document.getElementById("outlookWorkspace");
    const marketingEl = document.getElementById("marketingWorkspace");
    const btnOutlook = document.getElementById("btnModeOutlook");
    const btnMarketing = document.getElementById("btnModeMarketing");

    if (mode === "marketing") {
        outlookEl.style.display = "none";
        marketingEl.style.display = "flex";
        btnOutlook.classList.remove("mode-btn-active");
        btnMarketing.classList.add("mode-btn-active");
        btnOutlook.setAttribute("aria-pressed", "false");
        btnMarketing.setAttribute("aria-pressed", "true");
        loadMarketingPrompt();
    } else {
        outlookEl.style.display = "flex";
        marketingEl.style.display = "none";
        btnOutlook.classList.add("mode-btn-active");
        btnMarketing.classList.remove("mode-btn-active");
        btnOutlook.setAttribute("aria-pressed", "true");
        btnMarketing.setAttribute("aria-pressed", "false");
    }
}

async function loadMarketingPrompt() {
    const textarea = document.getElementById("marketingPromptTextarea");
    const status = document.getElementById("marketingPromptStatus");
    status.textContent = "";
    status.className = "prompt-status";
    try {
        const response = await fetch("/api/marketing/prompt");
        if (!response.ok) throw new Error("Could not load prompt");
        const data = await response.json();
        textarea.value = data.prompt || "";
    } catch (e) {
        console.error(e);
        status.textContent = "Could not load prompt: " + e.message;
        status.className = "prompt-status err";
    }
}

async function saveMarketingPrompt() {
    const textarea = document.getElementById("marketingPromptTextarea");
    const status = document.getElementById("marketingPromptStatus");
    const btnText = document.getElementById("btnSaveMarketingText");
    const loader = document.getElementById("savePromptLoader");

    status.textContent = "";
    status.className = "prompt-status";
    btnText.style.display = "none";
    loader.style.display = "block";

    try {
        const response = await fetch("/api/marketing/prompt", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt: textarea.value }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || data.message || "Save failed");
        }
        textarea.value = data.prompt || textarea.value;
        status.textContent = "Saved. This prompt will be used for the next marketing analysis.";
        status.className = "prompt-status ok";
    } catch (e) {
        console.error(e);
        status.textContent = "Save failed: " + e.message;
        status.className = "prompt-status err";
    } finally {
        btnText.style.display = "block";
        loader.style.display = "none";
    }
}

Office.onReady((info) => {
    document.getElementById("btnModeOutlook").onclick = () => setWorkspaceMode("outlook");
    document.getElementById("btnModeMarketing").onclick = () => setWorkspaceMode("marketing");
    document.getElementById("btnSaveMarketingPrompt").onclick = saveMarketingPrompt;

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
        // Extract basic data from Outlook native API
        const sender = currentItem.from.emailAddress;
        const subject = currentItem.subject;
        const ews_id = currentItem.itemId;
        // MS Graph API requires REST ID format, not the default EWS format.
        const email_id = Office.context.mailbox.convertToRestId(ews_id, Office.MailboxEnums.RestVersion.v2_0);
        const conversation_id = currentItem.conversationId;

        // Get the body asynchronously
        currentItem.body.getAsync(Office.CoercionType.Text, async (result) => {
            if (result.status === Office.AsyncResultStatus.Failed) {
                throw new Error("Failed to get email body");
            }

            const body = result.value;

            // Make API call to our local FastAPI server using relative path to avoid Mixed Content block
            const response = await fetch("/api/generate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sender, subject, body, email_id, conversation_id })
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
        let email_id = currentItem.itemId;

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
