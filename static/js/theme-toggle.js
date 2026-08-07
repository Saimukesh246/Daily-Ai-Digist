(function () {
    "use strict";

    // Shares the localStorage key with the public blog (static/js/blog.js)
    // so the light/dark preference is unified across the whole site.
    const THEME_KEY = "aidigest_blog_theme";

    const btn = document.getElementById("btn-theme-toggle");
    const icon = document.getElementById("theme-toggle-icon");
    if (!btn || !icon) return;

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
