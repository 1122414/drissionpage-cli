from __future__ import annotations

from dp_cli.projector import ExtractProjector, StructuredRecordProjector
from dp_cli.service import CliService


def _node(
    ref: str,
    xpath: str,
    *,
    parent_ref: str | None = None,
    ref_type: str = "container",
    tag: str = "div",
    role: str = "",
    text: str = "",
    name: str = "",
    href: str = "",
    url: str = "https://example.test/catalog",
) -> dict:
    return {
        "ref": ref,
        "ref_type": ref_type,
        "parent_ref": parent_ref,
        "xpath": xpath,
        "tag": tag,
        "role": role,
        "text": text,
        "name": name,
        "href": href,
        "url": url,
        "depth": xpath.count("/"),
        "visibility": {
            "in_viewport": True,
            "interactable_now": ref_type == "element",
        },
    }


def _project(nodes: list[dict], schema: list[str]) -> dict:
    return ExtractProjector().project(
        {
            "representative_ref": "r0",
            "item_refs": [node["ref"] for node in nodes],
            "root_xpath": "/html/body/main/div[1]",
        },
        nodes,
        schema,
    )


def test_projects_product_cards_without_losing_price() -> None:
    root = "/html/body/main/div[1]"
    nodes = [_node("r0", root, tag="main", role="main")]
    for index, (title, price) in enumerate(
        [("Alpha", "24.99"), ("Beta", "31.50"), ("Gamma", "8.00")],
        start=1,
    ):
        card_ref = f"r{index}"
        card_xpath = f"{root}/div[{index}]"
        nodes.extend(
            [
                _node(card_ref, card_xpath, parent_ref="r0"),
                _node(
                    f"e{index}a",
                    f"{card_xpath}/h3[1]/a[1]",
                    parent_ref=card_ref,
                    ref_type="element",
                    tag="a",
                    role="link",
                    text=title,
                    name=title,
                    href=f"/products/{index}",
                ),
                _node(
                    f"e{index}b",
                    f"{card_xpath}/div[1]",
                    parent_ref=card_ref,
                    ref_type="element",
                    text=price,
                ),
            ]
        )

    result = _project(nodes, ["title", "price", "url"])

    assert result["item_count"] == 3
    assert [item["price"] for item in result["items"]] == [
        "24.99",
        "31.50",
        "8.00",
    ]
    assert result["items"][0]["url"] == "https://example.test/products/1"


def test_structured_projection_respects_detail_link_seed_for_broad_regions(
    monkeypatch,
) -> None:
    root = "/html/body/main/div[1]"
    nodes = [_node("r0", root, tag="main", role="main")]
    movie_refs = []
    for index in range(1, 6):
        taxonomy_ref = f"taxonomy-{index}"
        taxonomy_xpath = f"{root}/aside[1]/a[{index}]"
        nodes.append(
            _node(
                taxonomy_ref,
                taxonomy_xpath,
                parent_ref="r0",
                ref_type="element",
                tag="a",
                role="link",
                text=f"类型 {index}",
                name=f"类型 {index}",
                href=f"/typerank?type={index}",
            )
        )
        card_ref = f"movie-card-{index}"
        movie_ref = f"movie-{index}"
        movie_refs.append(movie_ref)
        card_xpath = f"{root}/section[1]/article[{index}]"
        nodes.extend(
            [
                _node(card_ref, card_xpath, parent_ref="r0"),
                _node(
                    movie_ref,
                    f"{card_xpath}/h3[1]/a[1]",
                    parent_ref=card_ref,
                    ref_type="element",
                    tag="a",
                    role="link",
                    text=f"真实电影标题 {index}",
                    name=f"真实电影标题 {index}",
                    href=f"/subject/{index}/",
                ),
            ]
        )

    monkeypatch.setattr(
        StructuredRecordProjector,
        "project",
        lambda _self, _nodes, _schema, _root_xpath: [
            {
                "title": f"类型 {index}",
                "url": f"https://example.test/typerank?type={index}",
            }
            for index in range(1, 6)
        ],
    )

    result = ExtractProjector().project(
        {
            "group_ref": "r0",
            "item_refs": movie_refs,
            "root_xpath": root,
        },
        nodes,
        ["title", "url"],
    )

    assert result["item_count"] == 5
    assert [item["title"] for item in result["items"]] == [
        f"真实电影标题 {index}" for index in range(1, 6)
    ]
    assert all("/subject/" in item["url"] for item in result["items"])


def test_projects_quote_records_with_author_and_tags() -> None:
    root = "/html/body/main/div[1]"
    nodes = [_node("r0", root, tag="main", role="main")]
    for index in range(1, 4):
        quote_ref = f"r{index}"
        quote_xpath = f"{root}/div[{index}]"
        nodes.extend(
            [
                _node(
                    quote_ref,
                    quote_xpath,
                    parent_ref="r0",
                    text=f"“Quote {index} has enough text.” by Author {index} Tags: life wisdom",
                ),
                _node(
                    f"e{index}a",
                    f"{quote_xpath}/span[1]",
                    parent_ref=quote_ref,
                    ref_type="element",
                    tag="span",
                    text=f"“Quote {index} has enough text.”",
                ),
                _node(
                    f"e{index}b",
                    f"{quote_xpath}/small[1]",
                    parent_ref=quote_ref,
                    ref_type="element",
                    tag="small",
                    text=f"Author {index}",
                ),
                _node(
                    f"e{index}c",
                    f"{quote_xpath}/div[1]/a[1]",
                    parent_ref=quote_ref,
                    ref_type="element",
                    tag="a",
                    role="link",
                    text="life",
                    href="/tag/life/",
                ),
                _node(
                    f"e{index}d",
                    f"{quote_xpath}/div[1]/a[2]",
                    parent_ref=quote_ref,
                    ref_type="element",
                    tag="a",
                    role="link",
                    text="wisdom",
                    href="/tag/wisdom/",
                ),
            ]
        )

    result = _project(nodes, ["text", "author", "tags"])

    assert result["item_count"] == 3
    assert result["items"][0] == {
        "text": "“Quote 1 has enough text.”",
        "author": "Author 1",
        "tags": ["life", "wisdom"],
    }


def test_projects_container_table_rows_using_headers() -> None:
    root = "/html/body/main/div[1]"
    nodes = [_node("r0", root, tag="table", role="table")]
    headers = ["Team Name", "Year", "Wins", "Losses"]
    values = [
        ["A", "2024", "10", "2"],
        ["B", "2024", "8", "4"],
        ["C", "2024", "6", "6"],
    ]
    for column, text in enumerate(headers, start=1):
        nodes.append(
            _node(
                f"h{column}",
                f"{root}/tbody[1]/tr[1]/th[{column}]",
                parent_ref="r0",
                tag="th",
                text=text,
            )
        )
    for row_index, row in enumerate(values, start=2):
        for column, text in enumerate(row, start=1):
            nodes.append(
                _node(
                    f"r{row_index}c{column}",
                    f"{root}/tbody[1]/tr[{row_index}]/td[{column}]",
                    parent_ref="r0",
                    tag="td",
                    text=text,
                )
            )

    result = _project(nodes, ["team", "year", "wins", "losses"])

    assert result["item_count"] == 3
    assert result["items"][0] == {
        "team": "A",
        "year": "2024",
        "wins": "10",
        "losses": "2",
    }


def test_detects_linkless_quotes_and_container_only_table() -> None:
    service = CliService.__new__(CliService)
    quote_root = "/html/body/main/div[1]"
    quote_nodes = [_node("q0", quote_root, tag="main", role="main")]
    for index in range(1, 4):
        quote_nodes.extend(
            [
                _node(
                    f"q{index}",
                    f"{quote_root}/div[{index}]",
                    parent_ref="q0",
                    text=f"Quote {index} by Author {index}",
                ),
                _node(
                    f"qe{index}",
                    f"{quote_root}/div[{index}]/span[1]",
                    parent_ref=f"q{index}",
                    ref_type="element",
                    tag="span",
                    text=f"Quote {index}",
                ),
            ]
        )

    table_root = "/html/body/main/table[1]"
    table_nodes = [_node("t0", table_root, tag="table", role="table")]
    for row in range(1, 5):
        for column in range(1, 3):
            table_nodes.append(
                _node(
                    f"t{row}c{column}",
                    f"{table_root}/tbody[1]/tr[{row}]/td[{column}]",
                    parent_ref="t0",
                    tag="td",
                    text=f"{row}-{column}",
                )
            )

    quote_regions = service._detect_data_regions(quote_nodes)
    table_regions = service._detect_data_regions(table_nodes)

    assert quote_regions[0]["ref"] == "q0"
    assert quote_regions[0]["item_count"] == 3
    assert table_regions[0]["ref"] == "t0"
    assert table_regions[0]["item_count"] == 4
