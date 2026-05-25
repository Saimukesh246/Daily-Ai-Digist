/* ==========================================================================
   Daily AI Digest - Dashboard JS Controller
   Handles: API communication, Dynamic DOM injection, Sync Polling, Settings
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
    // Current state variables
    let activeDate = null;
    let syncInterval = null;

    // --- DOM ELEMENT REFERENCES ---
    const digestDateLabel = document.getElementById("digest-date-label");
    const digestHistoryList = document.getElementById("digest-history-list");
    const headerStatusBadge = document.getElementById("header-status-badge");
    const btnTriggerSync = document.getElementById("btn-trigger-sync");
    
    // News synthesis containers
    const editorialTrendTitle = document.getElementById("editorial-trend-title");
    const editorialTrendParagraphs = document.getElementById("editorial-trend-paragraphs");
    const newsGridContainer = document.getElementById("news-grid-container");
    const toolsTableBody = document.getElementById("tools-table-body");
    const changesTableBody = document.getElementById("changes-table-body");
    const workflowsGridContainer = document.getElementById("workflows-grid-container");
    const researchGridContainer = document.getElementById("research-grid-container");
    const marketGridContainer = document.getElementById("market-grid-container");
    const takesListContainer = document.getElementById("takes-list-container");
    const watchListContainer = document.getElementById("watch-list-container");

    // Sync HUD Modal Elements
    const syncHudOverlay = document.getElementById("sync-hud-overlay");
    const btnCloseHud = document.getElementById("btn-close-hud");
    const hudProgressBar = document.getElementById("hud-progress-bar");
    const hudStepText = document.getElementById("hud-step-text");
    const consoleLogsContainer = document.getElementById("console-logs-container");

    // Settings Modal Elements
    const settingsModalOverlay = document.getElementById("settings-modal-overlay");
    const btnSettingsToggle = document.getElementById("btn-settings-toggle");
    const btnCloseSettings = document.getElementById("btn-close-settings");
    const btnCancelSettings = document.getElementById("btn-cancel-settings");
    const btnSaveSettings = document.getElementById("btn-save-settings");
    const geminiKeyInput = document.getElementById("gemini-key-input");
    const keyStatusText = document.getElementById("key-status-text");

    // --- IMAGE & FAVICON HELPERS ---

    // Returns a Google favicon CDN URL for any web address
    function faviconFor(url) {
        try {
            const host = new URL(url).hostname;
            return `https://www.google.com/s2/favicons?domain=${host}&sz=32`;
        } catch (e) { return null; }
    }

    // Extracts a short domain label (e.g. "arxiv.org") from a URL
    function domainLabel(url) {
        try { return new URL(url).hostname.replace(/^www\./, ""); } catch (e) { return ""; }
    }

    // Card gradient presets — rotated by index for visual variety
    const THUMB_GRADS = ["thumb-g0","thumb-g1","thumb-g2","thumb-g3","thumb-g4"];

    // Asynchronously fetches OG image via backend proxy and applies it to a thumb element
    async function loadOgImage(url, thumbEl) {
        try {
            thumbEl.classList.add("thumb-loading");
            const res  = await fetch(`/api/og-image?url=${encodeURIComponent(url)}`);
            const data = await res.json();
            thumbEl.classList.remove("thumb-loading");
            if (data.image_url) {
                thumbEl.style.backgroundImage = `url(${data.image_url})`;
                thumbEl.style.backgroundSize  = "cover";
                thumbEl.style.backgroundPosition = "center";
                thumbEl.classList.add("has-og-image");
            }
        } catch (e) {
            thumbEl.classList.remove("thumb-loading");
        }
    }

    // --- UTILITY METHODS ---

    function formatDate(dateStr) {
        if (!dateStr) return "";
        const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
        const date = new Date(dateStr);
        return date.toLocaleDateString('en-US', options);
    }

    function setStatusIndicator(status) {
        let badgeHtml = "";
        switch (status) {
            case "idle":
                badgeHtml = `<span class="status-indicator status-idle"></span> <span class="status-text">System Idle</span>`;
                break;
            case "fetching":
                badgeHtml = `<span class="status-indicator status-fetching"></span> <span class="status-text">Syncing News...</span>`;
                break;
            case "analyzing":
                badgeHtml = `<span class="status-indicator status-analyzing"></span> <span class="status-text">Analyzing with Gemini...</span>`;
                break;
            case "complete":
                badgeHtml = `<span class="status-indicator status-complete"></span> <span class="status-text">Sync Complete</span>`;
                break;
            case "error":
                badgeHtml = `<span class="status-indicator status-error"></span> <span class="status-text">Sync Failed</span>`;
                break;
        }
        headerStatusBadge.innerHTML = badgeHtml;
    }

    // --- API CORE COMMUNICATIONS ---

    // Load available dates in sidebar
    async function loadDigestHistory(selectLatest = true) {
        try {
            const response = await fetch("/api/digests");
            if (!response.ok) throw new Error("Failed to load digest history.");
            const data = await response.json();
            
            digestHistoryList.innerHTML = "";
            
            if (data.dates.length === 0) {
                digestHistoryList.innerHTML = `<li class="loading-placeholder">No digests generated yet. Click 'Sync Latest News' to start!</li>`;
                // Show empty states
                showEmptyDashboard();
                return;
            }

            data.dates.forEach((date) => {
                const li = document.createElement("li");
                li.className = "digest-history-item";
                if (date === activeDate) li.classList.add("active");
                
                li.innerHTML = `
                    <span>${date}</span>
                    <i class="fa-solid fa-chevron-right item-icon"></i>
                `;
                li.addEventListener("click", () => {
                    document.querySelectorAll(".digest-history-item").forEach(item => item.classList.remove("active"));
                    li.classList.add("active");
                    loadDigest(date);
                });
                digestHistoryList.appendChild(li);
            });

            if (selectLatest && data.dates.length > 0) {
                // Select the first date (which is sorted descending -> latest)
                activeDate = data.dates[0];
                const firstItem = digestHistoryList.querySelector(".digest-history-item");
                if (firstItem) firstItem.classList.add("active");
                loadDigest(activeDate);
            }
        } catch (error) {
            console.error(error);
            digestHistoryList.innerHTML = `<li class="loading-placeholder text-danger">Error loading history</li>`;
        }
    }

    // Load detailed newsletter content for a specific date
    async function loadDigest(date) {
        try {
            activeDate = date;
            digestDateLabel.textContent = `Daily AI Digest — Loading...`;
            
            const response = await fetch(`/api/digests/${date}`);
            if (!response.ok) throw new Error(`Failed to load digest for date: ${date}`);
            const data = await response.json();
            
            renderDigest(data.content);
        } catch (error) {
            console.error(error);
            digestDateLabel.textContent = `Error Loading Digest`;
        }
    }

    // Render digest content onto structural DOM nodes
    function renderDigest(content) {
        // Update date titles
        digestDateLabel.textContent = `Daily AI Digest — ${formatDate(content.date)}`;
        
        // Render 0: Hero trend
        editorialTrendTitle.textContent = content.editorial_trend.title;
        editorialTrendParagraphs.innerHTML = content.editorial_trend.paragraphs
            .map(para => `<p>${para}</p>`)
            .join("");
            
        // Render 1: Biggest News Grid
        newsGridContainer.innerHTML = "";
        content.biggest_news.forEach((news, idx) => {
            const card = document.createElement("div");
            card.className = "news-card";

            const gradClass  = THUMB_GRADS[idx % THUMB_GRADS.length];
            const favicon    = faviconFor(news.link);
            const domain     = domainLabel(news.link);
            const faviconImg = favicon
                ? `<img src="${favicon}" onerror="this.style.display='none'" alt="">`
                : `<i class="fa-solid fa-globe" style="font-size:11px;opacity:0.5;"></i>`;

            const featuresHtml = news.key_features && news.key_features.length > 0
                ? `<ul class="news-features-list">
                    ${news.key_features.map(f => `<li><i class="fa-solid fa-caret-right"></i> ${f}</li>`).join("")}
                   </ul>`
                : "";

            card.innerHTML = `
                <div class="news-card-thumb ${gradClass}">
                    <i class="fa-regular fa-newspaper news-thumb-icon"></i>
                    <div class="news-thumb-source">
                        ${faviconImg}
                        <span>${domain}</span>
                    </div>
                </div>
                <div class="news-card-body">
                    <div class="news-card-header">
                        <h4 class="news-headline">${news.headline}</h4>
                        <span class="badge badge-source">TOP STORY</span>
                    </div>
                    <p class="news-summary">${news.summary}</p>
                    <div class="news-meta-block">
                        <strong>WHY IT MATTERS:</strong>
                        ${news.why_it_matters}
                    </div>
                    ${featuresHtml}
                    <div class="tldr-box">
                        <strong>TL;DR:</strong> ${news.tldr}
                    </div>
                    <div class="news-footer">
                        <div class="who-cares-badge">
                            <span>Impact:</span> ${news.who_should_care}
                        </div>
                        <a href="${news.link}" target="_blank" class="btn-link-action">
                            Read Source <i class="fa-solid fa-arrow-up-right-from-square"></i>
                        </a>
                    </div>
                </div>
            `;

            newsGridContainer.appendChild(card);
            if (news.link) loadOgImage(news.link, card.querySelector(".news-card-thumb"));
        });

        // Render 2: Discovered Tools Table
        toolsTableBody.innerHTML = "";
        content.discovered_tools.forEach((tool) => {
            const row     = document.createElement("tr");
            const favicon = faviconFor(tool.link);
            const favImg  = favicon
                ? `<img src="${favicon}" class="tool-favicon" onerror="this.style.display='none'" alt="">`
                : "";
            row.innerHTML = `
                <td class="tool-name-container">
                    <div class="tool-name-with-favicon">
                        ${favImg}
                        <span>${tool.tool}</span>
                    </div>
                </td>
                <td><span class="badge badge-category">${tool.category}</span></td>
                <td>${tool.what_it_does}</td>
                <td>${tool.why_it_matters}</td>
                <td><span class="pricing-tag">${tool.pricing}</span></td>
                <td>
                    <a href="${tool.link}" target="_blank" class="btn btn-secondary" style="width:auto;padding:6px 12px;font-size:12px;">
                        Explore <i class="fa-solid fa-external-link" style="font-size:10px;"></i>
                    </a>
                </td>
            `;
            toolsTableBody.appendChild(row);
        });

        // Render 3: Changes Table
        changesTableBody.innerHTML = "";
        content.what_changed.forEach((change) => {
            const row = document.createElement("tr");
            row.innerHTML = `
                <td class="tool-name-container">${change.tool_or_company}</td>
                <td><span style="color: var(--color-text-muted); text-decoration: line-through;">${change.yesterday}</span></td>
                <td><span style="color: var(--accent-cyan); font-weight: 500;"><i class="fa-solid fa-arrow-trend-up"></i> ${change.today}</span></td>
                <td>${change.why_it_matters}</td>
            `;
            changesTableBody.appendChild(row);
        });

        // Render 4: Trending Workflows
        workflowsGridContainer.innerHTML = "";
        content.trending_workflows.forEach((wf) => {
            const card = document.createElement("div");
            card.className = "workflow-card";
            
            // Map difficulty style class
            let diffClass = "diff-intermediate";
            if (wf.difficulty.toLowerCase().includes("beginner")) diffClass = "diff-beginner";
            if (wf.difficulty.toLowerCase().includes("advanced")) diffClass = "diff-advanced";
            
            const stepsHtml = wf.steps && wf.steps.length > 0
                ? `<ul class="workflow-steps-list">
                    ${wf.steps.map((step, idx) => `
                        <li><span class="step-num">${idx + 1}</span> ${step}</li>
                    `).join("")}
                   </ul>`
                : "";

            card.innerHTML = `
                <div class="workflow-header">
                    <h4>${wf.title}</h4>
                    <span class="difficulty-badge ${diffClass}">${wf.difficulty}</span>
                </div>
                <div class="workflow-field">
                    <strong>Problem Solved</strong>
                    ${wf.problem_solved}
                </div>
                <div class="workflow-field">
                    <strong>Workflow Execution Plan</strong>
                    ${stepsHtml}
                </div>
                <div class="workflow-field">
                    <strong>Business Value</strong>
                    ${wf.business_value}
                </div>
                <div class="workflow-field" style="margin-top: auto; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.03);">
                    <strong>Tools deployed</strong>
                    <span style="color: var(--accent-cyan); font-size: 12px; font-family: monospace;">${wf.tools_used}</span>
                </div>
            `;
            workflowsGridContainer.appendChild(card);
        });

        // Render 5: Open Source & Research
        researchGridContainer.innerHTML = "";
        content.open_source_research.forEach((item, idx) => {
            const card = document.createElement("div");
            card.className = "research-card";

            const badgeType  = item.category.toLowerCase().includes("paper") ? "badge-research" : "badge-repo";
            const gradClass  = THUMB_GRADS[(idx + 2) % THUMB_GRADS.length];
            const thumbIcon  = item.category.toLowerCase().includes("paper")
                ? "fa-graduation-cap" : "fa-code-branch";

            card.innerHTML = `
                <div class="research-card-thumb ${gradClass}">
                    <i class="fa-solid ${thumbIcon} research-thumb-icon"></i>
                </div>
                <div class="research-card-body">
                    <div class="research-header">
                        <h4 class="research-title">${item.title}</h4>
                        <span class="badge ${badgeType}">${item.category}</span>
                    </div>
                    <p class="news-summary">${item.summary}</p>
                    <div class="news-meta-block" style="border-color:var(--accent-cyan);margin-top:auto;">
                        <strong>IMPACT ANALYSIS:</strong>
                        ${item.why_it_matters}
                    </div>
                    <div style="text-align:right;padding-top:5px;">
                        <a href="${item.link}" target="_blank" class="btn-link-action" style="font-size:12px;">
                            Link <i class="fa-solid fa-chevron-right"></i>
                        </a>
                    </div>
                </div>
            `;
            researchGridContainer.appendChild(card);
            if (item.link) loadOgImage(item.link, card.querySelector(".research-card-thumb"));
        });

        // Render 6: Market Movements
        marketGridContainer.innerHTML = "";
        content.market_industry.forEach((item, idx) => {
            const card = document.createElement("div");
            card.className = "market-card";

            const gradClass  = THUMB_GRADS[(idx + 1) % THUMB_GRADS.length];
            const favicon    = faviconFor(item.link);
            const domain     = domainLabel(item.link);
            const faviconImg = favicon
                ? `<img src="${favicon}" onerror="this.style.display='none'" alt="">`
                : "";

            card.innerHTML = `
                <div class="market-card-thumb ${gradClass}">
                    <i class="fa-solid fa-chart-line market-thumb-icon"></i>
                    <div class="market-source-badge">
                        ${faviconImg}
                        <span>${domain}</span>
                    </div>
                </div>
                <div class="market-card-body">
                    <div class="market-meta">
                        <span class="badge badge-category" style="font-size:9px;padding:2px 6px;">${item.category}</span>
                        <a href="${item.link}" target="_blank" class="btn-link-action" style="font-size:11px;">
                            Source <i class="fa-solid fa-xs fa-arrow-up-right-from-square"></i>
                        </a>
                    </div>
                    <h4 class="market-headline">${item.headline}</h4>
                    <p class="news-summary">${item.summary}</p>
                </div>
            `;
            marketGridContainer.appendChild(card);
            if (item.link) loadOgImage(item.link, card.querySelector(".market-card-thumb"));
        });

        // Render 7: Quick Takes
        takesListContainer.innerHTML = "";
        content.quick_takes.forEach((take) => {
            const card = document.createElement("div");
            card.className = "take-card";
            
            let hypeClass = "hype-emerging";
            if (take.hype_level.toLowerCase().includes("underrated")) hypeClass = "hype-underrated";
            if (take.hype_level.toLowerCase().includes("overhyped")) hypeClass = "hype-overhyped";
            
            card.innerHTML = `
                <div class="take-header">
                    <span class="take-topic">${take.topic}</span>
                    <span class="hype-meter ${hypeClass}">${take.hype_level}</span>
                </div>
                <p class="take-opinion">${take.opinion}</p>
            `;
            takesListContainer.appendChild(card);
        });

        // Render 8: What to Watch Tomorrow
        watchListContainer.innerHTML = "";
        content.what_to_watch.forEach((watch) => {
            const li = document.createElement("li");
            li.className = "watch-item";
            li.innerHTML = `
                <div class="watch-title"><i class="fa-solid fa-circle-play"></i> ${watch.item}</div>
                <p class="watch-details">${watch.details}</p>
            `;
            watchListContainer.appendChild(li);
        });
    }

    function showEmptyDashboard() {
        digestDateLabel.textContent = "Daily AI Digest";
        editorialTrendTitle.textContent = "Welcome to your AI Digest Center";
        editorialTrendParagraphs.innerHTML = `
            <p>Our database contains no intelligence digests yet. Trigger your first daily search sync using the button in the left sidebar!</p>
            <p>The backend will crawl active research papers, Hacker News discussions, Reddit sentiment, GitHub repositories, and AI lab blogs immediately.</p>
        `;
        newsGridContainer.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--color-text-muted);">Sync required.</div>`;
        toolsTableBody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--color-text-muted);">Sync required.</td></tr>`;
        changesTableBody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--color-text-muted);">Sync required.</td></tr>`;
        workflowsGridContainer.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--color-text-muted);">Sync required.</div>`;
        researchGridContainer.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--color-text-muted);">Sync required.</div>`;
        marketGridContainer.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--color-text-muted);">Sync required.</div>`;
        takesListContainer.innerHTML = `<div style="text-align: center; padding: 20px; color: var(--color-text-muted);">Sync required.</div>`;
        watchListContainer.innerHTML = `<li style="text-align: center; padding: 20px; color: var(--color-text-muted);">Sync required.</li>`;
    }

    // --- SYNC HUB MECHANISM (POLLING BACKGROUND JOB STATUS) ---

    async function triggerSync() {
        if (syncInterval) return;
        
        try {
            // Set status to starting
            setStatusIndicator("fetching");
            
            // Pop open overlay HUD
            syncHudOverlay.classList.remove("hidden");
            consoleLogsContainer.innerHTML = `<div>[INIT] Establishing API route connection...</div>`;
            hudProgressBar.style.width = "5%";
            hudStepText.textContent = "Connecting to crawler engine...";
            
            const response = await fetch("/api/trigger", { method: "POST" });
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || "API trigger failed.");
            }
            
            // Setup polling interval every 800 milliseconds
            syncInterval = setInterval(pollSyncStatus, 800);
            
        } catch (error) {
            console.error(error);
            setStatusIndicator("error");
            hudStepText.textContent = `Error: ${error.message}`;
            consoleLogsContainer.innerHTML += `<div class="log-error">[CRITICAL] Trigger failed: ${error.message}</div>`;
        }
    }

    async function pollSyncStatus() {
        try {
            const response = await fetch("/api/status");
            if (!response.ok) throw new Error("Sync status query failed.");
            const data = await response.json();
            
            setStatusIndicator(data.status);
            
            // Render logs into console
            consoleLogsContainer.innerHTML = data.logs
                .map(log => {
                    const isErr = log.includes("ERROR") || log.includes("Failed");
                    return `<div class="${isErr ? 'log-error' : ''}">${log}</div>`;
                })
                .join("");
                
            // Auto scroll console
            consoleLogsContainer.scrollTop = consoleLogsContainer.scrollHeight;
            
            // Handle current step label
            hudStepText.textContent = data.current_step;
            
            // Dynamic Progress Bar width heuristics based on logs content
            const logsLength = data.logs.length;
            let progressWidth = 10;
            
            if (data.status === "fetching") {
                progressWidth = 10 + Math.min(logsLength * 4, 45); // up to 55%
            } else if (data.status === "analyzing") {
                progressWidth = 60 + Math.min((logsLength - 10) * 3, 30); // up to 90%
            } else if (data.status === "complete") {
                progressWidth = 100;
            } else if (data.status === "error") {
                progressWidth = 100;
                hudProgressBar.style.backgroundColor = "var(--accent-red)";
                hudProgressBar.style.boxShadow = "0 0 10px var(--accent-red)";
            }
            
            hudProgressBar.style.width = `${progressWidth}%`;
            
            // Terminate polling conditions
            if (data.status === "complete") {
                clearInterval(syncInterval);
                syncInterval = null;
                hudStepText.textContent = "Compilation successful! Loading dashboard...";
                
                // Keep overlay visible briefly for satisfaction, then close
                setTimeout(() => {
                    syncHudOverlay.classList.add("hidden");
                    loadDigestHistory(true); // reload list and select latest
                }, 1500);
            } else if (data.status === "error") {
                clearInterval(syncInterval);
                syncInterval = null;
                hudStepText.textContent = `Failed: ${data.error_message}`;
            }
            
        } catch (error) {
            console.error(error);
            clearInterval(syncInterval);
            syncInterval = null;
            setStatusIndicator("error");
            hudStepText.textContent = `Polling Connection Error`;
        }
    }

    // --- CONFIGURATION SETTINGS MODAL ---

    async function loadSettings() {
        try {
            const response = await fetch("/api/settings");
            if (!response.ok) throw new Error("Failed to load settings.");
            const data = await response.json();
            
            if (data.has_key) {
                geminiKeyInput.placeholder = `Configured: ${data.masked_key}`;
                keyStatusText.textContent = "● Gemini API Active";
                keyStatusText.style.color = "var(--accent-green)";
            } else {
                geminiKeyInput.placeholder = "Paste your API key...";
                keyStatusText.textContent = "○ Offline Fallback Active (Empty key)";
                keyStatusText.style.color = "var(--color-text-muted)";
            }
        } catch (error) {
            console.error(error);
            keyStatusText.textContent = "Error loading settings state";
            keyStatusText.style.color = "var(--accent-red)";
        }
    }

    async function saveSettings() {
        const apiKey = geminiKeyInput.value.trim();
        if (!apiKey) {
            keyStatusText.textContent = "Key input is empty.";
            keyStatusText.style.color = "var(--accent-red)";
            return;
        }

        try {
            keyStatusText.textContent = "Saving credentials...";
            keyStatusText.style.color = "var(--accent-cyan)";
            
            const response = await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ gemini_api_key: apiKey })
            });

            if (!response.ok) throw new Error("Save request rejected.");
            
            geminiKeyInput.value = ""; // clear entry
            await loadSettings(); // reload configurations
            
            // Pop close settings modal
            setTimeout(() => {
                settingsModalOverlay.classList.add("hidden");
            }, 800);

        } catch (error) {
            console.error(error);
            keyStatusText.textContent = "Failed to store credentials.";
            keyStatusText.style.color = "var(--accent-red)";
        }
    }

    // --- EVENT BINDERS ---

    // Toggle settings modal — handler defined below after tab logic is wired
    btnCloseSettings.addEventListener("click", () => settingsModalOverlay.classList.add("hidden"));
    btnCancelSettings.addEventListener("click", () => settingsModalOverlay.classList.add("hidden"));
    
    // Save settings click
    btnSaveSettings.addEventListener("click", saveSettings);
    
    // Close sync HUD
    btnCloseHud.addEventListener("click", () => {
        if (syncInterval) {
            // Do not allow closing if it is actively compiling
            alert("Aggregation runs cannot be closed while crawling is active.");
            return;
        }
        syncHudOverlay.classList.add("hidden");
    });

    // Trigger crawler sync
    btnTriggerSync.addEventListener("click", triggerSync);

    // --- SETTINGS MODAL TAB SWITCHING ---

    const settingsTabBtns  = document.querySelectorAll(".settings-tab-btn");
    const settingsTabPanels = document.querySelectorAll(".settings-tab-panel");

    settingsTabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const target = btn.dataset.tab;
            settingsTabBtns.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            settingsTabPanels.forEach(panel => {
                const isTarget = panel.id === `settings-panel-${target}`;
                panel.classList.toggle("hidden", !isTarget);
            });
            if (target === "email") {
                loadEmailSettings();
                loadSubscribers();
            }
        });
    });

    // Close buttons that appear in the email tab
    const btnCancelSettingsEmail = document.getElementById("btn-cancel-settings-email");
    if (btnCancelSettingsEmail) {
        btnCancelSettingsEmail.addEventListener("click", () => settingsModalOverlay.classList.add("hidden"));
    }

    // --- SMTP / EMAIL SETTINGS ---

    const smtpHostInput    = document.getElementById("smtp-host-input");
    const smtpPortInput    = document.getElementById("smtp-port-input");
    const smtpUserInput    = document.getElementById("smtp-user-input");
    const smtpPassInput    = document.getElementById("smtp-pass-input");
    const smtpFromNameInput = document.getElementById("smtp-from-name-input");
    const emailEnabledToggle = document.getElementById("email-enabled-toggle");
    const emailEnabledLabel  = document.getElementById("email-enabled-label");
    const smtpStatusText   = document.getElementById("smtp-status-text");
    const btnSaveSmtp      = document.getElementById("btn-save-smtp");

    async function loadEmailSettings() {
        try {
            const res  = await fetch("/api/settings/email");
            if (!res.ok) throw new Error("Failed to load email settings.");
            const data = await res.json();

            smtpHostInput.value     = data.smtp_host || "";
            smtpPortInput.value     = data.smtp_port || 587;
            smtpUserInput.value     = data.smtp_user || "";
            smtpFromNameInput.value = data.from_name || "Daily AI Digest";
            smtpPassInput.placeholder = data.has_password ? "Password saved — enter new to replace" : "Enter password";
            emailEnabledToggle.checked = data.enabled;
            emailEnabledLabel.textContent = data.enabled
                ? "Auto-send enabled (dispatches at 7 AM daily)"
                : "Auto-send disabled";

            smtpStatusText.textContent = data.smtp_host
                ? `● Configured: ${data.smtp_host}:${data.smtp_port}`
                : "○ Not configured";
            smtpStatusText.style.color = data.smtp_host ? "var(--accent-green)" : "var(--color-text-muted)";
        } catch (err) {
            console.error(err);
            smtpStatusText.textContent = "Error loading SMTP settings.";
            smtpStatusText.style.color = "var(--accent-red)";
        }
    }

    emailEnabledToggle && emailEnabledToggle.addEventListener("change", () => {
        emailEnabledLabel.textContent = emailEnabledToggle.checked
            ? "Auto-send enabled (dispatches at 7 AM daily)"
            : "Auto-send disabled";
    });

    async function saveEmailSettings() {
        smtpStatusText.textContent = "Saving…";
        smtpStatusText.style.color = "var(--accent-cyan)";
        try {
            const payload = {
                smtp_host:     smtpHostInput.value.trim(),
                smtp_port:     parseInt(smtpPortInput.value) || 587,
                smtp_user:     smtpUserInput.value.trim(),
                smtp_password: smtpPassInput.value,
                from_name:     smtpFromNameInput.value.trim() || "Daily AI Digest",
                enabled:       emailEnabledToggle.checked,
            };
            const res = await fetch("/api/settings/email", {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify(payload),
            });
            if (!res.ok) throw new Error((await res.json()).detail || "Save failed.");
            smtpPassInput.value = "";
            await loadEmailSettings();
        } catch (err) {
            smtpStatusText.textContent = `Error: ${err.message}`;
            smtpStatusText.style.color = "var(--accent-red)";
        }
    }

    btnSaveSmtp && btnSaveSmtp.addEventListener("click", saveEmailSettings);

    // --- SUBSCRIBER MANAGEMENT ---

    const subscriberList  = document.getElementById("subscriber-list");
    const subEmailInput   = document.getElementById("sub-email-input");
    const subNameInput    = document.getElementById("sub-name-input");
    const btnAddSubscriber = document.getElementById("btn-add-subscriber");
    const subStatusText   = document.getElementById("sub-status-text");

    async function loadSubscribers() {
        subscriberList.innerHTML = '<li class="subscriber-empty">Loading...</li>';
        try {
            const res  = await fetch("/api/subscribers");
            if (!res.ok) throw new Error("Failed to load subscribers.");
            const data = await res.json();

            subscriberList.innerHTML = "";
            if (data.subscribers.length === 0) {
                subscriberList.innerHTML = '<li class="subscriber-empty">No subscribers yet. Add one above.</li>';
                return;
            }
            data.subscribers.forEach(sub => {
                const li = document.createElement("li");
                li.className = "subscriber-item";
                li.dataset.email = sub.email;
                li.innerHTML = `
                    <div class="subscriber-info">
                        <span class="subscriber-email">${sub.email}</span>
                        ${sub.name ? `<span class="subscriber-name">${sub.name}</span>` : ""}
                    </div>
                    <button class="btn-remove-subscriber" data-email="${sub.email}">
                        <i class="fa-solid fa-xmark"></i> Remove
                    </button>
                `;
                subscriberList.appendChild(li);
            });

            // Bind remove buttons
            subscriberList.querySelectorAll(".btn-remove-subscriber").forEach(btn => {
                btn.addEventListener("click", () => removeSubscriber(btn.dataset.email));
            });
        } catch (err) {
            subscriberList.innerHTML = `<li class="subscriber-empty" style="color:var(--accent-red);">Error: ${err.message}</li>`;
        }
    }

    async function addSubscriber() {
        const email = subEmailInput.value.trim();
        const name  = subNameInput.value.trim();
        if (!email) return;

        subStatusText.textContent = "Adding...";
        subStatusText.style.color = "var(--accent-cyan)";
        try {
            const res = await fetch("/api/subscribers", {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ email, name }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Failed to add subscriber.");
            subEmailInput.value = "";
            subNameInput.value  = "";
            subStatusText.textContent = `✓ ${email} added.`;
            subStatusText.style.color = "var(--accent-green)";
            await loadSubscribers();
        } catch (err) {
            subStatusText.textContent = `Error: ${err.message}`;
            subStatusText.style.color = "var(--accent-red)";
        }
    }

    async function removeSubscriber(email) {
        try {
            const res = await fetch(`/api/subscribers/${encodeURIComponent(email)}`, { method: "DELETE" });
            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || "Remove failed.");
            }
            subStatusText.textContent = `✓ ${email} removed.`;
            subStatusText.style.color = "var(--accent-green)";
            await loadSubscribers();
        } catch (err) {
            subStatusText.textContent = `Error: ${err.message}`;
            subStatusText.style.color = "var(--accent-red)";
        }
    }

    btnAddSubscriber && btnAddSubscriber.addEventListener("click", addSubscriber);
    subEmailInput    && subEmailInput.addEventListener("keydown", e => {
        if (e.key === "Enter") addSubscriber();
    });

    // --- TEST EMAIL SEND ---

    const testEmailInput  = document.getElementById("test-email-input");
    const btnSendTest     = document.getElementById("btn-send-test");
    const testStatusText  = document.getElementById("test-status-text");

    async function sendTestEmail() {
        const to = testEmailInput.value.trim();
        if (!to) return;

        testStatusText.textContent = "Sending test email…";
        testStatusText.style.color = "var(--accent-cyan)";
        try {
            const res = await fetch("/api/email/test", {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ to }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Send failed.");
            testStatusText.textContent = `✓ Test sent to ${to} successfully.`;
            testStatusText.style.color = "var(--accent-green)";
        } catch (err) {
            testStatusText.textContent = `Error: ${err.message}`;
            testStatusText.style.color = "var(--accent-red)";
        }
    }

    btnSendTest && btnSendTest.addEventListener("click", sendTestEmail);

    // Reset tab to AI Model whenever the settings modal reopens
    const originalSettingsToggleHandler = () => {
        settingsModalOverlay.classList.remove("hidden");
        // Switch back to first tab
        settingsTabBtns.forEach((b, i) => b.classList.toggle("active", i === 0));
        settingsTabPanels.forEach((p, i) => p.classList.toggle("hidden", i !== 0));
        loadSettings();
    };
    btnSettingsToggle.addEventListener("click", originalSettingsToggleHandler);

    // --- RUN INITIALIZERS ON STARTUP ---
    loadDigestHistory(true);
    
    // Check initial status in case background scheduler is running
    fetch("/api/status")
        .then(res => res.json())
        .then(data => {
            setStatusIndicator(data.status);
            if (data.status === "fetching" || data.status === "analyzing") {
                // If it was already active on load, pop open HUD and continue polling!
                syncHudOverlay.classList.remove("hidden");
                syncInterval = setInterval(pollSyncStatus, 800);
            }
        });
});
