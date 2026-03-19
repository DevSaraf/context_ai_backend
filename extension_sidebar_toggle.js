/*
=================================================================
EXTENSION SIDEBAR TOGGLE / MINIMIZE
=================================================================
Adds a collapse button to the sidebar header.
When collapsed: sidebar shrinks to a small floating pill.
When expanded: full 340px sidebar as before.

Remembers state in chrome.storage.local so it persists across pages.

TWO CHANGES TO MAKE IN content.js:
1. Add the new CSS to your STYLES string
2. Replace createSidebar() function
=================================================================
*/


// ============================================================
// STEP 1: Add this CSS to your STYLES string in content.js
//         (paste before the closing backtick of STYLES)
// ============================================================

const TOGGLE_STYLES = `
/* Toggle Button */
.ctx-toggle-btn {
    width: 28px;
    height: 28px;
    background: rgba(255,255,255,0.08);
    border: 1px solid #333;
    border-radius: 6px;
    color: #888;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
    padding: 0;
    margin-left: 8px;
}

.ctx-toggle-btn:hover {
    background: rgba(255,255,255,0.12);
    color: #fff;
    border-color: #555;
}

.ctx-toggle-btn svg {
    width: 14px;
    height: 14px;
    transition: transform 0.2s;
}

/* Collapsed State */
#context-sidebar.collapsed {
    width: 48px;
    overflow: hidden;
}

#context-sidebar.collapsed .ctx-header {
    padding: 12px;
    justify-content: center;
}

#context-sidebar.collapsed .ctx-header-top {
    flex-direction: column;
    align-items: center;
    gap: 0;
}

#context-sidebar.collapsed .ctx-logo {
    display: none;
}

#context-sidebar.collapsed #auth-status {
    display: none;
}

#context-sidebar.collapsed .ctx-content {
    display: none;
}

#context-sidebar.collapsed .ctx-toggle-btn {
    margin-left: 0;
}

#context-sidebar.collapsed .ctx-toggle-btn svg {
    transform: rotate(180deg);
}

/* Floating Pill (alternative collapsed state) */
#context-sidebar-pill {
    position: fixed;
    top: 50%;
    right: 0;
    transform: translateY(-50%);
    width: 40px;
    height: 40px;
    background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
    border-radius: 10px 0 0 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    z-index: 9998;
    box-shadow: -2px 0 12px rgba(0,0,0,0.3);
    transition: all 0.2s;
    border: none;
    color: #fff;
}

#context-sidebar-pill:hover {
    width: 48px;
    box-shadow: -4px 0 20px rgba(0,0,0,0.4);
}

#context-sidebar-pill svg {
    width: 20px;
    height: 20px;
}

#context-sidebar-pill.hidden {
    display: none;
}

/* Transition for sidebar */
#context-sidebar {
    transition: width 0.2s ease, opacity 0.2s ease;
}

#context-sidebar.sidebar-hidden {
    width: 0;
    opacity: 0;
    overflow: hidden;
    border-left: none;
    pointer-events: none;
}
`;


// ============================================================
// STEP 2: Replace your createSidebar() function in content.js
//         with this version
// ============================================================

function createSidebar() {
    if (document.getElementById("context-sidebar")) return;

    // Inject styles (add TOGGLE_STYLES to your existing STYLES)
    const styleEl = document.createElement("style");
    styleEl.textContent = STYLES;   // Make sure TOGGLE_STYLES CSS is included in STYLES
    document.head.appendChild(styleEl);

    // Create the floating pill (shown when sidebar is hidden)
    const pill = document.createElement("button");
    pill.id = "context-sidebar-pill";
    pill.className = "hidden";
    pill.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/>
            <path d="M12 6v6l4 2"/>
        </svg>
    `;
    pill.addEventListener("click", () => toggleSidebar(true));
    document.body.appendChild(pill);

    // Create sidebar
    const sidebar = document.createElement("div");
    sidebar.id = "context-sidebar";

    sidebar.innerHTML = `
        <div class="ctx-header">
            <div class="ctx-header-top">
                <div class="ctx-logo">
                    ${ICONS.logo}
                    <h1 class="ctx-title">Context Assistant</h1>
                </div>
                <div style="display:flex;align-items:center;">
                    <div id="auth-status" class="ctx-status logged-out">
                        <span class="ctx-status-dot"></span>
                        <span class="ctx-status-text">Checking...</span>
                    </div>
                    <button class="ctx-toggle-btn" id="ctx-toggle-btn" title="Minimize sidebar">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="15 18 9 12 15 6"/>
                        </svg>
                    </button>
                </div>
            </div>
        </div>
        <div id="context-output" class="ctx-content">
            <div class="ctx-loading">
                <div class="ctx-spinner"></div>
                <div class="ctx-loading-text">Checking authentication...</div>
            </div>
        </div>
    `;

    document.body.appendChild(sidebar);

    // Toggle button listener
    document.getElementById("ctx-toggle-btn").addEventListener("click", () => {
        toggleSidebar(false);
    });

    // Restore saved state
    restoreSidebarState();

    checkAuth();
}

function toggleSidebar(show) {
    const sidebar = document.getElementById("context-sidebar");
    const pill = document.getElementById("context-sidebar-pill");
    const main = document.querySelector("main");

    if (show) {
        // Show sidebar
        sidebar.classList.remove("sidebar-hidden");
        pill.classList.add("hidden");
        if (main) main.style.marginRight = "340px";
        saveSidebarState(true);
    } else {
        // Hide sidebar
        sidebar.classList.add("sidebar-hidden");
        pill.classList.remove("hidden");
        if (main) main.style.marginRight = "0";
        saveSidebarState(false);
    }
}

function saveSidebarState(isOpen) {
    try {
        if (chrome.storage && chrome.storage.local) {
            chrome.storage.local.set({ sidebar_open: isOpen });
        }
    } catch (e) {
        // Ignore storage errors
    }
}

function restoreSidebarState() {
    try {
        if (chrome.storage && chrome.storage.local) {
            chrome.storage.local.get(["sidebar_open"], function(data) {
                // Default to open if no saved state
                if (data.sidebar_open === false) {
                    toggleSidebar(false);
                } else {
                    toggleSidebar(true);
                }
            });
        } else {
            // No storage access, default to open
            toggleSidebar(true);
        }
    } catch (e) {
        toggleSidebar(true);
    }
}

// Call this at the end of your content.js
// createSidebar();


// ============================================================
// SUMMARY OF CHANGES
// ============================================================
//
// 1. Added TOGGLE_STYLES CSS — paste into your STYLES string
//
// 2. Replaced createSidebar() — now includes:
//    - A chevron toggle button in the header (next to auth status)
//    - A floating pill button (appears when sidebar is hidden)
//    - toggleSidebar(show) function to switch states
//    - State persistence via chrome.storage.local
//
// 3. When collapsed:
//    - Sidebar slides to width:0 with opacity transition
//    - A small branded pill appears on the right edge
//    - Main content reclaims the 340px
//    - Click pill to restore sidebar
//
// 4. State persists across page navigations and chat switches
//
// That's it — two additions to content.js, no backend changes.
