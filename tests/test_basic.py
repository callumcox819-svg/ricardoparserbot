from parser.formatter import format_price, rating_from_score, relative_time_ru
from parser.models import VoidParserItem, VoidParserResult


def test_void_parser_schema():
    item = VoidParserItem(
        item_title="Test",
        item_link="https://www.ricardo.ch/de/a/1/",
        item_person_name="seller",
    )
    payload = VoidParserResult(items=[item]).to_dict()
    assert "items" in payload
    assert set(payload["items"][0].keys()) == set(VoidParserItem().__dict__.keys()) | set(item.to_dict().keys())


def test_format_price():
    assert format_price(12) == "12 .-"
    assert rating_from_score(0.91) == 91
