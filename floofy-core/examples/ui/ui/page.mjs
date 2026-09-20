// Example ui part: the mod's own page inside the FloofyCrew App (Requirement 16.4).
//
// The App imports this module same-origin (/api/apps/floofycrew/ui/mods/<id>/ui/page.mjs)
// and calls the default export with the page's container and `api = floofy.mod(id)`:
// config.get()/set(patch) persisted in the mod's .floofy/config.json, routes.list()/fetch(path)
// for the mod's python-hook routes, theme.tokens()/current(), state(). Return a cleanup
// function (or an object with unmount()); a throw is caught by the App's error boundary,
// which shows it with a Disable button and never takes the manager page down.
export default async function mount(container, api) {
  const config = await api.config.get();
  const label = document.createElement("label");
  label.textContent = "Greeting: ";
  const input = document.createElement("input");
  input.value = config.greeting || "";
  input.addEventListener("change", () => api.config.set({ greeting: input.value }));
  label.appendChild(input);
  container.appendChild(label);
  return () => {
    container.textContent = "";
  };
}
