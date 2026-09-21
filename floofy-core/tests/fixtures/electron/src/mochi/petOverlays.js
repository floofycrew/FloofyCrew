// Fixture: the two hunks of TT P512647819 in their upstream shape (mochi/petOverlays.js), plus decoys.
function rearm(win) {
  // a second loadURL of pet.html with a different token variable: NOT the anchor
  win.loadURL(mochiPageUrl(currentBaseUrl, "pet.html", pageToken));
}
function createOverlayForDisplay(display) {
  const win = new BrowserWindow({
    x: display.bounds.x,
    frame: false,
    transparent: true,
    acceptFirstMouse: true,
    webPreferences: {
      preload: path.join(__dirname, "pet-preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });
  win.setFocusable(false);
  win.setAlwaysOnTop(true, "screen-saver");
  win.loadURL(mochiPageUrl(currentBaseUrl, "pet.html", currentToken));

  win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
    handleOverlayLoadFailure(win, code, isMainFrame);
  });
  return win;
}
function openPanelWindow() {
  const win = new BrowserWindow({
    webPreferences: {
      preload: path.join(__dirname, "panel-preload.js"),
    },
  });
  win.setAlwaysOnTop(true, "screen-saver");
  return win;
}
