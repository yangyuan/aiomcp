import inspect
import types
from typing import Any, Dict, List, Optional, Union, get_args, get_origin

from pydantic import BaseModel


class McpSchemaResolver:

    @staticmethod
    def _is_optional(annotation) -> bool:
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
    def _annotation_to_schema(annotation) -> Dict[str, Any]:
        annotation = McpSchemaResolver._remove_optional(annotation)

        if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
            return annotation.model_json_schema()

        if annotation is int:
            return {"type": "integer"}
        if annotation is str:
            return {"type": "string"}
        if annotation is bool:
            return {"type": "boolean"}
        if annotation is float:
            return {"type": "number"}

        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin in (list, List):
            if not args:
                raise ValueError(
                    f"{McpSchemaResolver.__name__} list requires an item type."
                )
            return {
                "type": "array",
                "items": McpSchemaResolver._annotation_to_schema(args[0]),
            }

        # dict[str, V]
        if origin in (dict, Dict):
            if len(args) != 2:
                raise ValueError(
                    f"{McpSchemaResolver.__name__} dict requires key and value types."
                )
            key, value = args
            if key is not str:
                raise ValueError(f"{McpSchemaResolver.__name__} dict keys must be str.")
            return {
                "type": "object",
                "additionalProperties": McpSchemaResolver._annotation_to_schema(value),
            }

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
    def resolve(function) -> tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]:
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
                parameter.annotation
            )
            if McpSchemaResolver._is_param_required(parameter, parameter.annotation):
                required.append(name)

        input_schema: Dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required

        ret = signature.return_annotation
        if ret is inspect._empty or ret is Any or ret is None:
            output_schema = None
        else:
            if ret in (tuple, set, Ellipsis):
                raise TypeError(
                    f"{McpSchemaResolver.__name__} unsupported return type: tuple, set, or ellipsis."
                )
            core = McpSchemaResolver._remove_optional(ret)
            output_schema = McpSchemaResolver._annotation_to_schema(core)

        func_name = McpSchemaResolver._extract_function_name(function)
        return func_name, input_schema, output_schema
