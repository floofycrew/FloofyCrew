// Example spa part (Requirement 2.5, Requirement 4): an ES module the SPA host
// loads after the host bundle, inside an error boundary. It may talk to the
// local gateway in plaintext (loopback is the one exception to encrypted-only)
// and to the hosts declared in floofy.json.

export function activate(floofy) {
  const badge = document.createElement("span");
  badge.id = "floofy-example-badge";
  badge.textContent = `modded (${floofy.host.version})`;
  document.body.appendChild(badge);
  return fetch("http://127.0.0.1:8080/api/status").then((response) => response.ok);
}

export function deactivate() {
  document.getElementById("floofy-example-badge")?.remove();
}
