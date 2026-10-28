"""Shared normalisation for instruction syntax across the three sources."""
from __future__ import annotations

import re


def canon(s: str) -> str:
    """Canonical instruction syntax used to match HTML/PDF/scan rows."""
    s = s.replace("Ý", "#").replace("Ð", "-")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("iram addr", "direct").replace("data addr", "direct").replace("direct addr", "direct")
    s = re.sub(r"#\s*data\s*16", "#data16", s)
    s = re.sub(r"#\s*data", "#data", s)
    s = re.sub(r"@\s*R[0-7]\b", "@Ri", s)
    s = re.sub(r"\bR[0-7]\b", "Rn", s)
    s = s.replace("@ Ri", "@Ri")
    s = s.replace("@ A a DPTR", "@A+DPTR").replace("@ A a PC", "@A+PC")
    s = s.replace("@A a DPTR", "@A+DPTR").replace("@A a PC", "@A+PC")
    s = s.replace("bit addr", "bit").replace("/ bit", "/bit")
    s = s.replace("reladdr", "rel").replace("rel addr", "rel")
    s = s.replace("code addr", "addr16")
    s = re.sub(r"page[0-7]\b", "addr11", s)
    s = re.sub(r"\s*,\s*", ",", s)
    s = re.sub(r"\s*@\s*", "@", s)
    # Restore the space between mnemonic and a leading @Ri/#data operand.
    s = re.sub(r"^([A-Z]{2,6})(?=[@#])", r"\1 ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s == "DA":
        s = "DA A"
    return s


def slug(canonical: str) -> str:
    """Stable id such as MOV_direct_direct or JMP_AT_A_PLUS_DPTR."""
    s = canonical.replace("@", "AT_").replace("#", "IMM_")
    s = s.replace("+", "_PLUS_").replace("/", "NOT_")
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s
