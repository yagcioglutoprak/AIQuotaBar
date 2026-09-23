"""Design tokens shared by the menu bar title, the web UI, history and share card.

One source of truth for provider identity. The four brand hues were chosen
to stay distinguishable for colour-blind users on both light and dark
surfaces (validated all-pairs; the closest pair, Cursor/Copilot, always
ships with an icon + name so colour is never the only cue).
"""

# Provider identity: id -> display name, brand colour, icon file, icon tint.
# `tint` is applied to monochrome icons; None means the PNG is already coloured.
PROVIDERS = {
    "claude":  {"name": "Claude",  "color": "#D4704A", "icon": "claude_icon.png",        "tint": None},
    "chatgpt": {"name": "ChatGPT", "color": "#189E73", "icon": "chatgpt_icon_clean.png", "tint": "#189E73"},
    "cursor":  {"name": "Cursor",  "color": "#3A7BD5", "icon": "cursor.png",             "tint": "#3A7BD5"},
    "copilot": {"name": "Copilot", "color": "#B04BB8", "icon": "copilot.png",            "tint": "#B04BB8"},
}

# Display order everywhere (panel cards, status bar priority, history legend).
PROVIDER_ORDER = ["claude", "chatgpt", "cursor", "copilot"]

# ProviderData.name -> provider id
NAME_TO_ID = {v["name"]: k for k, v in PROVIDERS.items()}

# Severity colours (fixed, never used as a series colour).
SEVERITY = {
    "ok":   None,        # use the provider's brand colour
    "warn": "#F0A020",
    "crit": "#D8403C",
}

NEUTRAL = "#8A8A8E"


def brand_color(provider_id: str) -> str:
    return PROVIDERS.get(provider_id, {}).get("color", NEUTRAL)


def history_color(key: str) -> str:
    """Colour for a history key such as 'claude' or 'chatgpt_codex_tasks'."""
    for pid in PROVIDERS:
        if key == pid or key.startswith(pid + "_"):
            return PROVIDERS[pid]["color"]
    return NEUTRAL
