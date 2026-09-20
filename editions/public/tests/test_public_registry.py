"""The public edition's registry endpoint and pinned keys (task 7.1; Requirement 8.3, 10.1)."""
from __future__ import annotations

import base64
import json

import floofy_edition_public
from floofy_core.editions import default_sources, registry_keys, registry_opener
from floofy_core.registry_sources import Source
from floofy_core.sigverify import key_id
from floofy_edition_public.registry import CONFIG_PATH, archive_url_template


def test_pinned_keys_and_default_source_are_consistent():
    keys = floofy_edition_public.registry_keys()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert config["edition"] == floofy_edition_public.EDITION == "external"
    assert keys and all(key_id(public) == kid for kid, public in keys.items()), "ids are re-derived from the key bytes"
    assert {r["keyId"] for r in config["keys"]} == set(keys) and all("privateKey" not in r for r in config["keys"]), "only public records are pinned"
    sources = floofy_edition_public.default_sources()
    assert len(sources) == 1 and sources[0]["url"].startswith("https://") and sources[0]["keyId"] in keys and sources[0]["trust"] == "index"
    source = Source.from_default(sources[0])
    assert source.name == "floofycrew" and source.file_url("index.json").endswith("/index.json") and source.host_registry == {"repo": sources[0]["repo"], "branch": "main", "name": "floofycrew"}
    assert "{id}" in archive_url_template() and "{version}" in archive_url_template() and "{file}" in archive_url_template()
    # through the core hooks
    assert registry_keys([floofy_edition_public]) == keys
    assert default_sources([floofy_edition_public])[0]["edition"] == "external"
    assert registry_opener(sources[0]["url"], [floofy_edition_public]) is None, "no network identity on the public edition"
    assert floofy_edition_public.registry_opener("https://anything.example/") is None
    assert base64.b64decode(config["keys"][0]["publicKey"]) == keys[config["keys"][0]["keyId"]]
