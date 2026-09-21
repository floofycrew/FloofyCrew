"""settings-demo — the mod's backend (a ``python-hook`` part; Requirement 16.4).

The FloofyCrew Loader imports this package inside the gateway under a
namespaced ``sys.modules`` key and calls :func:`activate` with the mod's
context. The only thing this mod does on the Python side is register one
backend route, ``echo``, which the settings page (``ui/page.mjs``) reaches
through ``floofy.mod("settings-demo").routes.fetch("echo")`` — served by the
Loader at ``/api/apps/floofycrew/mods/settings-demo/api/echo``. The route answers
with the config the page stored through ``floofy.mod(id).config.set(...)``: the
page and the hook read the same ``.floofy/config.json`` (``ctx.config``), which
is the point of the demo.

No host function is hooked and no network is used; a raising route would be
that request's 500, never a fault of the mod (the Loader keeps a bad request
from disabling anything).
"""

DEFAULTS = {"greeting": "Hello from settings-demo", "notify": False}


def activate(ctx):
    """Register the ``echo`` route (GET and POST) and publish it in the mod's state."""

    def echo(request):
        stored = ctx.config.to_dict()
        reply = {
            "mod": ctx.mod_id,
            "version": ctx.version,
            "config": stored,
            "effective": {**DEFAULTS, **stored},
            "host": {"edition": ctx.host.edition, "version": ctx.host.version},
            "method": request.method,
        }
        if request.method == "POST":
            reply["echo"] = request.json()
        if request.query:
            reply["query"] = request.query
        return reply

    ctx.routes.add("GET", "echo", echo)
    ctx.routes.add("POST", "echo", echo)
    ctx.state["routes"] = ctx.routes.list()
    ctx.log.info("settings-demo active on host %s: %d route(s) registered", ctx.host.version, len(ctx.state["routes"]))


def deactivate(ctx):
    """Nothing to undo: the Loader unwinds the routes with the mod."""
    ctx.log.info("settings-demo deactivated")
