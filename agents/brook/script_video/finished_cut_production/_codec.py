"""Structure-driven JSON codec for this module's frozen records.

每一個 record 的欄位名稱與型別註記已經把「怎麼存、怎麼讀」講完了。以前 `_store`
把同一份資訊再抄一次——一個 `_to_dict` 抄欄位名、一個 `_from_dict` 抄欄位名加
`str()`／`float()`／`cast()`——於是加一個欄位要改三個地方，漏掉一個不會有人發現，
直到某天 round-trip 少了一格。

這支只有一條規則：**欄位的型別註記就是 schema**。支援的形狀刻意窄，窄到讀得完：

* `str` / `int` / `float` / `bool`
* `X | None`，以及純量聯集（`ProbeValue` 那種）
* `Literal[...]`（含 `Status`、`ComponentLane` 這些別名）——值不在名單內就擋
* `tuple[X, ...]` 與定長 `tuple[X, Y]`
* `Enum`（存 `.value`）
* 巢狀 frozen dataclass

不支援的形狀會當場 `RecordCodecError`，不會靜默放過——這比「悄悄存成 null」好。

規則之外的個案（退役投影要走 mint、tagged union、衍生欄位不落盤）用
`register()` 掛自訂函式，遞迴時會自動走那一份，所以巢狀的 record 也吃得到。
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, Literal, TypeVar, get_args, get_origin, get_type_hints

_T = TypeVar("_T")

_NONE_TYPE = type(None)
_SCALAR_TYPES = (str, int, float, bool)


class RecordCodecError(ValueError):
    """A persisted payload does not match the record it claims to be."""


class RecordCodec:
    """Dump and load frozen dataclasses from their own field annotations."""

    def __init__(self, *, localns: Mapping[str, object] | None = None) -> None:
        #: `get_type_hints` 解不到只在 `TYPE_CHECKING` 下匯入的名字（例如
        #: `_PreReleaseCorrection`），呼叫端把那些名字交進來。
        self._localns = dict(localns or {})
        self._hints: dict[type, dict[str, Any]] = {}
        self._dumpers: dict[type, Callable[[Any], object]] = {}
        self._loaders: dict[type, Callable[[object], Any]] = {}

    def register(
        self,
        cls: type,
        *,
        dump: Callable[[Any], object] | None = None,
        load: Callable[[object], Any] | None = None,
    ) -> None:
        """Route one record through a bespoke codec, including when nested."""

        if dump is not None:
            self._dumpers[cls] = dump
        if load is not None:
            self._loaders[cls] = load

    # -- dump ------------------------------------------------------------
    def dump(self, value: object) -> object:
        """Dump any supported value, honouring registered dumpers."""

        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (tuple, list)):
            return [self.dump(item) for item in value]
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            dumper = self._dumpers.get(type(value))
            if dumper is not None:
                return dumper(value)
            return self.dump_record(value)
        return value

    def dump_record(
        self,
        record: object,
        *,
        exclude: frozenset[str] = frozenset(),
        overrides: Mapping[str, object] | None = None,
    ) -> dict:
        """Dump a record structurally, without consulting its own registered dumper."""

        supplied = overrides or {}
        return {
            field.name: (
                supplied[field.name]
                if field.name in supplied
                else self.dump(getattr(record, field.name))
            )
            for field in dataclasses.fields(record)  # type: ignore[arg-type]
            if field.name not in exclude
        }

    # -- load ------------------------------------------------------------
    def load(self, cls: type[_T], value: object) -> _T:
        """Load a record, honouring registered loaders."""

        loader = self._loaders.get(cls)
        if loader is not None:
            return typing.cast(_T, loader(value))
        return self.load_record(cls, value)

    def load_record(
        self,
        cls: type[_T],
        value: object,
        *,
        exclude: frozenset[str] = frozenset(),
        factory: Callable[..., _T] | None = None,
        presets: Mapping[str, object] | None = None,
    ) -> _T:
        """Load a record structurally, without consulting its own registered loader.

        `presets` 是給「這個欄位結構描述不了」用的逃生口（tagged union）：值已經由
        呼叫端建好，直接放進去，不再讀 payload 裡的同名鍵。
        """

        if not isinstance(value, dict):
            raise RecordCodecError(f"{cls.__name__} payload is not an object")
        hints = self._hints_for(cls)
        values: dict[str, object] = dict(presets or {})
        for field in dataclasses.fields(cls):  # type: ignore[arg-type]
            if not field.init or field.name in exclude or field.name in values:
                continue
            optional = (
                field.default is not dataclasses.MISSING
                or field.default_factory is not dataclasses.MISSING
            )
            if field.name not in value:
                if optional:
                    continue
                raise RecordCodecError(f"{cls.__name__} payload is missing {field.name!r}")
            raw = value[field.name]
            annotation = hints[field.name]
            if raw is None and optional and not _admits_none(annotation):
                # 舊檔把「還沒有這個欄位」寫成 null。欄位自己帶預設就用預設，
                # 不要硬轉成 "None" 這種字串——那是會一路存回磁碟的髒資料。
                continue
            values[field.name] = self._load_value(annotation, raw, f"{cls.__name__}.{field.name}")
        return (factory or cls)(**values)

    # -- internals -------------------------------------------------------
    def _hints_for(self, cls: type) -> dict[str, Any]:
        cached = self._hints.get(cls)
        if cached is None:
            cached = get_type_hints(cls, localns=self._localns)
            self._hints[cls] = cached
        return cached

    def _load_value(self, annotation: Any, raw: object, path: str) -> object:
        origin = get_origin(annotation)
        if origin is types.UnionType or origin is typing.Union:
            return self._load_union(annotation, raw, path)
        if origin is Literal:
            choices = get_args(annotation)
            if raw not in choices:
                raise RecordCodecError(f"{path} is not one of {choices!r}")
            return raw
        if origin is tuple:
            return self._load_tuple(annotation, raw, path)
        if isinstance(annotation, type):
            if issubclass(annotation, Enum):
                try:
                    return annotation(raw)
                except ValueError as error:
                    raise RecordCodecError(f"{path} is not a {annotation.__name__}") from error
            if dataclasses.is_dataclass(annotation):
                return self.load(annotation, raw)
            if annotation is bool:
                # `bool(raw)` 對 dict／list／字串一律給得出答案（`{}` 是 False、
                # `"false"` 是 True），於是壞掉的 payload 會靜靜變成一個看起來合理
                # 的布林值。這支的承諾是「不支援的形狀當場報錯」，布林不能是唯一
                # 的例外。
                if not isinstance(raw, bool):
                    raise RecordCodecError(f"{path} is not a bool")
                return raw
            if annotation in (str, int, float):
                if raw is None or isinstance(raw, (dict, list, tuple)):
                    raise RecordCodecError(f"{path} is not a {annotation.__name__}")
                try:
                    return annotation(raw)  # type: ignore[call-arg]
                except (TypeError, ValueError) as error:
                    raise RecordCodecError(f"{path} is not a {annotation.__name__}") from error
        raise RecordCodecError(f"{path} has an annotation this codec does not persist")

    def _load_union(self, annotation: Any, raw: object, path: str) -> object:
        members = get_args(annotation)
        if raw is None:
            if _NONE_TYPE in members:
                return None
            raise RecordCodecError(f"{path} is not optional")
        concrete = tuple(member for member in members if member is not _NONE_TYPE)
        if len(concrete) == 1:
            return self._load_value(concrete[0], raw, path)
        # 純量聯集（`ProbeValue`）沒有判別依據，也不需要——JSON 已經把型別帶著了。
        if all(member in _SCALAR_TYPES for member in concrete) and isinstance(raw, _SCALAR_TYPES):
            return raw
        raise RecordCodecError(f"{path} has an ambiguous union this codec cannot load")

    def _load_tuple(self, annotation: Any, raw: object, path: str) -> tuple:
        if not isinstance(raw, (list, tuple)):
            raise RecordCodecError(f"{path} is not a sequence")
        members = get_args(annotation)
        if len(members) == 2 and members[1] is Ellipsis:
            return tuple(
                self._load_value(members[0], item, f"{path}[{index}]")
                for index, item in enumerate(raw)
            )
        if len(members) != len(raw):
            raise RecordCodecError(f"{path} needs exactly {len(members)} entries")
        return tuple(
            self._load_value(member, item, f"{path}[{index}]")
            for index, (member, item) in enumerate(zip(members, raw))
        )


def _admits_none(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        return _NONE_TYPE in get_args(annotation)
    return annotation is None or annotation is _NONE_TYPE


__all__ = ["RecordCodec", "RecordCodecError"]
