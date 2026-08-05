"""Unit tests for the secret-argument contract (`bridgic/browser/_secrets.py`).

Covers the two halves of the promise `is_secret` makes:

1. ``redact_tool_arguments`` masks exactly the values a call marked secret and
   nothing else, for every tool listed in ``SECRET_TOOL_ARGUMENTS``.
2. ``scrub_secrets`` removes a value from text bridgic did not compose itself.
"""
from __future__ import annotations

import json

from bridgic.browser import (
    REDACTED,
    SECRET_SCHEMA_KEY,
    SECRET_TOOL_ARGUMENTS,
    SecretArgumentRule,
    annotate_schema_with_secrets,
    redact_tool_arguments,
    scrub_secrets,
    secret_argument_rules,
)
from bridgic.browser._secrets import secret_field_values
from bridgic.browser.tools._browser_tool_spec import BrowserToolSpec


# ==================== redact_tool_arguments ====================

class TestRedactToolArguments:
    """The gate flag decides; nothing else in the record changes."""

    def test_input_text_by_ref_masks_gated_text(self):
        out = redact_tool_arguments(
            "input_text_by_ref",
            {"ref": "8d4b03a9", "text": "hunter2", "is_secret": True},
        )
        assert out == {"ref": "8d4b03a9", "text": REDACTED, "is_secret": True}

    def test_input_text_by_ref_leaves_ungated_text(self):
        args = {"ref": "8d4b03a9", "text": "alice@example.com", "is_secret": False}
        assert redact_tool_arguments("input_text_by_ref", args) == args

    def test_missing_gate_is_treated_as_not_secret(self):
        args = {"ref": "8d4b03a9", "text": "plain"}
        assert redact_tool_arguments("input_text_by_ref", args) == args

    def test_type_text_masks_gated_text(self):
        out = redact_tool_arguments("type_text", {"text": "hunter2", "is_secret": True})
        assert out == {"text": REDACTED, "is_secret": True}

    def test_unknown_tool_is_returned_unchanged(self):
        args = {"ref": "8d4b03a9", "text": "hunter2", "is_secret": True}
        assert redact_tool_arguments("click_element_by_ref", args) == args

    def test_input_is_never_mutated(self):
        args = {"text": "hunter2", "is_secret": True}
        redact_tool_arguments("type_text", args)
        assert args["text"] == "hunter2"

    def test_non_mapping_arguments_pass_through(self):
        assert redact_tool_arguments("type_text", None) is None  # type: ignore[arg-type]

    def test_every_declared_tool_redacts_its_value_param(self):
        """No rule can silently stop firing - each one is exercised by name."""
        for tool_name, rules in SECRET_TOOL_ARGUMENTS.items():
            for rule in rules:
                assert rule.gate_param, f"{tool_name}.{rule.value_param} has no gate"
                if rule.item_value_key is None:
                    args = {rule.value_param: "s3cret", rule.gate_param: True}
                    out = redact_tool_arguments(tool_name, args)
                    assert out[rule.value_param] == REDACTED, tool_name
                else:
                    args = {
                        rule.value_param: [{"ref": "e1", rule.item_value_key: "s3cret"}],
                        rule.gate_param: True,
                    }
                    out = redact_tool_arguments(tool_name, args)
                    assert out[rule.value_param][0][rule.item_value_key] == REDACTED, tool_name


class TestRedactFillFormFields:
    """`fill_form` carries its values inside a list of dicts."""

    def test_call_level_flag_masks_every_field(self):
        out = redact_tool_arguments("fill_form", {
            "fields": [
                {"ref": "e1", "value": "alice"},
                {"ref": "e2", "value": "hunter2"},
            ],
            "is_secret": True,
        })
        assert [f["value"] for f in out["fields"]] == [REDACTED, REDACTED]
        assert [f["ref"] for f in out["fields"]] == ["e1", "e2"]

    def test_per_field_flag_masks_only_that_field(self):
        out = redact_tool_arguments("fill_form", {
            "fields": [
                {"ref": "e1", "value": "alice"},
                {"ref": "e2", "value": "hunter2", "is_secret": True},
            ],
        })
        assert out["fields"][0]["value"] == "alice"
        assert out["fields"][1]["value"] == REDACTED

    def test_no_flag_leaves_all_fields_readable(self):
        fields = [{"ref": "e1", "value": "alice"}, {"ref": "e2", "value": "bob"}]
        out = redact_tool_arguments("fill_form", {"fields": fields, "is_secret": False})
        assert out["fields"] == fields

    def test_nested_field_dicts_are_not_mutated(self):
        fields = [{"ref": "e1", "value": "hunter2", "is_secret": True}]
        redact_tool_arguments("fill_form", {"fields": fields})
        assert fields[0]["value"] == "hunter2"

    def test_json_string_form_is_redacted_and_reserialized(self):
        """The CLI transports `fields` as a JSON string."""
        payload = json.dumps([{"ref": "e1", "value": "hunter2", "is_secret": True}])
        out = redact_tool_arguments("fill_form", {"fields": payload})
        assert "hunter2" not in out["fields"]
        assert json.loads(out["fields"])[0]["value"] == REDACTED

    def test_unparseable_json_is_masked_wholesale_when_gated(self):
        out = redact_tool_arguments(
            "fill_form", {"fields": '[{"ref": "e1", "value": "hunter2"', "is_secret": True}
        )
        assert out["fields"] == REDACTED

    def test_unparseable_json_is_kept_when_nothing_is_marked(self):
        payload = '[{"ref": "e1", "value": "alice"'
        out = redact_tool_arguments("fill_form", {"fields": payload})
        assert out["fields"] == payload

    def test_malformed_fields_do_not_raise(self):
        out = redact_tool_arguments(
            "fill_form", {"fields": ["not-a-dict", {"ref": "e1"}, None], "is_secret": True}
        )
        assert out["fields"] == ["not-a-dict", {"ref": "e1"}, None]


# ==================== scrub_secrets ====================

class TestScrubSecrets:
    def test_removes_every_occurrence(self):
        assert scrub_secrets("hunter2 then hunter2", "hunter2") == f"{REDACTED} then {REDACTED}"

    def test_ignores_none_and_empty(self):
        assert scrub_secrets("untouched", None, "") == "untouched"

    def test_leaves_short_values_alone(self):
        """Substituting a 1-2 char value would corrupt the message; see _MIN_SCRUB_LENGTH."""
        assert scrub_secrets("a banana", "a") == "a banana"

    def test_handles_multiple_secrets(self):
        out = scrub_secrets("user=alice pw=hunter2", "alice", "hunter2")
        assert "alice" not in out and "hunter2" not in out

    def test_empty_message_passes_through(self):
        assert scrub_secrets("", "hunter2") == ""


class TestSecretFieldValues:
    def test_collects_only_marked_values(self):
        values = secret_field_values([
            {"ref": "e1", "value": "alice"},
            {"ref": "e2", "value": "hunter2", "is_secret": True},
        ])
        assert values == ("hunter2",)

    def test_non_list_input_is_empty(self):
        assert secret_field_values(None) == ()
        assert secret_field_values("nope") == ()


# ==================== BrowserToolSpec surface ====================

class _StubBrowser:
    """Minimal stand-in - the spec only needs a callable with the right name."""

    async def input_text_by_ref(self, ref: str, text: str, is_secret: bool = False) -> str:
        return "ok"

    async def click_element_by_ref(self, ref: str) -> str:
        return "ok"


class TestToolSpecSecretSurface:
    def test_secret_arguments_names_the_gated_param(self):
        spec = BrowserToolSpec.from_raw(_StubBrowser().input_text_by_ref)
        rules = spec.secret_arguments
        assert [r.value_param for r in rules] == ["text"]
        assert rules[0].gate_param == "is_secret"

    def test_secret_arguments_empty_for_tools_without_secrets(self):
        spec = BrowserToolSpec.from_raw(_StubBrowser().click_element_by_ref)
        assert spec.secret_arguments == ()

    def test_redact_arguments_delegates_to_the_table(self):
        spec = BrowserToolSpec.from_raw(_StubBrowser().input_text_by_ref)
        out = spec.redact_arguments({"ref": "e1", "text": "hunter2", "is_secret": True})
        assert out == {"ref": "e1", "text": REDACTED, "is_secret": True}

    def test_dump_to_dict_lists_secret_arguments(self):
        spec = BrowserToolSpec.from_raw(_StubBrowser().input_text_by_ref)
        assert spec.dump_to_dict()["secret_arguments"] == ["text"]

    def test_dump_to_dict_omits_key_for_tools_without_secrets(self):
        spec = BrowserToolSpec.from_raw(_StubBrowser().click_element_by_ref)
        assert "secret_arguments" not in spec.dump_to_dict()

    def test_declared_tool_names_exist_on_browser(self):
        """The table keys must stay in sync with the real method names."""
        from bridgic.browser import Browser

        for tool_name in SECRET_TOOL_ARGUMENTS:
            assert callable(getattr(Browser, tool_name, None)), tool_name

    def test_declared_params_match_the_real_signatures(self):
        """A renamed parameter would silently disable redaction."""
        import inspect

        from bridgic.browser import Browser

        for tool_name, rules in SECRET_TOOL_ARGUMENTS.items():
            params = inspect.signature(getattr(Browser, tool_name)).parameters
            for rule in rules:
                assert rule.value_param in params, f"{tool_name}.{rule.value_param}"
                assert rule.gate_param in params, f"{tool_name}.{rule.gate_param}"

    def test_secret_argument_rules_is_the_same_table(self):
        assert secret_argument_rules("type_text") == SECRET_TOOL_ARGUMENTS["type_text"]
        assert secret_argument_rules("navigate_to") == ()


# ==================== x-bridgic-secret schema marker ====================

class TestSchemaMarker:
    """The marker is the import-free half of the contract: a framework that
    already walks a tool's parameter schema can find the secret without
    importing anything from bridgic."""

    def test_generated_spec_marks_the_secret_property(self):
        spec = BrowserToolSpec.from_raw(_StubBrowser().input_text_by_ref)
        prop = spec.tool_parameters["properties"]["text"]
        assert prop[SECRET_SCHEMA_KEY] == {"gated_by": "is_secret"}

    def test_marker_survives_into_the_llm_facing_tool(self):
        spec = BrowserToolSpec.from_raw(_StubBrowser().input_text_by_ref)
        prop = spec.to_tool().parameters["properties"]["text"]
        assert SECRET_SCHEMA_KEY in prop

    def test_non_secret_properties_are_untouched(self):
        spec = BrowserToolSpec.from_raw(_StubBrowser().input_text_by_ref)
        assert SECRET_SCHEMA_KEY not in spec.tool_parameters["properties"]["ref"]

    def test_tools_without_secrets_get_no_marker(self):
        spec = BrowserToolSpec.from_raw(_StubBrowser().click_element_by_ref)
        dumped = json.dumps(spec.tool_parameters)
        assert SECRET_SCHEMA_KEY not in dumped

    def test_secret_schema_key_property_matches_the_constant(self):
        spec = BrowserToolSpec.from_raw(_StubBrowser().click_element_by_ref)
        assert spec.secret_schema_key == SECRET_SCHEMA_KEY == "x-bridgic-secret"

    def test_fill_form_marker_describes_the_nested_shape(self):
        from bridgic.browser import Browser

        class _B(Browser):
            def __init__(self): pass

        spec = BrowserToolSpec.from_raw(_B().fill_form)
        assert spec.tool_parameters["properties"]["fields"][SECRET_SCHEMA_KEY] == {
            "gated_by": "is_secret",
            "item_value_key": "value",
            "item_gate_key": "is_secret",
        }

    def test_caller_supplied_schema_is_annotated_not_mutated(self):
        supplied = {"properties": {"text": {"type": "string"}}, "required": ["text"]}
        spec = BrowserToolSpec.from_raw(
            _StubBrowser().input_text_by_ref, tool_parameters=supplied
        )
        assert SECRET_SCHEMA_KEY in spec.tool_parameters["properties"]["text"]
        assert SECRET_SCHEMA_KEY not in supplied["properties"]["text"]

    def test_marker_is_truthy_so_the_naive_check_works(self):
        """`if prop.get("x-bridgic-secret"):` must fire for every declared rule."""
        for rules in SECRET_TOOL_ARGUMENTS.values():
            for rule in rules:
                assert rule.as_schema_marker(), rule

    def test_unconditional_rule_reports_gated_by_none(self):
        rule = SecretArgumentRule(value_param="token")
        assert rule.as_schema_marker() == {"gated_by": None}
        assert rule.as_schema_marker()  # still truthy

    def test_annotate_is_a_noop_for_unknown_tools(self):
        params = {"properties": {"text": {"type": "string"}}}
        assert annotate_schema_with_secrets("navigate_to", params) is params

    def test_annotate_tolerates_schemas_without_properties(self):
        assert annotate_schema_with_secrets("type_text", {}) == {}
        assert annotate_schema_with_secrets("type_text", None) is None

    def test_annotate_skips_rules_whose_property_is_absent(self):
        """`file`-style hidden params, or a trimmed schema, must not raise."""
        params = {"properties": {"ref": {"type": "string"}}}
        assert annotate_schema_with_secrets("input_text_by_ref", params) is params


# ==================== Public import surface ====================

class TestPublicImportSurface:
    """A project must be able to wire this up without touching a private name."""

    EXPECTED = {
        "REDACTED",
        "SECRET_SCHEMA_KEY",
        "SECRET_TOOL_ARGUMENTS",
        "SecretArgumentRule",
        "annotate_schema_with_secrets",
        "redact_tool_arguments",
        "scrub_secrets",
        "secret_argument_rules",
    }

    def test_exported_from_bridgic_browser(self):
        import bridgic.browser as pkg

        assert self.EXPECTED <= set(pkg.__all__)
        for name in self.EXPECTED:
            assert hasattr(pkg, name), name

    def test_exported_from_bridgic_browser_tools(self):
        """The subpackage a project already imports the builder from."""
        import bridgic.browser.tools as tools

        assert self.EXPECTED <= set(tools.__all__)
        for name in self.EXPECTED:
            assert hasattr(tools, name), name

    def test_both_paths_are_the_same_objects(self):
        import bridgic.browser as pkg
        import bridgic.browser.tools as tools

        for name in self.EXPECTED:
            assert getattr(pkg, name) is getattr(tools, name), name
