// 点击工具栏图标时打开侧边栏（兼容 Chrome / Edge）
chrome.action.onClicked.addListener(async (tab) => {
  // Chrome 优先使用原生 sidePanel
  try {
    await chrome.sidePanel.open({ tabId: tab.id });
    return;
  } catch (_e) {
    // Edge 可能不支持 sidePanel.open，回退到弹窗
  }
  if (!chrome.runtime.lastError) {
    try {
      await chrome.sidePanel.setOptions({
        tabId: tab.id,
        path: "panel.html",
        enabled: true,
      });
      await chrome.sidePanel.open({ tabId: tab.id });
      return;
    } catch (_e2) {}
  }
  // 回退：在新标签页打开
  chrome.tabs.create({ url: "panel.html" });
});

// 安装时尝试设置侧边栏行为
chrome.runtime.onInstalled.addListener(() => {
  try {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  } catch (_e) {}
});
