from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class VoidParserItem:
    item_title: str = ""
    item_photo: str = ""
    ads_number: int = 0
    parser_views: int = 0
    ads_number_bought: int = 0
    ads_number_sold: int = 0
    gender: str = ""
    email: str = ""
    person_reg_date: str = ""
    item_price: str = ""
    views: int | None = None
    rating: int = 0
    created_date: str = ""
    created_real_date: str = ""
    phone: str = ""
    item_desc: str = ""
    location: str = ""
    item_link: str = ""
    person_link: str = ""
    item_person_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoidParserResult:
    items: list[VoidParserItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items]}
