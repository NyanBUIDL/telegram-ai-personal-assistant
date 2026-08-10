from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from tg_assistant.services.coingecko import (
    CoinGeckoClient,
    CoinGeckoError,
    extract_price_asset,
    format_coin_price,
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("giá BTC bao nhiêu?", "BTC"),
        ("ETH giá bao nhiêu", "ETH"),
        ("price of $SOL", "SOL"),
        ("Cho mình tỷ giá bitcoin", "bitcoin"),
    ],
)
def test_extract_price_asset(question: str, expected: str) -> None:
    assert extract_price_asset(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "Bitcoin hoạt động như thế nào?",
        "Cập nhật dự án Solana",
        "@a_member nói gì về ETH?",
    ],
)
def test_extract_price_asset_avoids_non_price_questions(question: str) -> None:
    assert extract_price_asset(question) is None


@pytest.mark.asyncio
async def test_common_coin_price_uses_demo_header_and_formats_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/simple/price"
        assert request.headers["x-cg-demo-api-key"] == "CG-test-key-12345678901234567890"
        assert request.url.params["ids"] == "bitcoin"
        return httpx.Response(
            200,
            json={
                "bitcoin": {
                    "usd": 68421.12,
                    "vnd": 1780000000,
                    "usd_market_cap": 1360000000000,
                    "usd_24h_vol": 31000000000,
                    "usd_24h_change": 2.45,
                    "last_updated_at": 1784998800,
                }
            },
        )

    client = CoinGeckoClient(
        "CG-test-key-12345678901234567890",
        transport=httpx.MockTransport(handler),
    )
    try:
        price = await client.get_price("BTC")
    finally:
        await client.close()

    assert price.coin_id == "bitcoin"
    assert price.usd == Decimal("68421.12")
    output = format_coin_price(price)
    assert "Bitcoin (BTC)" in output
    assert "Giá USD: $68,421.12" in output
    assert "Biến động 24h: +2.45%" in output
    assert "https://www.coingecko.com/en/coins/bitcoin" in output


@pytest.mark.asyncio
async def test_unknown_symbol_is_resolved_with_search() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v3/search":
            return httpx.Response(
                200,
                json={"coins": [{"id": "pepe", "name": "Pepe", "symbol": "PEPE"}]},
            )
        return httpx.Response(
            200,
            json={
                "pepe": {
                    "usd": 0.000012,
                    "vnd": 0.31,
                    "last_updated_at": 1784998800,
                }
            },
        )

    client = CoinGeckoClient(
        "CG-test-key-12345678901234567890",
        transport=httpx.MockTransport(handler),
    )
    try:
        price = await client.get_price("PEPE")
    finally:
        await client.close()

    assert requests == ["/api/v3/search", "/api/v3/simple/price"]
    assert price.name == "Pepe"
    assert price.symbol == "PEPE"


@pytest.mark.asyncio
async def test_invalid_key_returns_safe_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    client = CoinGeckoClient(
        "CG-test-key-12345678901234567890",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(CoinGeckoError, match="không hợp lệ"):
            await client.get_price("BTC")
    finally:
        await client.close()
