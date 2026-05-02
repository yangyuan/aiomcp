from enum import StrEnum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel
from pydantic import ConfigDict


class JsonSchemaType(StrEnum):
    OBJECT = "object"
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ARRAY = "array"
    NULL = "null"


class JsonSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Optional[Union[JsonSchemaType, List[JsonSchemaType]]] = None
    items: Optional[Union["JsonSchema", List["JsonSchema"]]] = None
    anyOf: Optional[List["JsonSchema"]] = None
    allOf: Optional[List["JsonSchema"]] = None
    oneOf: Optional[List["JsonSchema"]] = None
    additionalProperties: Optional[Union[bool, "JsonSchema"]] = None
    properties: Optional[Dict[str, "JsonSchema"]] = None
    enum: Optional[List[Any]] = None
    const: Optional[Any] = None
    title: Optional[str] = None
    description: Optional[str] = None
    default: Optional[Any] = None
    required: Optional[List[str]] = None
