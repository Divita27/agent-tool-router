"""Tool catalog helpers.

    from tools import TOOLS_BY_NAME, schema, schemas
    schemas()                         # all tools, in the format Sarvam's API expects
    schemas(["get_next_emi", ...])    # only some tools (what the router will do)
"""
from tools.catalog import TOOLS, Param, Tool

TOOLS_BY_NAME = {t.name: t for t in TOOLS}
AREAS = sorted({t.area for t in TOOLS})
RISKS = ("read", "write", "payment", "pii", "irreversible")


def schema(tool: Tool) -> dict:
    """The function-calling definition the model sees. No area, risk or examples."""
    properties = {}
    for name, p in tool.params.items():
        prop = {"type": p.type, "description": p.description}
        if p.enum:
            prop["enum"] = p.enum
        properties[name] = prop
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": [n for n, p in tool.params.items() if p.required],
            },
        },
    }


def schemas(names=None) -> list:
    tools = TOOLS if names is None else [TOOLS_BY_NAME[n] for n in names]
    return [schema(t) for t in tools]
