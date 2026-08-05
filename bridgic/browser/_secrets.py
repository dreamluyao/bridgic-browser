"""
Secret-argument declarations and redaction helpers for browser tool calls.

Passing ``is_secret=True`` to a text-input tool tells bridgic that the
accompanying value is a credential.  bridgic then masks it everywhere bridgic
itself controls:

- the status string the tool returns, and
- bridgic's own log records - including a value echoed back inside a Playwright
  error message (see :func:`scrub_secrets`).

What bridgic **cannot** reach is the *arguments* record kept by whatever agent
framework invoked the tool.  A framework such as ``bridgic-amphibious`` stores
the raw ``arguments`` dict of every tool call and fans it out to its step log,
its on-disk trace, and the prompt of the next LLM turn - all outside this
package.  :data:`SECRET_TOOL_ARGUMENTS` and :func:`redact_tool_arguments` are
the machine-readable contract across that boundary: a framework, or an
application's own pre-record hook, can redact before recording.

    from bridgic.browser import redact_tool_arguments

    redact_tool_arguments(
        "input_text_by_ref",
        {"ref": "8d4b03a9", "text": "hunter2", "is_secret": True},
    )
    # -> {"ref": "8d4b03a9", "text": "***", "is_secret": True}

Redaction is **gated** on the flag the call actually passed, so ordinary form
filling stays fully readable in traces - only values the caller marked secret
are masked.  Tools not listed in :data:`SECRET_TOOL_ARGUMENTS` are returned
unchanged, so the function is safe to apply to every tool call indiscriminately.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

REDACTED = "***"
"""Replacement text substituted for a secret value."""

SECRET_SCHEMA_KEY = "x-bridgic-secret"
"""JSON Schema extension key marking a tool property as secret-bearing.

:func:`annotate_schema_with_secrets` stamps this onto the generated parameter
schema of every tool in :data:`SECRET_TOOL_ARGUMENTS`, so a consumer that never
imports bridgic can still find the secret argument by walking the schema it
already has. The ``x-`` prefix is the JSON Schema convention for a vendor
extension: validators and LLM providers ignore unknown keywords.

The value is a dict, which means the naive check degrades safely --
``if prop.get("x-bridgic-secret"): redact`` redacts unconditionally, while a
consumer that reads ``gated_by`` can match bridgic's own behaviour and redact
only calls that actually set the flag.
"""

_MIN_SCRUB_LENGTH = 3
"""Shortest secret :func:`scrub_secrets` will substring-replace.

Replacing a one- or two-character value inside a diagnostic message corrupts
the message far beyond its remaining usefulness (a secret of ``"a"`` would eat
every ``a`` in the text), and such values carry no real secret.  The primary
masking path - the tool's own return message - never contains the value at all,
so this threshold only bounds the defense-in-depth scrub of error strings.
"""


@dataclass(frozen=True)
class SecretArgumentRule:
    """Declares one argument of one tool as a conditionally-secret value.

    Attributes
    ----------
    value_param : str
        Name of the argument that holds the secret value.
    gate_param : Optional[str]
        Name of the boolean argument that marks the call as secret. ``None``
        means ``value_param`` is unconditionally secret.
    item_value_key : Optional[str]
        Set when ``value_param`` is a list of dicts (``fill_form``'s ``fields``)
        rather than a scalar: the dict key holding each item's secret value.
    item_gate_key : Optional[str]
        Per-item gate key inside those dicts. An item is redacted when either
        the call-level ``gate_param`` or this per-item key is truthy.
    """

    value_param: str
    gate_param: Optional[str] = None
    item_value_key: Optional[str] = None
    item_gate_key: Optional[str] = None

    def as_schema_marker(self) -> Dict[str, Any]:
        """Return this rule as the value of a :data:`SECRET_SCHEMA_KEY` entry.

        A scalar argument's marker stays ``{"gated_by": "is_secret"}``; the
        list-of-dicts case carries the two extra keys a consumer needs to reach
        into the items. ``gated_by`` is always present -- ``None`` means the
        argument is unconditionally secret -- so the dict is never empty and
        therefore never falsy.

        Returns
        -------
        Dict[str, Any]
        """
        marker: Dict[str, Any] = {"gated_by": self.gate_param}
        if self.item_value_key is not None:
            marker["item_value_key"] = self.item_value_key
        if self.item_gate_key is not None:
            marker["item_gate_key"] = self.item_gate_key
        return marker


SECRET_TOOL_ARGUMENTS: Dict[str, Tuple[SecretArgumentRule, ...]] = {
    "input_text_by_ref": (
        SecretArgumentRule(value_param="text", gate_param="is_secret"),
    ),
    "type_text": (
        SecretArgumentRule(value_param="text", gate_param="is_secret"),
    ),
    "fill_form": (
        SecretArgumentRule(
            value_param="fields",
            gate_param="is_secret",
            item_value_key="value",
            item_gate_key="is_secret",
        ),
    ),
}
"""Every browser tool argument that can carry a secret, keyed by tool name.

Tool names are the SDK method names, which are also the names
``BrowserToolSetBuilder`` gives the generated tool specs and the command names
the CLI daemon dispatches on - one table serves all three surfaces.
"""


def secret_argument_rules(tool_name: str) -> Tuple[SecretArgumentRule, ...]:
    """Return the secret-argument rules for ``tool_name`` (empty when none)."""
    return SECRET_TOOL_ARGUMENTS.get(tool_name, ())


def annotate_schema_with_secrets(
    tool_name: str,
    parameters: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Stamp :data:`SECRET_SCHEMA_KEY` onto every secret-bearing property.

    Lets a consumer discover the secret argument from the parameter schema it
    already holds, without importing anything from bridgic:

        prop = tool.parameters["properties"]["text"]
        if prop.get("x-bridgic-secret"):
            ...

    Parameters
    ----------
    tool_name : str
        SDK method name of the tool. Unknown names annotate nothing.
    parameters : Optional[Dict[str, Any]]
        A JSON Schema object with a ``properties`` mapping. Returned unchanged
        (well, unannotated) when it has none.

    Returns
    -------
    Optional[Dict[str, Any]]
        An annotated copy when there was something to annotate, otherwise the
        input object itself. The input is never mutated, so a caller-supplied
        schema is safe to pass and safe to reuse.
    """
    rules = secret_argument_rules(tool_name)
    if not rules or not isinstance(parameters, dict):
        return parameters

    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return parameters

    applicable = [r for r in rules if isinstance(properties.get(r.value_param), dict)]
    if not applicable:
        return parameters

    annotated = dict(parameters)
    annotated["properties"] = dict(properties)
    for rule in applicable:
        prop = dict(properties[rule.value_param])
        prop[SECRET_SCHEMA_KEY] = rule.as_schema_marker()
        annotated["properties"][rule.value_param] = prop
    return annotated


def redact_tool_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return a copy of ``arguments`` with secret values replaced by :data:`REDACTED`.

    Parameters
    ----------
    tool_name : str
        SDK method name of the tool being called (e.g. ``"input_text_by_ref"``).
        Unknown names redact nothing.
    arguments : Mapping[str, Any]
        The arguments the tool was called with.

    Returns
    -------
    Dict[str, Any]
        A shallow copy, with only the values the call marked secret masked. The
        gate flag itself is left in place so the record still shows *why* a
        value was masked. Nested field dicts are copied rather than mutated, so
        the caller's own structures are never modified.

    Examples
    --------
    >>> redact_tool_arguments("type_text", {"text": "hunter2", "is_secret": True})
    {'text': '***', 'is_secret': True}
    >>> redact_tool_arguments("type_text", {"text": "hello", "is_secret": False})
    {'text': 'hello', 'is_secret': False}
    """
    if not isinstance(arguments, Mapping):
        return arguments  # type: ignore[return-value]

    redacted: Dict[str, Any] = dict(arguments)
    for rule in secret_argument_rules(tool_name):
        if rule.value_param not in redacted:
            continue

        call_gated = rule.gate_param is None or bool(redacted.get(rule.gate_param))
        value = redacted[rule.value_param]

        if rule.item_value_key is None:
            if call_gated:
                redacted[rule.value_param] = REDACTED
            continue

        redacted[rule.value_param] = _redact_items(value, rule, call_gated)

    return redacted


def _redact_items(value: Any, rule: SecretArgumentRule, call_gated: bool) -> Any:
    """Redact secret values inside a list-of-dicts argument.

    Also accepts the JSON-string form the CLI transports (``fill-form`` passes
    ``fields`` as a JSON string), redacting inside it and re-serializing so the
    record keeps the shape the tool was actually called with.
    """
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            # Unparseable: mask wholesale rather than risk passing a secret
            # through, but only when something signalled one is in there.
            if call_gated or (rule.item_gate_key and rule.item_gate_key in value):
                return REDACTED
            return value
        return json.dumps(_redact_items(parsed, rule, call_gated))

    if not isinstance(value, (list, tuple)):
        return value

    items = []
    for item in value:
        if not isinstance(item, Mapping) or rule.item_value_key not in item:
            items.append(item)
            continue
        item_gated = call_gated or (
            rule.item_gate_key is not None and bool(item.get(rule.item_gate_key))
        )
        if not item_gated:
            items.append(dict(item))
            continue
        masked = dict(item)
        masked[rule.item_value_key] = REDACTED
        items.append(masked)

    return items if isinstance(value, list) else tuple(items)


def scrub_secrets(message: str, *secrets: Optional[str]) -> str:
    """Replace every occurrence of each secret in ``message`` with :data:`REDACTED`.

    Used on diagnostic strings bridgic did not compose itself - chiefly
    Playwright exception text, which can echo back the value it was handed.
    Secrets shorter than :data:`_MIN_SCRUB_LENGTH` characters are left alone;
    see that constant for why.

    Parameters
    ----------
    message : str
        Text about to be logged or returned.
    *secrets : Optional[str]
        Values to remove. ``None`` and empty strings are ignored, so callers can
        pass a value straight through without pre-checking it.

    Returns
    -------
    str
        ``message`` with the secrets substituted out.
    """
    if not message:
        return message
    for secret in secrets:
        if secret and len(secret) >= _MIN_SCRUB_LENGTH and secret in message:
            message = message.replace(secret, REDACTED)
    return message


def secret_field_values(fields: Any) -> Tuple[str, ...]:
    """Collect the values of every ``fill_form`` field marked ``is_secret``.

    Parameters
    ----------
    fields : Any
        The ``fields`` argument as passed to
        :meth:`~bridgic.browser.Browser.fill_form`. Non-list input yields an
        empty tuple.

    Returns
    -------
    Tuple[str, ...]
        Values suitable for passing to :func:`scrub_secrets`.
    """
    if not isinstance(fields, (list, tuple)):
        return ()
    values = []
    for field in fields:
        if isinstance(field, Mapping) and field.get("is_secret"):
            value = field.get("value")
            if isinstance(value, str) and value:
                values.append(value)
    return tuple(values)


__all__ = [
    "REDACTED",
    "SECRET_SCHEMA_KEY",
    "SECRET_TOOL_ARGUMENTS",
    "SecretArgumentRule",
    "annotate_schema_with_secrets",
    "redact_tool_arguments",
    "scrub_secrets",
    "secret_argument_rules",
    "secret_field_values",
]
