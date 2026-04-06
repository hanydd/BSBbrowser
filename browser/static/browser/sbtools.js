// ---------------------------------------------------------------------------
// Table partial-refresh helpers
// ---------------------------------------------------------------------------

/**
 * Fetch `url`, parse the response HTML, and swap only #tableContainer in-place.
 * Falls back to a full navigation if the element is missing.
 */
function fetchTable(url) {
    const current = document.querySelector('#tableContainer');
    if (!current) {
        window.location.href = url;
        return;
    }

    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (r) { return r.text(); })
        .then(function (html) {
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const incoming = doc.querySelector('#tableContainer');
            if (incoming) {
                current.replaceWith(incoming);
            } else {
                // server returned a page without the container (e.g. redirect)
                window.location.href = url;
            }
        })
        .catch(function () {
            window.location.href = url;
        });
}

// ---------------------------------------------------------------------------
// Filter form: intercept submit so only #tableContainer is refreshed
// ---------------------------------------------------------------------------
document.addEventListener('submit', function (e) {
    const form = e.target;
    if (form.id !== 'filterForm') return;
    if (!document.querySelector('#tableContainer')) return;

    e.preventDefault();
    const params = new URLSearchParams();
    new FormData(form).forEach(function (v, k) {
        if (v !== '') params.append(k, v);
    });
    const url = location.pathname + (params.toString() ? '?' + params.toString() : '');
    history.pushState(null, '', url);
    fetchTable(url);
});

// ---------------------------------------------------------------------------
// Clear button: go back to bare pathname (no query string)
// ---------------------------------------------------------------------------
document.addEventListener('click', function (e) {
    const btn = e.target.closest('.formreset');
    if (!btn) return;
    e.preventDefault();
    if (!document.querySelector('#tableContainer')) return;
    history.pushState(null, '', location.pathname);
    fetchTable(location.pathname);
});

// ---------------------------------------------------------------------------
// Pagination & sort links inside #tableContainer (event delegation so it
// keeps working after every swap). We intentionally do NOT intercept
// row detail links (.cell-link), so those still do a full-page navigation.
// ---------------------------------------------------------------------------
document.addEventListener('click', function (e) {
    const link = e.target.closest('#tableContainer a');
    if (!link) return;

    const isPaginationLink =
        link.classList.contains('page-link') &&
        Boolean(link.closest('.pagination')) &&
        Boolean(link.getAttribute('href'));
    const isSortLink =
        Boolean(link.closest('thead th')) &&
        Boolean(link.getAttribute('href'));

    if (!isPaginationLink && !isSortLink) return;

    e.preventDefault();
    history.pushState(null, '', link.href);
    fetchTable(link.href);
});

// ---------------------------------------------------------------------------
// Browser back / forward
// ---------------------------------------------------------------------------
window.addEventListener('popstate', function () {
    if (document.querySelector('#tableContainer')) {
        fetchTable(location.href);
    }
});

// ---------------------------------------------------------------------------
// Dark mode toggle
// ---------------------------------------------------------------------------
const darkmodeBtn = document.querySelector('#darkmode');
if (darkmodeBtn && typeof darkmode !== 'undefined') {
    darkmodeBtn.onclick = function () {
        darkmode.toggleDarkMode();
    };
}

// ---------------------------------------------------------------------------
// Copy-to-clipboard (.clip elements) – delegated so it survives table swaps
// ---------------------------------------------------------------------------
document.addEventListener('click', function (e) {
    const el = e.target.closest('.clip');
    if (!el) return;
    navigator.clipboard.writeText(el.dataset.value);
});

window.onpagehide = function () {};

// ---------------------------------------------------------------------------
// Cursor-follow glow for search buttons
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.search-card .btn-primary').forEach(function (btn) {
        btn.addEventListener('mousemove', function (e) {
            const rect = btn.getBoundingClientRect();
            btn.style.setProperty('--glow-x', (e.clientX - rect.left) + 'px');
            btn.style.setProperty('--glow-y', (e.clientY - rect.top) + 'px');
        });
    });
});
