(function () {
    "use strict";

    const THEME_KEY = "aidigest_blog_theme";

    function escapeHtml(value) {
        if (value === null || value === undefined) return "";
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function safeUrl(url) {
        try {
            const u = new URL(url, window.location.href);
            if (u.protocol === "http:" || u.protocol === "https:") return u.href;
        } catch (e) { /* fall through */ }
        return "#";
    }

    function formatDate(dateStr) {
        try {
            const d = new Date(dateStr + "T00:00:00");
            return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
        } catch (e) { return dateStr; }
    }

    function estimateReadTime(text) {
        const words = (text || "").split(/\s+/).filter(Boolean).length;
        return Math.max(1, Math.round(words / 200)) + " min read";
    }

    function articleUrl(date, section, index) {
        return `/article.html?date=${encodeURIComponent(date)}&section=${encodeURIComponent(section)}&index=${index}`;
    }

    // ── Theme toggle (same key/behavior as blog.js, so preference carries over) ──
    function setTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        localStorage.setItem(THEME_KEY, theme);
        const icon = document.getElementById("theme-icon");
        if (icon) icon.className = theme === "dark" ? "fa-solid fa-sun" : "fa-solid fa-moon";
        document.querySelectorAll("#settings-theme-control .segmented-btn").forEach(btn => {
            btn.classList.toggle("active", btn.dataset.themeChoice === theme);
        });
    }

    (function initTheme() {
        const btn = document.getElementById("theme-toggle");
        setTheme(document.documentElement.getAttribute("data-theme") || "light");
        btn.addEventListener("click", () => {
            const current = document.documentElement.getAttribute("data-theme");
            setTheme(current === "dark" ? "light" : "dark");
        });
    })();

    // ── Text-size preference (same key as blog.js, so preference carries over) ──
    const FONT_SIZE_KEY = "aidigest_blog_font_size";

    function setFontSize(size) {
        document.documentElement.setAttribute("data-font-size", size);
        localStorage.setItem(FONT_SIZE_KEY, size);
        document.querySelectorAll("#settings-fontsize-control .segmented-btn").forEach(btn => {
            btn.classList.toggle("active", btn.dataset.fontsizeChoice === size);
        });
    }

    (function initFontSize() {
        setFontSize(document.documentElement.getAttribute("data-font-size") || "md");
    })();

    // ── Settings drawer ─────────────────────────────────────────────────────
    (function initSettingsDrawer() {
        const toggleBtn = document.getElementById("settings-toggle-btn");
        const backdrop = document.getElementById("settings-drawer-backdrop");
        const drawer = document.getElementById("settings-drawer");
        const closeBtn = document.getElementById("settings-close-btn");

        if (!toggleBtn || !drawer) return;

        function openDrawer() {
            drawer.removeAttribute("hidden");
            if (backdrop) backdrop.removeAttribute("hidden");
        }

        function closeDrawer() {
            drawer.setAttribute("hidden", "");
            if (backdrop) backdrop.setAttribute("hidden", "");
        }

        toggleBtn.addEventListener("click", openDrawer);
        if (closeBtn) closeBtn.addEventListener("click", closeDrawer);
        if (backdrop) backdrop.addEventListener("click", closeDrawer);

        document.querySelectorAll("#settings-theme-control .segmented-btn").forEach(btn => {
            btn.addEventListener("click", () => setTheme(btn.dataset.themeChoice));
        });
        document.querySelectorAll("#settings-fontsize-control .segmented-btn").forEach(btn => {
            btn.addEventListener("click", () => setFontSize(btn.dataset.fontsizeChoice));
        });
    })();

    function loadThumbnail(el, link) {
        if (!link) return;
        fetch(`/api/public/og-image?url=${encodeURIComponent(link)}`)
            .then(res => res.ok ? res.json() : null)
            .then(data => {
                if (data && data.image_url) {
                    el.style.backgroundImage = `url(${safeUrl(data.image_url)})`;
                    el.classList.add("has-image");
                }
            })
            .catch(() => { /* keep placeholder */ });
    }

    /** Builds the body content for each section type from real structured
     *  digest fields — no invented prose, just the data we actually have,
     *  laid out with the same editorial typographic treatment (drop cap,
     *  pull-quote) as the rest of the site. */
    function buildBody(section, item) {
        let html = "";
        const lead = item.why_it_matters || "";
        if (lead) html += `<p class="drop-cap">${escapeHtml(lead)}</p>`;

        if (section === "biggest_news") {
            if ((item.key_features || []).length) {
                html += `<h2>Key Features</h2><ul>${item.key_features.map(f => `<li>${escapeHtml(f)}</li>`).join("")}</ul>`;
            }
            if (item.real_world_impact) {
                html += `<h2>Real-World Impact</h2><p>${escapeHtml(item.real_world_impact)}</p>`;
            }
            if (item.who_should_care) {
                html += `<h2>Who Should Care</h2><p>${escapeHtml(item.who_should_care)}</p>`;
            }
        } else if (section === "discovered_tools") {
            if (item.pricing) {
                html += `<h2>Pricing</h2><p>${escapeHtml(item.pricing)}</p>`;
            }
        }
        return html;
    }

    function renderRelated(items) {
        const section = document.getElementById("related-section");
        const grid = document.getElementById("related-grid");
        if (!items.length) return;
        let html = "";
        items.forEach(r => {
            const href = articleUrl(window.__articleDate, r.section, r.index);
            html += `
            <div>
                <a href="${href}">
                    <div class="thumb research-thumb" data-link="${escapeHtml(r.link)}"></div>
                </a>
                <a class="research-title" href="${href}">${escapeHtml(r.title)}</a>
                <div class="category-tag">${escapeHtml(r.label)}</div>
            </div>`;
        });
        grid.innerHTML = html;
        section.hidden = false;
        grid.querySelectorAll(".thumb[data-link]").forEach(el => loadThumbnail(el, el.dataset.link));
    }

    function showState(state) {
        ["state-loading", "state-error", "article-content"].forEach(id => {
            document.getElementById(id).hidden = (id !== state);
        });
    }

    // ── Boot ──────────────────────────────────────────────────────────────────
    document.getElementById("footer-year").textContent = new Date().getFullYear();

    const params = new URLSearchParams(window.location.search);
    const date = params.get("date");
    const section = params.get("section");
    const index = parseInt(params.get("index"), 10);

    if (!date || !section || Number.isNaN(index)) {
        showState("state-error");
    } else {
        window.__articleDate = date;
        fetch(`/api/public/article?date=${encodeURIComponent(date)}&section=${encodeURIComponent(section)}&index=${index}`)
            .then(res => {
                if (!res.ok) throw new Error("Not found");
                return res.json();
            })
            .then(data => {
                const item = data.item;
                const titleKey = { biggest_news: "headline", discovered_tools: "tool", open_source_research: "title", market_industry: "headline" }[section];
                const dekKey = { biggest_news: "summary", discovered_tools: "what_it_does", open_source_research: "summary", market_industry: "summary" }[section];
                const title = item[titleKey] || "Untitled";
                const dek = item[dekKey] || "";

                document.title = `${title} — AI Digest`;
                document.getElementById("article-kicker").textContent = data.label;
                document.getElementById("article-headline").textContent = title;
                document.getElementById("article-dek").textContent = dek;
                document.getElementById("article-meta").innerHTML =
                    `<span>Editorial Desk</span><span>·</span><span>${escapeHtml(formatDate(data.date))}</span><span>·</span><span>${escapeHtml(estimateReadTime(dek + " " + (item.why_it_matters || "")))}</span>`;

                const hero = document.getElementById("article-hero");
                if (item.link) loadThumbnail(hero, item.link);

                document.getElementById("article-body").innerHTML = buildBody(section, item);

                if (item.link) {
                    document.getElementById("article-source-link").href = safeUrl(item.link);
                    document.getElementById("article-source").hidden = false;
                }

                renderRelated(data.related || []);
                showState("article-content");
            })
            .catch(() => showState("state-error"));
    }
})();
