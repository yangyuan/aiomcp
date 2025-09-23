import typing
import pytest
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel

from aiomcp.mcp_schema_resolver import McpSchemaResolver


class Point(BaseModel):
    x: int
    y: int


def test_resolve_primitives_and_required():
    def f(a: int, b: str, c: bool) -> int:
        return 0

    _, input_schema, output_schema = McpSchemaResolver.resolve(f)

    assert input_schema["type"] == "object"
    assert set(input_schema["properties"].keys()) == {"a", "b", "c"}
    assert input_schema["properties"]["a"] == {"type": "integer"}
    assert input_schema["properties"]["b"] == {"type": "string"}
    assert input_schema["properties"]["c"] == {"type": "boolean"}
    assert set(input_schema.get("required", [])) == {"a", "b", "c"}

    assert output_schema == {"type": "integer"}


def test_optional_and_defaults_affect_required():
    def f(
        a: Optional[int], b: int | None, c: str = "x", d: Optional[str] = None
    ) -> None:
        return None

    _, input_schema, output_schema = McpSchemaResolver.resolve(f)

    assert input_schema["type"] == "object"
    # Optional/default params should not be required
    assert "required" not in input_schema or input_schema["required"] == []
    assert input_schema["properties"]["a"] == {"type": "integer"}
    assert input_schema["properties"]["b"] == {"type": "integer"}
    assert input_schema["properties"]["c"] == {"type": "string"}
    assert input_schema["properties"]["d"] == {"type": "string"}

    assert output_schema is None


def test_list_and_dict_mapping():
    def f(
        a: list[int], b: List[str], c: dict[str, int], d: Dict[str, List[int]]
    ) -> None:
        return None

    _, input_schema, output_schema = McpSchemaResolver.resolve(f)

    props = input_schema["properties"]
    assert props["a"] == {"type": "array", "items": {"type": "integer"}}
    assert props["b"] == {"type": "array", "items": {"type": "string"}}
    assert props["c"] == {"type": "object", "additionalProperties": {"type": "integer"}}
    assert props["d"] == {
        "type": "object",
        "additionalProperties": {"type": "array", "items": {"type": "integer"}},
    }
    assert output_schema is None


def test_return_schema_handling():
    def g1() -> None:  # no output schema
        return None

    def g2() -> Any:  # Any also means no output schema
        return 1

    def g3() -> Optional[int]:  # Optional[T] returns T's schema
        return 1

    def g4() -> Point:  # Pydantic model as return
        return Point(x=0, y=0)

    assert McpSchemaResolver.resolve(g1)[2] is None
    assert McpSchemaResolver.resolve(g2)[2] is None
    assert McpSchemaResolver.resolve(g3)[2] == {"type": "integer"}

    out_schema = McpSchemaResolver.resolve(g4)[2]
    assert isinstance(out_schema, dict)
    assert out_schema.get("type") == "object"
    assert "properties" in out_schema and set(out_schema["properties"].keys()) == {
        "x",
        "y",
    }


def test_clean_signature_for_methods_and_staticmethods():
    class C:
        def inst(self, a: int) -> int:
            return a

        @staticmethod
        def stat(a: int) -> int:
            return a

        @classmethod
        def cls(cls, a: int) -> int:
            return a

    c = C()

    _, in_schema, out_schema = McpSchemaResolver.resolve(c.inst)
    assert set(in_schema["properties"].keys()) == {"a"}
    assert set(in_schema.get("required", [])) == {"a"}
    assert out_schema == {"type": "integer"}

    _, in_schema, out_schema = McpSchemaResolver.resolve(C.stat)
    assert set(in_schema["properties"].keys()) == {"a"}

    _, in_schema, _ = McpSchemaResolver.resolve(C.cls)
    assert set(in_schema["properties"].keys()) == {"a"}


def test_pydantic_model_param_and_return():
    def f(p: Point) -> Point:
        return p

    _, input_schema, output_schema = McpSchemaResolver.resolve(f)

    p_schema = input_schema["properties"]["p"]
    assert isinstance(p_schema, dict)
    assert p_schema.get("type") == "object"
    assert set(p_schema.get("properties", {}).keys()) == {"x", "y"}

    assert isinstance(output_schema, dict)
    assert set(output_schema.get("properties", {}).keys()) == {"x", "y"}


def test_list_of_point_param_and_dict_point_return():
    def f(points: List[Point]) -> Dict[str, Point]:
        return {}

    _, input_schema, output_schema = McpSchemaResolver.resolve(f)

    # Input: points is array of Point schema
    pts = input_schema["properties"]["points"]
    assert pts["type"] == "array"
    assert pts["items"]["type"] == "object"
    assert set(pts["items"]["properties"].keys()) == {"x", "y"}

    # Output: object with additionalProperties of Point schema
    assert output_schema["type"] == "object"
    ap = output_schema["additionalProperties"]
    assert ap["type"] == "object"
    assert set(ap["properties"].keys()) == {"x", "y"}


def test_dict_point_param_and_list_point_return():
    def f(mapping: Dict[str, Point]) -> List[Point]:
        return []

    _, input_schema, output_schema = McpSchemaResolver.resolve(f)

    mp = input_schema["properties"]["mapping"]
    assert mp["type"] == "object"
    ap = mp["additionalProperties"]
    assert ap["type"] == "object"
    assert set(ap["properties"].keys()) == {"x", "y"}

    # Output: array of Point
    assert output_schema["type"] == "array"
    it = output_schema["items"]
    assert it["type"] == "object"
    assert set(it["properties"].keys()) == {"x", "y"}


# -------------------------
# Negative cases
# -------------------------


def test_invalid_list_unsubscripted_is_unsupported():
    def f(a: list):
        return None

    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f)


def test_invalid_dict_unsubscripted_is_unsupported():
    def f(a: dict):
        return None

    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f)


def test_invalid_dict_key_must_be_str():
    def f(a: dict[int, int]):
        return None

    with pytest.raises(ValueError):
        McpSchemaResolver.resolve(f)


def test_union_and_any_rejected_for_inputs():
    def f1(a: Union[int, str]):
        return None

    def f2(a: int | str):
        return None

    def f3(a: Any):
        return None

    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f1)
    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f2)
    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f3)


def test_tuple_set_rejected_for_inputs():
    def f1(a: tuple):
        return None

    def f2(a: set):
        return None

    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f1)
    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f2)


def test_missing_annotation_and_varargs_kwargs():
    def f1(a, b: int):
        return None

    def f2(*args):
        return None

    def f3(**kwargs):
        return None

    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f1)
    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f2)
    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f3)


def test_return_tuple_set_ellipsis_rejected():
    def f1() -> tuple:
        return ()

    def f2() -> set:
        return set()

    def f3() -> ...:  # Ellipsis return
        return ...

    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f1)
    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f2)
    with pytest.raises(TypeError):
        McpSchemaResolver.resolve(f3)
