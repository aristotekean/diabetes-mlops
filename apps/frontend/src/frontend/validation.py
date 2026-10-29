"""Client-side checks run before calling the API.

They catch obvious typos early; the API still validates every request and its
422 errors are shown as well.
"""

from __future__ import annotations

import re
from typing import Any

from frontend.fields import FIELDS

# ICD-9: numeric (428, 250.83), supplementary V codes (V45) or E codes (E878).
_ICD9 = re.compile(r"^(\d{1,3}(\.\d{1,2})?|V\d{2}(\.\d{1,2})?|E\d{3}(\.\d)?)$")


def validate_features(features: dict[str, Any]) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    for field in FIELDS:
        value = features.get(field.name)
        if field.kind == "text" and value is not None and not _ICD9.match(value):
            errors.append((field.name, f"'{value}' no es un código ICD-9 válido"))
        elif field.kind == "number" and not field.min_value <= value <= field.max_value:
            errors.append(
                (field.name, f"debe estar entre {field.min_value} y {field.max_value}")
            )
        elif field.kind == "select" and value is None and not field.nullable:
            errors.append((field.name, "es obligatorio"))
    if features.get("diag_1") is None:
        errors.append(("diag_1", "el diagnóstico principal es obligatorio"))
    return errors
