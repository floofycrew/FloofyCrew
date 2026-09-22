"""FloofyCrew shared core.

Edition-neutral by construction: manifest, resolver, Patcher engine, registry
client, compatibility matrix, Loader runtime and SPA host. Edition-specific
values (payload discovery roots, host-version derivation, shim locations,
registry endpoints, identity) live in the ``floofy_edition_*`` adapters.
"""

#: FloofyCrew release version (SemVer). Bumped by the Forge release stage.
__version__ = "1.3.1"

#: The mod-facing API version (``floofy.api_version``). Mods depend on this,
#: never on host internals; deprecated names are kept for two minors.
API_VERSION = "1.2.0"
