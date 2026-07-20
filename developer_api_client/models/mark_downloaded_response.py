from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="MarkDownloadedResponse")


@_attrs_define
class MarkDownloadedResponse:
    """Результат отметки файлов.

    Attributes:
        marked_now (int): Сколько файлов отмечено скачанными впервые этим запросом.
        already_marked (int): Сколько из переданных файлов уже были отмечены ранее.
    """

    marked_now: int
    already_marked: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        marked_now = self.marked_now

        already_marked = self.already_marked

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "marked_now": marked_now,
                "already_marked": already_marked,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        marked_now = d.pop("marked_now")

        already_marked = d.pop("already_marked")

        mark_downloaded_response = cls(
            marked_now=marked_now,
            already_marked=already_marked,
        )

        mark_downloaded_response.additional_properties = d
        return mark_downloaded_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
