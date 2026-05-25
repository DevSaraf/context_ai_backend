#!/usr/bin/env python3
"""
KRAB Dashboard Patcher — Removes all confidence displays.
Usage: python3 patch_dashboard.py dashboard.html
Output: dashboard.html (overwritten in place)
"""
import sys, os

if len(sys.argv) < 2:
    print("Usage: python3 patch_dashboard.py <path-to-dashboard.html>")
    sys.exit(1)

filepath = sys.argv[1]
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

original_len = len(content)
patches_applied = 0

def patch(old, new, label):
    global content, patches_applied
    if old in content:
        content = content.replace(old, new, 1)
        patches_applied += 1
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ SKIPPED (not found): {label}")

print(f"Patching {filepath} ({original_len} chars)...\n")

# 1. Remove confidence bar from Ask AI answer
patch(
    '<div class="answer-confidence"><span>Confidence:</span><div class="conf-bar"><div class="conf-fill ${cl}" style="width:${cp}%"></div></div><span>${cp}%</span><span style="margin-left:6px;">· ${data.chunks_used || 0} sources</span></div>',
    '<div class="answer-confidence"><span>${data.chunks_used || 0} sources used</span></div>',
    "Remove confidence bar from Ask AI"
)

# 2. Remove cp/cl variables in askQuestion()
patch(
    """        const cp = Math.round((data.confidence || 0) * 100);
        const cl = cp >= 70 ? 'high' : cp >= 40 ? 'medium' : 'low';
        const sources = data.sources || [];""",
    "        const sources = data.sources || [];",
    "Remove cp/cl vars in askQuestion()"
)

# 3. Remove confidence from source items
patch(
    """            const sc = Math.round((s.confidence || s.similarity || 0) * 100);
            sh += `<div class="source-item"><div class="source-meta"><span class="source-label">[${i+1}] ${s.source_type || 'Doc'} #${s.source_id || ''}</span><span class="source-conf">${sc}%</span></div>${esc(s.text || '').substring(0, 250)}${(s.text||'').length > 250 ? '...' : ''}</div>`;""",
    """            sh += `<div class="source-item"><div class="source-meta"><span class="source-label">[${i+1}] ${s.source_type || 'Doc'} #${s.source_id || ''}</span></div>${esc(s.text || '').substring(0, 250)}${(s.text||'').length > 250 ? '...' : ''}</div>`;""",
    "Remove confidence from source items"
)

# 4. Remove confidence from widget inbox tickets
patch(
    """                    ${t.confidence ? '<span>' + Math.round(t.confidence * 100) + '% conf</span>' : ''}""",
    "",
    "Remove confidence from widget inbox"
)

# 5. Remove AI confidence span from ticket detail
patch(
    """                        <span id="ai-confidence" style="margin-left: auto; font-size: 11px; color: var(--text-muted);"></span>""",
    "",
    "Remove ai-confidence span from ticket detail"
)

# 6. Remove JS that sets ai-confidence text
patch(
    """                document.getElementById('ai-confidence').textContent = data.confidence ? `${(data.confidence * 100).toFixed(0)}% confidence` : '';""",
    "",
    "Remove ai-confidence JS setter"
)

# 7. Remove % match from similar tickets
patch(
    """                        <span>${Math.round((t.confidence || t.similarity || 0) * 100)}% match</span>""",
    "",
    "Remove % match from similar tickets"
)

# 8. Remove cp% from ticket response header
patch(
    """                <span style="font-size:11px;color:var(--text-dim);">${cp}% · ${esc(selectedTone)}</span>""",
    """                <span style="font-size:11px;color:var(--text-dim);">${esc(selectedTone)}</span>""",
    "Remove cp% from response header"
)

# 9. Remove cp variable in resolveTicket()
patch(
    """        const cp = Math.round((data.confidence || 0) * 100);
        const responseText = data.suggested_response || '';""",
    "        const responseText = data.suggested_response || '';",
    "Remove cp var in resolveTicket()"
)

# 10. Remove confidence from ticket history save
patch(
    """            confidence: cp,""",
    "",
    "Remove confidence from history save"
)

# 11. Remove confidence from loadTicketFromHistory
patch(
    """            <span style="font-size:11px;color:var(--text-dim);">${item.confidence}% · ${esc(item.tone)}</span>""",
    """            <span style="font-size:11px;color:var(--text-dim);">${esc(item.tone)}</span>""",
    "Remove confidence from history display"
)

# 12. Remove confidence from ticket history list
patch(
    """                <span>${t.confidence}%</span>""",
    "",
    "Remove confidence from history list"
)

print(f"\n{patches_applied} patches applied.")
print(f"File size: {original_len} → {len(content)} chars")

# Write patched file
with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"Saved to {filepath}")