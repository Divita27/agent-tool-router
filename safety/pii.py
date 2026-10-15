"""Mask sensitive data before it reaches the model's context or any log.

mask_value("phone", "9876500001")      -> "XXXXXX0001"
mask_text("my PAN is ABCPK1234F")      -> "my PAN is AB•••••34F"
"""
import re

MASK_KEYS = {
    "phone": "last4", "new_phone": "last4", "account_number": "last4", "pan": "pan", "email": "email",
    "new_email": "email", "dob": "all",
}
LOG_ONLY_KEYS = {"address", "new_address"}  # the model may need these to confirm a change; logs never do

PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)")
PAN = re.compile(r"\b([A-Z]{2})[A-Z]{3}\d{2}(\d{2}[A-Z])\b", re.I)
LONG_NUMBER = re.compile(r"(?<!\d)\d{5,14}(\d{4})(?!\d)")  # bank accounts and other long numbers
EMAIL = re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[a-z]{2,})\b")


def mask_value(key, value):
    if value is None:
        return value
    s = str(value)
    kind = MASK_KEYS.get(key)
    if kind == "last4":
        digits = re.sub(r"\D", "", s)
        return "X" * max(len(digits) - 4, 0) + digits[-4:]
    if kind == "pan":
        return PAN.sub(r"\1•••••\2", s.upper())
    if kind == "email":
        return EMAIL.sub(r"\1•••\2", s)
    if kind == "all":
        return "••••"
    return value


def mask_text(text):
    text = PHONE.sub(lambda m: "XXXXXX" + m.group(1)[-4:], str(text))
    text = PAN.sub(r"\1•••••\2", text)
    text = EMAIL.sub(r"\1•••\2", text)
    return LONG_NUMBER.sub(lambda m: "X" * (len(m.group(0)) - 4) + m.group(1), text)


def mask_for_model(obj):
    """Tool arguments/results going to the model: mask identifiers, keep what the agent must say back."""
    if isinstance(obj, dict):
        return {k: (mask_value(k, v) if k in MASK_KEYS else mask_for_model(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_for_model(x) for x in obj]
    return obj


def mask_for_log(obj):
    """Anything written to disk: mask identifiers AND addresses, and scrub free text."""
    if isinstance(obj, dict):
        return {k: ("[address]" if k in LOG_ONLY_KEYS else mask_value(k, v) if k in MASK_KEYS else mask_for_log(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_for_log(x) for x in obj]
    if isinstance(obj, str):
        return mask_text(obj)
    return obj
