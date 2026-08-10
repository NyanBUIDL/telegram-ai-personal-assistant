from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx

COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_WEB_BASE = "https://www.coingecko.com/en/coins"
VIETNAM_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

COMMON_COIN_IDS = {
    "ada": "cardano",
    "avax": "avalanche-2",
    "bch": "bitcoin-cash",
    "bitcoin": "bitcoin",
    "bnb": "binancecoin",
    "btc": "bitcoin",
    "cardano": "cardano",
    "chainlink": "chainlink",
    "doge": "dogecoin",
    "dogecoin": "dogecoin",
    "dot": "polkadot",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "hype": "hyperliquid",
    "hyperliquid": "hyperliquid",
    "link": "chainlink",
    "litecoin": "litecoin",
    "ltc": "litecoin",
    "sol": "solana",
    "solana": "solana",
    "sui": "sui",
    "the-open-network": "the-open-network",
    "ton": "the-open-network",
    "trx": "tron",
    "usdc": "usd-coin",
    "usdt": "tether",
    "xrp": "ripple",
}

PRICE_MARKERS = (
    "giá",
    "giá cả",
    "bao nhiêu",
    "tỷ giá",
    "price",
    "quote",
)
CASHTAG_RE = re.compile(r"(?<!\w)\$([A-Za-z][A-Za-z0-9-]{1,19})\b")
PRICE_AFTER_MARKER_RE = re.compile(
    r"(?:giá(?:\s+(?:của|coin|token))?|price(?:\s+(?:of|for))?|tỷ giá)"
    r"\s*[:\-]?\s*\$?([A-Za-z][A-Za-z0-9-]{1,39})",
    re.IGNORECASE,
)
PRICE_BEFORE_MARKER_RE = re.compile(
    r"(?<![@\w])\$?([A-Za-z][A-Za-z0-9-]{1,39})"
    r"\s+(?:giá|price|bao nhiêu)",
    re.IGNORECASE,
)


class CoinGeckoError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class CoinPrice:
    coin_id: str
    name: str
    symbol: str
    usd: Decimal
    vnd: Decimal
    usd_market_cap: Decimal | None
    usd_24h_volume: Decimal | None
    usd_24h_change: Decimal | None
    last_updated_at: datetime | None


def extract_price_asset(question: str) -> str | None:
    """Extract a coin name/symbol only when the text clearly asks for a price."""
    normalized = " ".join(question.casefold().split())
    if not any(marker in normalized for marker in PRICE_MARKERS):
        return None
    cashtag = CASHTAG_RE.search(question)
    if cashtag:
        return cashtag.group(1)
    for pattern in (PRICE_BEFORE_MARKER_RE, PRICE_AFTER_MARKER_RE):
        match = pattern.search(question)
        if match:
            candidate = match.group(1).strip(".,!?;:()[]{}")
            if candidate.casefold() not in {"coin", "token", "crypto", "của", "of", "for"}:
                return candidate
    for alias in sorted(COMMON_COIN_IDS, key=len, reverse=True):
        if re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", normalized):
            return alias
    return None


def _decimal(value: object, *, required: bool = False) -> Decimal | None:
    if value is None:
        if required:
            raise CoinGeckoError("CoinGecko không trả về đủ dữ liệu giá.")
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CoinGeckoError("CoinGecko trả về dữ liệu giá không hợp lệ.") from exc


def _format_money(value: Decimal, *, currency: str) -> str:
    absolute = abs(value)
    if currency == "VND":
        return f"{value:,.0f} ₫"
    if absolute >= Decimal("1"):
        decimals = 2 if absolute >= Decimal("100") else 4
    elif absolute >= Decimal("0.01"):
        decimals = 6
    else:
        decimals = 10
    rendered = f"{value:,.{decimals}f}".rstrip("0").rstrip(".")
    return f"${rendered}"


def format_coin_price(price: CoinPrice) -> str:
    change = "Không có dữ liệu"
    if price.usd_24h_change is not None:
        sign = "+" if price.usd_24h_change >= 0 else ""
        change = f"{sign}{price.usd_24h_change:.2f}%"
    market_cap = (
        _format_money(price.usd_market_cap, currency="USD")
        if price.usd_market_cap is not None
        else "Không có dữ liệu"
    )
    volume = (
        _format_money(price.usd_24h_volume, currency="USD")
        if price.usd_24h_volume is not None
        else "Không có dữ liệu"
    )
    updated = (
        price.last_updated_at.astimezone(VIETNAM_TIMEZONE).strftime("%d/%m/%Y %H:%M:%S")
        if price.last_updated_at
        else "Không có dữ liệu"
    )
    return (
        f"GIÁ COINGECKO — {price.name} ({price.symbol.upper()})\n\n"
        f"Giá USD: {_format_money(price.usd, currency='USD')}\n"
        f"Giá VND: {_format_money(price.vnd, currency='VND')}\n"
        f"Biến động 24h: {change}\n"
        f"Vốn hóa: {market_cap}\n"
        f"Khối lượng 24h: {volume}\n"
        f"Cập nhật: {updated} (giờ Việt Nam)\n\n"
        f"Nguồn: {COINGECKO_WEB_BASE}/{price.coin_id}"
    )


class CoinGeckoClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self.api_key = (api_key or "").strip()
        headers = {
            "Accept": "application/json",
            "User-Agent": "TelegramAIPersonalAssistant/0.1",
        }
        if self.api_key:
            headers["x-cg-demo-api-key"] = self.api_key
        self.client = httpx.AsyncClient(
            base_url=COINGECKO_API_BASE,
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        )

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def _get_json(self, path: str, *, params: dict[str, str]) -> object:
        if not self.available:
            raise CoinGeckoError(
                "CoinGecko chưa được cấu hình. Chạy `tg-assistant coingecko-key` trong terminal."
            )
        try:
            response = await self.client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise CoinGeckoError("CoinGecko phản hồi quá chậm; vui lòng thử lại sau.") from exc
        except httpx.HTTPError as exc:
            raise CoinGeckoError("Không kết nối được CoinGecko; vui lòng thử lại sau.") from exc
        if response.status_code in {401, 403}:
            raise CoinGeckoError("CoinGecko API key không hợp lệ hoặc không còn hiệu lực.")
        if response.status_code == 429:
            raise CoinGeckoError("CoinGecko đang giới hạn số lần gọi; vui lòng thử lại sau.")
        if response.is_error:
            raise CoinGeckoError(
                f"CoinGecko tạm lỗi (HTTP {response.status_code}); vui lòng thử lại sau."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise CoinGeckoError("CoinGecko trả về phản hồi không hợp lệ.") from exc

    async def resolve_coin(self, query: str) -> tuple[str, str, str]:
        cleaned = query.strip().lstrip("$").casefold()
        if not cleaned:
            raise CoinGeckoError("Hãy nhập mã hoặc tên coin, ví dụ BTC hoặc bitcoin.")
        if coin_id := COMMON_COIN_IDS.get(cleaned):
            return coin_id, cleaned.upper(), coin_id.replace("-", " ").title()
        payload = await self._get_json("/search", params={"query": cleaned})
        coins = payload.get("coins", []) if isinstance(payload, dict) else []
        if not coins:
            raise CoinGeckoError(f"Không tìm thấy coin “{query.strip()}” trên CoinGecko.")
        exact = next(
            (
                coin
                for coin in coins
                if cleaned
                in {
                    str(coin.get("id", "")).casefold(),
                    str(coin.get("symbol", "")).casefold(),
                    str(coin.get("name", "")).casefold(),
                }
            ),
            coins[0],
        )
        coin_id = str(exact.get("id", "")).strip()
        if not coin_id:
            raise CoinGeckoError(f"Không tìm thấy coin “{query.strip()}” trên CoinGecko.")
        return (
            coin_id,
            str(exact.get("symbol") or cleaned).upper(),
            str(exact.get("name") or coin_id.replace("-", " ").title()),
        )

    async def get_price(self, query: str) -> CoinPrice:
        coin_id, symbol, name = await self.resolve_coin(query)
        payload = await self._get_json(
            "/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd,vnd",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_24hr_change": "true",
                "include_last_updated_at": "true",
            },
        )
        row = payload.get(coin_id) if isinstance(payload, dict) else None
        if not isinstance(row, dict):
            raise CoinGeckoError(f"CoinGecko chưa có dữ liệu giá cho “{query.strip()}”.")
        timestamp = row.get("last_updated_at")
        updated_at = (
            datetime.fromtimestamp(int(timestamp), tz=UTC) if timestamp is not None else None
        )
        return CoinPrice(
            coin_id=coin_id,
            name=name,
            symbol=symbol,
            usd=_decimal(row.get("usd"), required=True),
            vnd=_decimal(row.get("vnd"), required=True),
            usd_market_cap=_decimal(row.get("usd_market_cap")),
            usd_24h_volume=_decimal(row.get("usd_24h_vol")),
            usd_24h_change=_decimal(row.get("usd_24h_change")),
            last_updated_at=updated_at,
        )

    async def close(self) -> None:
        await self.client.aclose()
