// Illustrative App UI entry. Real apps use the host-provided app SDK.
export function mount(container) {
  const node = document.createElement("p");
  node.textContent = "Hello from example-app (installed by FloofyCrew).";
  container.appendChild(node);
  return () => node.remove();
}
