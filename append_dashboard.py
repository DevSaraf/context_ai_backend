import os

DASHBOARD_PATH = r"D:\context-ai-backend\dashboard.html"
with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
    content = f.read()

picker_script = """
<!--
  KRAB — Google Drive Picker (frontend)
-->
<script>
(function () {
  let _gapiLoaded = false;
  let _gisLoaded = false; 
  let _pickerReady = false;

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if (document.querySelector(`script[src="${src}"]`)) return resolve();
      const s = document.createElement("script");
      s.src = src; s.async = true; s.defer = true;
      s.onload = resolve; s.onerror = () => reject(new Error("load failed: " + src));
      document.head.appendChild(s);
    });
  }

  async function ensurePickerLoaded() {
    if (_pickerReady) return;
    await loadScript("https://apis.google.com/js/api.js");
    await new Promise((resolve) => gapi.load("picker", { callback: resolve }));
    _pickerReady = true;
  }

  window.openDrivePicker = async function () {
    try {
      const res = await fetch(`${API_URL}/connectors/google_drive/picker-config`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (res.status === 401) {
        alert("Your Google session expired. Please reconnect Google Drive.");
        return;
      }
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        alert(e.detail || "Could not open the file picker.");
        return;
      }
      const cfg = await res.json();

      await ensurePickerLoaded();

      const docsView = new google.picker.DocsView(google.picker.ViewId.DOCS)
        .setIncludeFolders(true)
        .setSelectFolderEnabled(false)   
        .setOwnedByMe(true);
      const sharedView = new google.picker.DocsView(google.picker.ViewId.DOCS)
        .setIncludeFolders(true)
        .setOwnedByMe(false);

      const picker = new google.picker.PickerBuilder()
        .enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
        .setOAuthToken(cfg.oauth_token)
        .setDeveloperKey(cfg.api_key)
        .setAppId(cfg.app_id)
        .addView(docsView)
        .addView(sharedView)
        .setTitle("Select files to import into KRAB")
        .setCallback(pickerCallback)
        .build();

      picker.setVisible(true);
    } catch (err) {
      console.error(err);
      alert("Failed to open Google Picker: " + err.message);
    }
  };

  async function pickerCallback(data) {
    if (data.action !== google.picker.Action.PICKED) return;
    const ids = (data.docs || []).map((d) => d.id);
    if (!ids.length) return;

    try {
      const res = await fetch(`${API_URL}/connectors/google_drive/selection`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({ file_ids: ids }),
      });
      const out = await res.json();
      if (res.ok) {
        alert(`Importing ${out.count} file${out.count !== 1 ? "s" : ""} from Google Drive…`);
        if (typeof loadConnectors === "function") setTimeout(loadConnectors, 1500);
        if (typeof loadSyncLogs === "function") setTimeout(loadSyncLogs, 1500);
      } else {
        alert(out.detail || "Failed to save selection.");
      }
    } catch (e) {
      alert("Connection error while saving selection.");
    }
  }
})();
</script>
"""

if "window.openDrivePicker = async function" not in content:
    content = content.replace("</body>", picker_script + "\n</body>")
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print("Updated dashboard.html")
else:
    print("Already updated.")
