// Fixture: the older crew-companion/petOverlay.js shape (two webPreferences blocks, two loadURLs).
function createOverlayFor(display) {
  const win = new BrowserWindow({
    // Without it, a click on the pet activates the app. No such method exists on
    // BrowserWindow — `acceptFirstMouse` is constructor-only.
    acceptFirstMouse: true,
    webPreferences: {
      preload: path.join(__dirname, "pet-preload.js"),
      contextIsolation: true,
    },
  });
  win.setContentProtection(true);
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  win.loadURL(companionPageUrl(baseUrl, "pet.html", credential));
  return win;
}
function createBrainWindow() {
  const win = new BrowserWindow({
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "pet-preload.js"),
      contextIsolation: true,
    },
  });
  win.setContentProtection(true);
  win.loadURL(companionPageUrl(baseUrl, "pet.html", credential));
  return win;
}
