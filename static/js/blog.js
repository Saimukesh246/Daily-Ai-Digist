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

    function articleUrl(section, index) {
        return `/article.html?date=${encodeURIComponent(window.__digestDate)}&section=${encodeURIComponent(section)}&index=${index}`;
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
        if (!toggle || !nav) return;
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

        let html = `
        <div>
            <a href="${articleUrl("biggest_news", 0)}">
                <div class="thumb lead-thumb" data-link="${escapeHtml(lead.link)}"></div>
            </a>
            <div class="breaking-badge">Breaking</div>
            <a class="lead-headline" href="${articleUrl("biggest_news", 0)}">${escapeHtml(lead.headline)}</a>
            <div class="lead-dek">${escapeHtml(lead.summary || "")}</div>
        </div>
        <div class="top-sidebar">`;

        sidebar.forEach((item, i) => {
            const href = articleUrl("biggest_news", i + 1);
            html += `
            <div class="top-sidebar-item">
                <a href="${href}" style="flex-shrink:0">
                    <div class="thumb top-sidebar-thumb" data-link="${escapeHtml(item.link)}"></div>
                </a>
                <div>
                    <a class="top-sidebar-headline" href="${href}">${escapeHtml(item.headline)}</a>
                    <div class="top-sidebar-dek">${escapeHtml(item.summary || "")}</div>
                </div>
            </div>`;
        });

        html += `<div class="top-meta">${escapeHtml(formatDate(window.__digestDate))} · ${estimateReadTime(lead.summary)}</div></div>`;
        grid.innerHTML = html;
        document.getElementById("top-stories").hidden = false;

        grid.querySelectorAll(".thumb[data-link]").forEach(el => loadThumbnail(el, el.dataset.link));
    }

    /** Shared renderer for image-card grids (Research, Tools) — same visual
     *  shape, different fields per section. */
    function renderCardGrid(sectionId, gridId, items, opts) {
        if (!items.length) return;
        const grid = document.getElementById(gridId);
        const linkKey = opts.linkKey || "link";
        let html = "";
        items.forEach((item, i) => {
            const thumbSrc = item[linkKey];
            const href = articleUrl(opts.section, i);
            html += `
            <div>
                <a href="${href}">
                    <div class="thumb research-thumb" data-link="${escapeHtml(thumbSrc)}"></div>
                </a>
                <a class="research-title" href="${href}">${escapeHtml(item[opts.titleKey])}</a>
                ${opts.dekKey ? `<div class="research-dek">${escapeHtml(item[opts.dekKey] || "")}</div>` : ""}
                ${opts.categoryKey ? `<div class="category-tag">${escapeHtml(item[opts.categoryKey] || "")}</div>` : ""}
                ${opts.extraKey ? `<div class="pricing-tag">${escapeHtml(item[opts.extraKey] || "")}</div>` : ""}
            </div>`;
        });
        grid.innerHTML = html;
        document.getElementById(sectionId).hidden = false;
        grid.querySelectorAll(".thumb[data-link]").forEach(el => loadThumbnail(el, el.dataset.link));
    }

    function renderMarket(items) {
        if (!items.length) return;
        const container = document.getElementById("market-list");
        let html = "";
        items.forEach((item, i) => {
            const href = articleUrl("market_industry", i);
            html += `
            <div class="tm-item">
                <a class="tm-title" href="${href}">${escapeHtml(item.headline)}</a>
                <div class="tm-dek">${escapeHtml(item.summary || "")}</div>
            </div>`;
        });
        container.innerHTML = html;
        document.getElementById("market").hidden = false;
    }

    function renderWhatChanged(items) {
        if (!items.length) return;
        const list = document.getElementById("changed-list");
        let html = "";
        items.forEach(item => {
            html += `
            <div class="changed-card">
                <h3>${escapeHtml(item.tool_or_company)}</h3>
                <div class="changed-row yesterday"><span class="changed-label">Yesterday</span><span>${escapeHtml(item.yesterday)}</span></div>
                <div class="changed-row today"><span class="changed-label">Today</span><span>${escapeHtml(item.today)}</span></div>
                <div class="changed-why">${escapeHtml(item.why_it_matters || "")}</div>
            </div>`;
        });
        list.innerHTML = html;
        document.getElementById("what-changed").hidden = false;
    }

    function renderWorkflows(items) {
        if (!items.length) return;
        const list = document.getElementById("workflow-list");
        let html = "";
        items.forEach(item => {
            const steps = (item.steps || []).map(s => `<li>${escapeHtml(s)}</li>`).join("");
            html += `
            <div class="workflow-card">
                <div class="workflow-head">
                    <h3>${escapeHtml(item.title)}</h3>
                    <div class="difficulty-tag">${escapeHtml(item.difficulty || "")}</div>
                </div>
                <div class="workflow-problem">${escapeHtml(item.problem_solved || "")}</div>
                <div class="workflow-tools">${escapeHtml(item.tools_used || "")}</div>
                <ol class="workflow-steps">${steps}</ol>
                <div class="workflow-value">${escapeHtml(item.business_value || "")}</div>
            </div>`;
        });
        list.innerHTML = html;
        document.getElementById("trending-workflows").hidden = false;
    }

    function renderQuickTakes(items) {
        if (!items.length) return;
        const grid = document.getElementById("quick-takes-grid");
        let html = "";
        items.forEach(item => {
            html += `
            <div class="quick-take-card">
                <div class="hype-tag">${escapeHtml(item.hype_level || "")}</div>
                <h3>${escapeHtml(item.topic)}</h3>
                <div class="quick-take-opinion">${escapeHtml(item.opinion || "")}</div>
            </div>`;
        });
        grid.innerHTML = html;
        document.getElementById("quick-takes").hidden = false;
    }

    function renderWhatToWatch(items) {
        if (!items.length) return;
        const list = document.getElementById("watch-list");
        let html = "";
        items.forEach(item => {
            html += `
            <div class="watch-item">
                <i class="fa-solid fa-arrow-trend-up watch-icon"></i>
                <div>
                    <h3>${escapeHtml(item.item)}</h3>
                    <div class="watch-details">${escapeHtml(item.details || "")}</div>
                </div>
            </div>`;
        });
        list.innerHTML = html;
        document.getElementById("what-to-watch").hidden = false;
    }

    function renderTicker(digest) {
        const content = digest.content || {};
        const items = [];
        items.push({ text: `Digest last updated ${formatDate(digest.date)}` });
        if ((content.biggest_news || []).length) items.push({ text: `${content.biggest_news.length} top stories today` });
        if ((content.discovered_tools || []).length) items.push({ text: `${content.discovered_tools.length} new tools discovered` });
        if ((content.open_source_research || []).length) items.push({ text: `${content.open_source_research.length} research items indexed` });
        if ((content.market_industry || []).length) items.push({ text: `${content.market_industry.length} market movements tracked` });
        if ((content.trending_workflows || []).length) items.push({ text: `${content.trending_workflows.length} trending workflows featured` });

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
            renderCardGrid("tools", "tools-grid", (content.discovered_tools || []).slice(0, 6),
                { section: "discovered_tools", titleKey: "tool", categoryKey: "category", dekKey: "what_it_does", extraKey: "pricing" });
            renderWhatChanged(content.what_changed || []);
            renderWorkflows(content.trending_workflows || []);
            renderCardGrid("research", "research-grid", (content.open_source_research || []).slice(0, 4),
                { section: "open_source_research", titleKey: "title", categoryKey: "category" });
            renderMarket(content.market_industry || []);
            renderQuickTakes(content.quick_takes || []);
            renderWhatToWatch(content.what_to_watch || []);
            renderTicker(digest);

            showState("content");
            initReveal();
            initInfiniteTimeline();
            initRawArticleStream();
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

    // ── Infinite Timeline (Past Daily Briefings) ────────────────────────────
    function initInfiniteTimeline() {
        const sentinel = document.getElementById("timeline-sentinel");
        const container = document.getElementById("timeline-stream-container");
        if (!sentinel || !container) return;

        let loading = false;
        let hasMore = true;
        let lastDate = window.__digestDate || new Date().toISOString().split("T")[0];

        async function loadNextTimelineDate() {
            if (loading || !hasMore) return;
            loading = true;
            try {
                const res = await fetch(`/api/public/digest/timeline?before_date=${encodeURIComponent(lastDate)}`);
                if (!res.ok) { hasMore = false; return; }
                const data = await res.json();
                if (!data.has_more || !data.digest) {
                    hasMore = false;
                    sentinel.style.display = "none";
                    return;
                }

                lastDate = data.date;
                const d = data.digest;
                const dateLabel = formatDate(d.date);

                const sectionEl = document.createElement("section");
                sectionEl.className = "timeline-day-block";
                sectionEl.innerHTML = `
                    <div class="timeline-date-divider">
                        <div class="timeline-date-title">Briefing for ${escapeHtml(dateLabel)}</div>
                        <span class="timeline-date-badge">${escapeHtml(d.date)}</span>
                    </div>
                `;

                const topStories = (d.content && d.content.biggest_news) || [];
                if (topStories.length > 0) {
                    const gridEl = document.createElement("div");
                    gridEl.className = "top-grid";
                    gridEl.style.marginBottom = "40px";
                    topStories.forEach((item, idx) => {
                        const card = document.createElement("a");
                        card.className = "top-card" + (idx === 0 ? " lead" : "");
                        card.href = articleUrl("biggest_news", idx);
                        card.innerHTML = `
                            <div class="top-card-source">${escapeHtml(item.source || "Top Story")}</div>
                            <h3 class="top-card-title">${escapeHtml(item.headline || "")}</h3>
                            <p class="top-card-summary">${escapeHtml(item.summary || "")}</p>
                        `;
                        gridEl.appendChild(card);
                    });
                    sectionEl.appendChild(gridEl);
                }

                container.appendChild(sectionEl);
            } catch (err) {
                hasMore = false;
            } finally {
                loading = false;
            }
        }

        const observer = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting) {
                loadNextTimelineDate();
            }
        }, { rootMargin: "300px" });

        observer.observe(sentinel);
    }

    // ── Infinite Raw Article Stream ─────────────────────────────────────────
    function initRawArticleStream() {
        const sentinel = document.getElementById("raw-stream-sentinel");
        const grid = document.getElementById("raw-stream-grid");
        const filtersContainer = document.getElementById("stream-filters");
        if (!sentinel || !grid) return;

        let offset = 0;
        let limit = 15;
        let currentSource = "all";
        let loading = false;
        let hasMore = true;

        async function loadRawArticles(reset = false) {
            if (loading || (!hasMore && !reset)) return;
            if (reset) {
                offset = 0;
                hasMore = true;
                grid.innerHTML = "";
            }
            loading = true;

            try {
                const url = `/api/public/articles/stream?limit=${limit}&offset=${offset}&source=${encodeURIComponent(currentSource === "all" ? "" : currentSource)}`;
                const res = await fetch(url);
                if (!res.ok) { hasMore = false; return; }
                const data = await res.json();
                const articles = data.articles || [];

                if (articles.length === 0) {
                    hasMore = false;
                    if (offset === 0) {
                        grid.innerHTML = `<div style="grid-column: 1/-1; color: var(--muted); padding: 20px 0;">No articles indexed for this filter yet.</div>`;
                    }
                    sentinel.style.display = "none";
                    return;
                }

                articles.forEach(art => {
                    const card = document.createElement("div");
                    card.className = "raw-card";
                    const safeLink = safeUrl(art.url);
                    card.innerHTML = `
                        <div>
                            <div class="raw-card-top">
                                <span class="raw-card-source">${escapeHtml(art.source || "Feed")}</span>
                                <span class="raw-card-category">${escapeHtml(art.category || "News")}</span>
                            </div>
                            <h3 class="raw-card-title">${escapeHtml(art.title || "Untitled")}</h3>
                            <p class="raw-card-desc">${escapeHtml(art.description || "")}</p>
                        </div>
                        <div class="raw-card-footer">
                            <span>${escapeHtml(art.date || "")}</span>
                            <a href="${safeLink}" target="_blank" rel="noopener noreferrer" class="raw-card-link">
                                Read Source <i class="fa-solid fa-arrow-up-right-from-square"></i>
                            </a>
                        </div>
                    `;
                    grid.appendChild(card);
                });

                offset += articles.length;
                hasMore = data.has_more;
                if (!hasMore) {
                    sentinel.innerHTML = `<span class="stream-loading-text">End of intelligence stream</span>`;
                }
            } catch (err) {
                hasMore = false;
            } finally {
                loading = false;
            }
        }

        if (filtersContainer) {
            filtersContainer.querySelectorAll(".filter-pill").forEach(btn => {
                btn.addEventListener("click", () => {
                    filtersContainer.querySelectorAll(".filter-pill").forEach(p => p.classList.remove("active"));
                    btn.classList.add("active");
                    currentSource = btn.dataset.source || "all";
                    loadRawArticles(true);
                });
            });
        }

        const observer = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting) {
                loadRawArticles();
            }
        }, { rootMargin: "400px" });

        observer.observe(sentinel);
    }
})();
