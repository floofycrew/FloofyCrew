"""Example python-hook part (Requirement 2.4).

The Loader imports this package under a namespaced ``sys.modules`` key and
calls ``activate(ctx)`` inside a try/except; an exception disables only this
mod. Hooks registered through ``ctx.hooks`` are unwound on ``deactivate``.
"""


def activate(ctx):
    """Log a greeting and wrap one host function."""
    ctx.log.info("example-python-hook active on host %s", ctx.host.version)

    def after_index(result, *args, **kwargs):
        ctx.log.debug("dashboard index served")
        return result

    ctx.hooks.after("kiro_crew.dashboard.handlers.core:index", after_index)


def deactivate(ctx):
    """Nothing to do: the hook registry unwinds our wrappers."""
    ctx.log.info("example-python-hook deactivated")
