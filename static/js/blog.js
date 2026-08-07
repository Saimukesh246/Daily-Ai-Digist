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

    /** Returns the URL unchanged if it's http(s), otherwise "#" — blocks
     *  javascript:/data: URIs that scraped content could otherwise smuggle into href/src. */
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

    // ── Theme toggle ──────────────────────────────────────────────────────────
    (function initTheme() {
        const btn = document.getElementById("theme-toggle");
        const icon = document.getElementById("theme-icon");
        function applyIcon() {
            const theme = document.documentElement.getAttribute("data-theme");
            icon.className = theme === "dark" ? "fa-solid fa-sun" : "fa-solid fa-moon";
        }
        applyIcon();
        btn.addEventListener("click", () => {
            const current = document.documentElement.getAttribute("data-theme");
            const next = current === "dark" ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", next);
            localStorage.setItem(THEME_KEY, next);
            applyIcon();
        });
    })();

    // ── Mobile nav ────────────────────────────────────────────────────────────
    (function initMobileNav() {
        const toggle = document.getElementById("nav-toggle");
        const nav = document.getElementById("mobile-nav");
        toggle.addEventListener("click", () => {
            const isHidden = nav.hasAttribute("hidden");
            if (isHidden) { nav.removeAttribute("hidden"); toggle.setAttribute("aria-expanded", "true"); }
            else { nav.setAttribute("hidden", ""); toggle.setAttribute("aria-expanded", "false"); }
        });
        nav.querySelectorAll("a").forEach(a => a.addEventListener("click", () => {
            nav.setAttribute("hidden", "");
            toggle.setAttribute("aria-expanded", "false");
        }));
    })();

    // ── Scroll progress + header shadow ─────────────────────────────────────────
    (function initScroll() {
        const progressBar = document.getElementById("progress-bar");
        const header = document.getElementById("site-header");
        function onScroll() {
            const y = window.scrollY;
            const h = document.documentElement.scrollHeight - window.innerHeight;
            progressBar.style.width = (h > 0 ? Math.min(100, (y / h) * 100) : 0) + "%";
            header.classList.toggle("scrolled", y > 30);
        }
        window.addEventListener("scroll", onScroll, { passive: true });
        onScroll();
    })();

    // ── Section reveal-on-scroll ─────────────────────────────────────────────
    function initReveal() {
        const sections = document.querySelectorAll("[data-reveal]");
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(en => {
                if (en.isIntersecting) {
                    en.target.classList.add("revealed");
                    observer.unobserve(en.target);
                }
            });
        }, { threshold: 0.1 });
        sections.forEach(el => observer.observe(el));
        // Anything already in view (or that never intersects, e.g. very short pages)
        // still reveals after a beat, matching the reference design's behavior.
        setTimeout(() => sections.forEach(el => el.classList.add("revealed")), 1200);
    }

    // ── Thumbnail loading (og:image, with graceful placeholder fallback) ────────
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

    // ── Rendering ─────────────────────────────────────────────────────────────
    function renderTopStories(items) {
        const grid = document.getElementById("top-grid");
        if (!items.length) return;
        const lead = items[0];
        const sidebar = items.slice(1, 3);

        const thumbs = [];
        let html = `
        <div>
            <a href="${safeUrl(lead.link)}" target="_blank" rel="noopener noreferrer">
                <div class="thumb lead-thumb" data-link="${escapeHtml(lead.link)}"></div>
            </a>
            <div class="breaking-badge">Breaking</div>
            <a class="lead-headline" href="${safeUrl(lead.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(lead.headline)}</a>
            <div class="lead-dek">${escapeHtml(lead.summary || "")}</div>
        </div>
        <div class="top-sidebar">`;

        sidebar.forEach(item => {
            html += `
            <div class="top-sidebar-item">
                <a href="${safeUrl(item.link)}" target="_blank" rel="noopener noreferrer" style="flex-shrink:0">
                    <div class="thumb top-sidebar-thumb" data-link="${escapeHtml(item.link)}"></div>
                </a>
                <div>
                    <a class="top-sidebar-headline" href="${safeUrl(item.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.headline)}</a>
                    <div class="top-sidebar-dek">${escapeHtml(item.summary || "")}</div>
                </div>
            </div>`;
        });

        html += `<div class="top-meta">${escapeHtml(formatDate(window.__digestDate))} · ${estimateReadTime(lead.summary)}</div></div>`;
        grid.innerHTML = html;
        document.getElementById("top-stories").hidden = false;

        grid.querySelectorAll(".thumb[data-link]").forEach(el => loadThumbnail(el, el.dataset.link));
    }

    function renderResearch(items) {
        if (!items.length) return;
        const grid = document.getElementById("research-grid");
        let html = "";
        items.slice(0, 4).forEach(item => {
            html += `
            <div>
                <a href="${safeUrl(item.link)}" target="_blank" rel="noopener noreferrer">
                    <div class="thumb research-thumb" data-link="${escapeHtml(item.link)}"></div>
                </a>
                <a class="research-title" href="${safeUrl(item.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a>
                <div class="category-tag">${escapeHtml(item.category || "")}</div>
            </div>`;
        });
        grid.innerHTML = html;
        document.getElementById("research").hidden = false;
        grid.querySelectorAll(".thumb[data-link]").forEach(el => loadThumbnail(el, el.dataset.link));
    }

    function renderList(containerId, items, titleKey, dekKey) {
        const container = document.getElementById(containerId);
        let html = "";
        items.forEach(item => {
            html += `
            <div class="tm-item">
                <a class="tm-title" href="${safeUrl(item.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item[titleKey])}</a>
                <div class="tm-dek">${escapeHtml(item[dekKey] || "")}</div>
            </div>`;
        });
        container.innerHTML = html;
    }

    function renderToolsMarket(tools, market) {
        if (!tools.length && !market.length) return;
        if (tools.length) {
            renderList("tools-list", tools.slice(0, 6), "tool", "what_it_does");
            document.getElementById("tools-col").hidden = false;
        }
        if (market.length) {
            renderList("market-list", market.slice(0, 4), "headline", "summary");
            document.getElementById("market-col").hidden = false;
        }
        document.getElementById("tools-market").hidden = false;
    }

    function renderTicker(digest) {
        const content = digest.content || {};
        const items = [];
        items.push({ text: `Digest last updated ${formatDate(digest.date)}` });
        if ((content.biggest_news || []).length) items.push({ text: `${content.biggest_news.length} top stories today` });
        if ((content.discovered_tools || []).length) items.push({ text: `${content.discovered_tools.length} new tools discovered` });
        if ((content.open_source_research || []).length) items.push({ text: `${content.open_source_research.length} research items indexed` });
        if ((content.market_industry || []).length) items.push({ text: `${content.market_industry.length} market movements tracked` });

        if (!items.length) return;
        const ticker = document.getElementById("ticker");
        const track = document.getElementById("ticker-track");
        // Duplicated once so the marquee loop (translateX(-50%)) is seamless.
        const renderItems = (list) => list.map(t => `<div class="ticker-item">${escapeHtml(t.text)}</div>`).join("");
        track.innerHTML = renderItems(items) + renderItems(items);
        ticker.hidden = false;
    }

    function showState(state) {
        ["state-loading", "state-empty", "state-error", "content"].forEach(id => {
            document.getElementById(id).hidden = (id !== state);
        });
    }

    // ── Boot ──────────────────────────────────────────────────────────────────
    document.getElementById("footer-year").textContent = new Date().getFullYear();

    fetch("/api/public/digest")
        .then(res => {
            if (res.status === 404) { showState("state-empty"); return null; }
            if (!res.ok) throw new Error("Request failed");
            return res.json();
        })
        .then(digest => {
            if (!digest) return;
            window.__digestDate = digest.date;
            const content = digest.content || {};
            const kicker = document.getElementById("kicker");
            kicker.textContent = `Daily Intelligence Briefing — ${formatDate(digest.date)}`;

            renderTopStories(content.biggest_news || []);
            renderResearch(content.open_source_research || []);
            renderToolsMarket(content.discovered_tools || [], content.market_industry || []);
            renderTicker(digest);

            showState("content");
            initReveal();
        })
        .catch(() => showState("state-error"));

    // ── Subscribe form ────────────────────────────────────────────────────────
    document.getElementById("subscribe-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const input = document.getElementById("subscribe-email");
        const btn = e.target.querySelector("button");
        const msg = document.getElementById("subscribe-msg");
        const email = input.value.trim();
        msg.textContent = "";
        msg.className = "subscribe-msg";
        btn.disabled = true;
        try {
            const res = await fetch("/api/public/subscribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email }),
            });
            const data = await res.json();
            if (!res.ok) {
                msg.textContent = data.detail || "Something went wrong.";
                msg.classList.add("error");
            } else {
                msg.textContent = "You're subscribed. Check your inbox for confirmation.";
                msg.classList.add("success");
                input.value = "";
            }
        } catch (err) {
            msg.textContent = "Network error — please try again.";
            msg.classList.add("error");
        } finally {
            btn.disabled = false;
        }
    });
})();
