import inspect
import types
from enum import Enum
from typing import (
    Annotated,
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Union,
    get_args,
    get_origin,
)

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined


class McpSchemaResolver:
    @staticmethod
    def _json_schema_type_for_values(values: list[Any]) -> Optional[str]:
        if all(value is None for value in values):
            return "null"
        if all(isinstance(value, bool) for value in values):
            return "boolean"
        if all(
            isinstance(value, int) and not isinstance(value, bool) for value in values
        ):
            return "integer"
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in values
        ):
            return "number"
        if all(isinstance(value, str) for value in values):
            return "string"
        return None

    @staticmethod
    def _json_enum_value(value: Any) -> Any:
        if isinstance(value, Enum):
            value = value.value
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise TypeError(
            f"{McpSchemaResolver.__name__} enum values must be JSON primitives."
        )

    @staticmethod
    def _values_to_enum_schema(values: tuple[Any, ...]) -> Dict[str, Any]:
        enum_values = [McpSchemaResolver._json_enum_value(value) for value in values]
        schema: Dict[str, Any] = {"enum": enum_values}
        json_type = McpSchemaResolver._json_schema_type_for_values(enum_values)
        if json_type is not None:
            schema["type"] = json_type
        return schema

    @staticmethod
    def _unwrap_annotated(annotation) -> tuple[Any, tuple[Any, ...]]:
        if get_origin(annotation) is Annotated:
            args = get_args(annotation)
            return args[0], args[1:]
        return annotation, ()

    @staticmethod
    def _apply_schema_metadata(
        schema: Dict[str, Any],
        metadata: tuple[Any, ...],
        format_map: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not metadata:
            return schema

        enriched = dict(schema)
        for item in metadata:
            if isinstance(item, FieldInfo):
                if item.title is not None:
                    enriched["title"] = item.title
                if item.description is not None:
                    enriched["description"] = item.description
                if not item.is_required() and item.default is not PydanticUndefined:
                    enriched["default"] = item.default
                if isinstance(item.json_schema_extra, dict):
                    enriched.update(item.json_schema_extra)
            elif isinstance(item, dict):
                enriched.update(item)

        if isinstance(enriched.get("title"), str):
            enriched["title"] = enriched["title"].format_map(format_map or {})
        if isinstance(enriched.get("description"), str):
            enriched["description"] = enriched["description"].format_map(
                format_map or {}
            )
        return enriched

    @staticmethod
    def _is_optional(annotation) -> bool:
        annotation, _ = McpSchemaResolver._unwrap_annotated(annotation)
        origin = get_origin(annotation)
        return origin in (Union, types.UnionType) and type(None) in get_args(annotation)

    @staticmethod
    def _remove_optional(annotation):
        if McpSchemaResolver._is_optional(annotation):
            args = tuple(arg for arg in get_args(annotation) if arg is not type(None))
            if len(args) == 1:
                return args[0]
        return annotation

    @staticmethod
    def _annotation_to_schema(
        annotation, format_map: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        annotation, metadata = McpSchemaResolver._unwrap_annotated(annotation)
        annotation = McpSchemaResolver._remove_optional(annotation)
        annotation, inner_metadata = McpSchemaResolver._unwrap_annotated(annotation)
        metadata = inner_metadata + metadata

        if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
            return McpSchemaResolver._apply_schema_metadata(
                annotation.model_json_schema(), metadata, format_map
            )

        if inspect.isclass(annotation) and issubclass(annotation, Enum):
            return McpSchemaResolver._apply_schema_metadata(
                McpSchemaResolver._values_to_enum_schema(
                    tuple(member.value for member in annotation)
                ),
                metadata,
                format_map,
            )

        if annotation is int:
            return McpSchemaResolver._apply_schema_metadata(
                {"type": "integer"}, metadata, format_map
            )
        if annotation is str:
            return McpSchemaResolver._apply_schema_metadata(
                {"type": "string"}, metadata, format_map
            )
        if annotation is bool:
            return McpSchemaResolver._apply_schema_metadata(
                {"type": "boolean"}, metadata, format_map
            )
        if annotation is float:
            return McpSchemaResolver._apply_schema_metadata(
                {"type": "number"}, metadata, format_map
            )

        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin is Literal:
            return McpSchemaResolver._apply_schema_metadata(
                McpSchemaResolver._values_to_enum_schema(args),
                metadata,
                format_map,
            )

        if origin in (list, List):
            if not args:
                raise ValueError(
                    f"{McpSchemaResolver.__name__} list requires an item type."
                )
            return McpSchemaResolver._apply_schema_metadata(
                {
                    "type": "array",
                    "items": McpSchemaResolver._annotation_to_schema(
                        args[0], format_map
                    ),
                },
                metadata,
                format_map,
            )

        # dict[str, V]
        if origin in (dict, Dict):
            if len(args) != 2:
                raise ValueError(
                    f"{McpSchemaResolver.__name__} dict requires key and value types."
                )
            key, value = args
            if key is not str:
                raise ValueError(f"{McpSchemaResolver.__name__} dict keys must be str.")
            return McpSchemaResolver._apply_schema_metadata(
                {
                    "type": "object",
                    "additionalProperties": McpSchemaResolver._annotation_to_schema(
                        value, format_map
                    ),
                },
                metadata,
                format_map,
            )

        if origin in (Union, types.UnionType):
            raise TypeError(
                f"{McpSchemaResolver.__name__} unsupported annotation: generic 'Union'."
            )

        if annotation is Any:
            raise TypeError(
                f"{McpSchemaResolver.__name__} unsupported annotation: 'Any' for inputs."
            )

        if annotation in (tuple, set, Ellipsis):
            raise TypeError(
                f"{McpSchemaResolver.__name__} unsupported annotation: tuple, set, or ellipsis."
            )

        raise TypeError(
            f"{McpSchemaResolver.__name__} unsupported annotation: {annotation!r}"
        )

    @staticmethod
    def _clean_signature(target):
        if inspect.ismethod(target):
            func = target.__func__
            sig = inspect.signature(func)
            # remove 'self' or 'cls'
            params = list(sig.parameters.values())[1:]
            return sig.replace(parameters=params)

        if isinstance(target, (staticmethod, classmethod)):
            return inspect.signature(target.__func__)

        return inspect.signature(target)

    @staticmethod
    def _is_param_required(param: inspect.Parameter, annotation) -> bool:
        return (param.default is inspect._empty) and (
            not McpSchemaResolver._is_optional(annotation)
        )

    @staticmethod
    def _extract_function_name(target) -> str:
        if isinstance(target, (staticmethod, classmethod)):
            target = target.__func__
        if inspect.ismethod(target):
            return target.__func__.__name__

        if inspect.isfunction(target):
            return target.__name__

        if hasattr(target, "__name__"):
            return target.__name__

        raise TypeError(
            f"{McpSchemaResolver.__name__} unsupported target type: {type(target)!r}"
        )

    @staticmethod
    def resolve(
        function,
        format_map: Optional[Dict[str, Any]] = None,
        skip_mcp_tool_output_schema: bool = False,
    ) -> tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]:
        signature = McpSchemaResolver._clean_signature(function)

        properties: Dict[str, Any] = {}
        required: List[str] = []

        for name, parameter in signature.parameters.items():
            if parameter.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                raise TypeError(
                    f"{McpSchemaResolver.__name__} unsupported parameter: *args/**kwargs."
                )
            if parameter.annotation is inspect._empty:
                raise TypeError(
                    f"{McpSchemaResolver.__name__} missing type hint for parameter '{name}'."
                )

            properties[name] = McpSchemaResolver._annotation_to_schema(
                parameter.annotation, format_map
            )
            if McpSchemaResolver._is_param_required(parameter, parameter.annotation):
                required.append(name)

        input_schema: Dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required

        ret = signature.return_annotation
        if (
            skip_mcp_tool_output_schema
            or ret is inspect._empty
            or ret is Any
            or ret is None
        ):
            output_schema = None
        else:
            if ret in (tuple, set, Ellipsis):
                raise TypeError(
                    f"{McpSchemaResolver.__name__} unsupported return type: tuple, set, or ellipsis."
                )
            core = McpSchemaResolver._remove_optional(ret)
            output_schema = McpSchemaResolver._annotation_to_schema(core, format_map)

        func_name = McpSchemaResolver._extract_function_name(function)
        return func_name, input_schema, output_schema
