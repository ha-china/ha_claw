from __future__ import annotations
import logging
import re
import asyncio
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from aiohttp import ClientSession, ClientTimeout, TCPConnector

_LOGGER = logging.getLogger(__name__)

TENCENT_API_URL = "https://qt.gtimg.cn/q="

CN_DATA_FORMAT = [
    'stock', 'unused', 'name', '股票代码', '当前价格', '昨收', '今开', '成交量(手)', '外盘', '内盘',
    '买一', '买一量(手)', '买二', '买二量(手)', '买三', '买三量(手)', '买四', '买四量(手)', '买五',
    '买五量(手)', '卖一', '卖一量(手)', '卖二', '卖二量(手)', '卖三', '卖三量(手)', '卖四',
    '卖四量(手)', '卖五', '卖五量(手)', 'unknown1', 'datetime', '涨跌', '涨跌(%)', '最高', '最低',
    '价格/成交量(手)/成交额', '成交量(手)', '成交额(万)', '换手率', '市盈率', 'unknown2', '最高1', '最低1', '振幅',
    '流通市值', '总市值', '市净率', '涨停价', '跌停价', '量比', '委差', '均价', '市盈(动)', '市盈(静)'
]

CN_DATA_PATTERN = re.compile(r'v_([-/\.\w]*)="([\w]*)' + (r'~([-/\.\w]*)' * (len(CN_DATA_FORMAT) - 2)))
US_DATA_PATTERN = re.compile(r'v_([-/\.\w]*)="([\d]*)~([^"]*)"')
FUND_DATA_PATTERN = re.compile(r'v_([-/\.\w]*)="([^"]*)"')


@dataclass
class StockData:
    code: str
    name: str
    price: str
    change: str
    change_percent: str
    open_price: str
    close_price: str
    high: str
    low: str
    volume: str
    amount: str
    datetime: str
    market: str
    extra: Dict[str, Any] = None


class StockAPI:
    def __init__(self):
        self.timeout = ClientTimeout(total=10)
        self.session: Optional[ClientSession] = None

    async def __aenter__(self):
        self.session = ClientSession(timeout=self.timeout, connector=TCPConnector(ssl=False))
        return self

    async def __aexit__(self, *args):
        if self.session and not self.session.closed:
            await self.session.close()
            await asyncio.sleep(0.1)

    def _detect_market(self, code: str) -> tuple[str, str]:
        code = code.upper().strip()
        if code.startswith('SH') or code.startswith('SZ'):
            return 'cn', code.lower()
        if code.startswith('US'):
            return 'us', code.lower()
        if re.match(r'^[015]\d{5}$', code):
            return 'fund', f"jj{code}"
        if re.match(r'^\d{6}$', code):
            if code.startswith('6'):
                return 'cn', f"sh{code}"
            elif code.startswith(('0', '3')):
                return 'cn', f"sz{code}"
            elif code.startswith('8') or code.startswith('4'):
                return 'cn', f"bj{code}"
        if re.match(r'^[A-Z]+$', code):
            return 'us', f"us{code}"
        return 'cn', f"sh{code}"

    async def query_stock(self, code: str) -> Optional[StockData]:
        market, full_code = self._detect_market(code)
        url = f"{TENCENT_API_URL}{full_code}"

        _LOGGER.debug("Query stock: %s -> %s (market: %s)", code, full_code, market)

        try:
            async with self.session.get(url, ssl=False) as resp:
                if resp.status != 200:
                    _LOGGER.error("Stock API returned an error: %s", resp.status)
                    return None

                raw_bytes = await resp.read()
                text = raw_bytes.decode('gbk', errors='ignore')
                _LOGGER.debug("Stock API response: %s...", text[:200])

                if market == 'cn':
                    return self._parse_cn_stock(text, full_code)
                elif market == 'us':
                    return self._parse_us_stock(text, full_code)
                elif market == 'fund':
                    return self._parse_fund(text, full_code)

        except Exception as e:
            _LOGGER.error("Stock query failed: %s", e)

        return None

    async def query_stocks(self, codes: List[str]) -> List[StockData]:
        results = []
        cn_codes = []
        us_codes = []
        fund_codes = []

        for code in codes:
            market, full_code = self._detect_market(code)
            if market == 'cn':
                cn_codes.append(full_code)
            elif market == 'us':
                us_codes.append(full_code)
            elif market == 'fund':
                fund_codes.append(full_code)
        if cn_codes:
            url = f"{TENCENT_API_URL}{','.join(cn_codes)}"
            try:
                async with self.session.get(url, ssl=False) as resp:
                    if resp.status == 200:
                        raw_bytes = await resp.read()
                        text = raw_bytes.decode('gbk', errors='ignore')
                        for code in cn_codes:
                            data = self._parse_cn_stock(text, code)
                            if data:
                                results.append(data)
            except Exception as e:
                _LOGGER.error("Batch China stock query failed: %s", e)

        if us_codes:
            url = f"{TENCENT_API_URL}{','.join(us_codes)}"
            try:
                async with self.session.get(url, ssl=False) as resp:
                    if resp.status == 200:
                        raw_bytes = await resp.read()
                        text = raw_bytes.decode('gbk', errors='ignore')
                        for code in us_codes:
                            data = self._parse_us_stock(text, code)
                            if data:
                                results.append(data)
            except Exception as e:
                _LOGGER.error("Batch US stock query failed: %s", e)

        if fund_codes:
            for code in fund_codes:
                data = await self.query_stock(code.replace('jj', ''))
                if data:
                    results.append(data)

        return results

    def _parse_cn_stock(self, text: str, code: str) -> Optional[StockData]:
        text = text.replace(" ", "").replace("*", "ST")

        matches = CN_DATA_PATTERN.finditer(text)
        for match in matches:
            if len(match.groups()) == len(CN_DATA_FORMAT):
                data = dict(zip(CN_DATA_FORMAT, match.groups()))
                if data.get('stock', '').lower() == code.lower():
                    return StockData(
                        code=data.get('股票代码', code),
                        name=data.get('name', ''),
                        price=data.get('当前价格', ''),
                        change=data.get('涨跌', ''),
                        change_percent=data.get('涨跌(%)', ''),
                        open_price=data.get('今开', ''),
                        close_price=data.get('昨收', ''),
                        high=data.get('最高', ''),
                        low=data.get('最低', ''),
                        volume=data.get('成交量(手)', ''),
                        amount=data.get('成交额(万)', ''),
                        datetime=data.get('datetime', ''),
                        market='cn',
                        extra={
                            '换手率': data.get('换手率', ''),
                            '市盈率': data.get('市盈率', ''),
                            '市净率': data.get('市净率', ''),
                            '总市值': data.get('总市值', ''),
                            '流通市值': data.get('流通市值', ''),
                            '涨停价': data.get('涨停价', ''),
                            '跌停价': data.get('跌停价', ''),
                        }
                    )
        return None

    def _parse_us_stock(self, text: str, code: str) -> Optional[StockData]:
        text = text.replace(" ", "")

        matches = US_DATA_PATTERN.finditer(text)
        for match in matches:
            stock_code = match.group(1)
            raw_data = match.group(3)

            if stock_code.lower() != code.lower():
                continue

            data = raw_data.split('~')
            _LOGGER.debug("US stock data parsed: %s, field count: %s", stock_code, len(data))

            if len(data) < 35:
                continue

            return StockData(
                code=data[2] if len(data) > 2 else code,
                name=data[1] if len(data) > 1 else '',
                price=data[3] if len(data) > 3 else '',
                change=data[30] if len(data) > 30 else '',
                change_percent=data[31] if len(data) > 31 else '',
                open_price=data[5] if len(data) > 5 else '',
                close_price=data[4] if len(data) > 4 else '',
                high=data[32] if len(data) > 32 else '',
                low=data[33] if len(data) > 33 else '',
                volume=data[6] if len(data) > 6 else '',
                amount=data[37] if len(data) > 37 else '',
                datetime=data[29] if len(data) > 29 else '',
                market='us',
                extra={
                    '币种': data[34] if len(data) > 34 else 'USD',
                    '市盈率': data[38] if len(data) > 38 else '',
                    '市值(亿美元)': data[44] if len(data) > 44 else '',
                    '公司名称': data[45] if len(data) > 45 else '',
                }
            )
        return None

    def _parse_fund(self, text: str, code: str) -> Optional[StockData]:
        matches = FUND_DATA_PATTERN.finditer(text)
        for match in matches:
            fund_code = match.group(1)
            raw_data = match.group(2)

            if fund_code.lower() != code.lower():
                continue

            data = raw_data.split('~')
            if len(data) < 5:
                continue

            return StockData(
                code=data[0] if len(data) > 0 else code.replace('jj', ''),
                name=data[1] if len(data) > 1 else '',
                price=data[3] if len(data) > 3 else '',
                change=data[4] if len(data) > 4 else '',
                change_percent=data[5] if len(data) > 5 else '',
                open_price='',
                close_price=data[2] if len(data) > 2 else '',
                high='',
                low='',
                volume='',
                amount='',
                datetime=data[6] if len(data) > 6 else '',
                market='fund',
                extra={}
            )
        return None


def format_stock_data(data: StockData) -> str:
    market_name = {'cn': 'China stock', 'us': 'US stock', 'fund': 'Fund'}.get(data.market, 'Unknown market')

    extra_label_map = {
        '换手率': 'Turnover rate',
        '市盈率': 'P/E ratio',
        '市净率': 'P/B ratio',
        '总市值': 'Market cap',
        '流通市值': 'Free-float market cap',
        '涨停价': 'Limit-up price',
        '跌停价': 'Limit-down price',
        '币种': 'Currency',
        '市值(亿美元)': 'Market cap (USD 100M)',
        '公司名称': 'Company name',
    }

    lines = [
        f"【{data.name}】({data.code}) - {market_name}",
        f"Current price: {data.price}",
        f"Change: {data.change} ({data.change_percent}%)",
    ]

    if data.open_price:
        lines.append(f"Open: {data.open_price} | Previous close: {data.close_price}")
    if data.high and data.low:
        lines.append(f"High: {data.high} | Low: {data.low}")
    if data.volume:
        lines.append(f"Volume: {data.volume}")
    if data.amount:
        lines.append(f"Turnover: {data.amount}")
    if data.datetime:
        lines.append(f"Updated at: {data.datetime}")

    if data.extra:
        extra_items = []
        for k, v in data.extra.items():
            if v:
                extra_items.append(f"{extra_label_map.get(k, k)}: {v}")
        if extra_items:
            lines.append(" | ".join(extra_items[:4]))

    return "\n".join(lines)
