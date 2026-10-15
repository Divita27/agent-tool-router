"""Understand a caller's yes / no in Hinglish, Hindi and English.

classify("haan kar do")        -> "yes"
classify("nahi ruko")          -> "no"
classify("haan... nahi ruko")  -> "unclear"   (mixed signals: ask again, never act)
classify("kitna lagega?")      -> "unclear"   (not an answer)
"""
import re

# Latin words get word boundaries on both sides. Devanagari gets a leading boundary only, because
# Python's \b doesn't see a boundary after a trailing vowel sign (e.g. the ं in नहीं).
YES = [
    r"\bhaa*n?\b", r"\bhaanji\b", r"\bji\b", r"\btheek\b", r"\bthik\b", r"\bok\b", r"\bokay\b", r"\byes\b",
    r"\byeah\b", r"\bsure\b", r"\bbilkul\b", r"\bkar ?do\b", r"\bkar dijiye\b", r"\bconfirm", r"\bproceed\b",
    r"\bgo ahead\b", r"\bchalega\b",
    r"\bहाँ", r"\bहां", r"\bजी\b", r"\bठीक", r"\bकर दो", r"\bकर दीजिए", r"\bबिल्कुल", r"\bचलेगा",
]
NO = [
    r"\bnahi+n?\b", r"\bnai\b", r"\bmat\b", r"\bruko\b", r"\bruk\b", r"\bcancel", r"\bno\b", r"\bnope\b",
    r"\bdon'?t\b", r"\bwait\b", r"\bbaad me", r"\blater\b", r"\bstop\b", r"\brehne do\b", r"\bchhodo\b",
    r"\bरुको", r"\bनहीं", r"\bनही", r"\bमत\b", r"\bबाद में", r"\bरहने दो", r"\bछोड़ो",
]
_yes = re.compile("|".join(YES), re.I)
_no = re.compile("|".join(NO), re.I)


def classify(text):
    t = text.lower()
    yes, no = bool(_yes.search(t)), bool(_no.search(t))
    if yes and not no:
        return "yes"
    if no and not yes:
        return "no"
    return "unclear"
