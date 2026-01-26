from __future__ import annotations
import logging
import re
import asyncio
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from aiohttp import ClientSession, ClientTimeout

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
        self.session = ClientSession(timeout=self.timeout)
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
        
        _LOGGER.info(f"查询股票: {code} -> {full_code} (市场: {market})")
        
        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    _LOGGER.error(f"股票API返回错误: {resp.status}")
                    return None
                
                raw_bytes = await resp.read()
                text = raw_bytes.decode('gbk', errors='ignore')
                _LOGGER.debug(f"股票API响应: {text[:200]}...")
                
                if market == 'cn':
                    return self._parse_cn_stock(text, full_code)
                elif market == 'us':
                    return self._parse_us_stock(text, full_code)
                elif market == 'fund':
                    return self._parse_fund(text, full_code)
        
        except Exception as e:
            _LOGGER.error(f"查询股票失败: {e}")
        
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
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        raw_bytes = await resp.read()
                        text = raw_bytes.decode('gbk', errors='ignore')
                        for code in cn_codes:
                            data = self._parse_cn_stock(text, code)
                            if data:
                                results.append(data)
            except Exception as e:
                _LOGGER.error(f"批量查询A股失败: {e}")
        
        if us_codes:
            url = f"{TENCENT_API_URL}{','.join(us_codes)}"
            try:
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        raw_bytes = await resp.read()
                        text = raw_bytes.decode('gbk', errors='ignore')
                        for code in us_codes:
                            data = self._parse_us_stock(text, code)
                            if data:
                                results.append(data)
            except Exception as e:
                _LOGGER.error(f"批量查询美股失败: {e}")
        
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
            _LOGGER.debug(f"美股数据解析: {stock_code}, 字段数: {len(data)}")
            
            if len(data) < 35:
                _LOGGER.warning(f"美股数据字段不足: {len(data)}")
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
    market_name = {'cn': 'A股', 'us': '美股', 'fund': '基金'}.get(data.market, '未知')
    
    lines = [
        f"【{data.name}】({data.code}) - {market_name}",
        f"当前价格: {data.price}",
        f"涨跌: {data.change} ({data.change_percent}%)",
    ]
    
    if data.open_price:
        lines.append(f"今开: {data.open_price} | 昨收: {data.close_price}")
    if data.high and data.low:
        lines.append(f"最高: {data.high} | 最低: {data.low}")
    if data.volume:
        lines.append(f"成交量: {data.volume}")
    if data.amount:
        lines.append(f"成交额: {data.amount}")
    if data.datetime:
        lines.append(f"更新时间: {data.datetime}")
    
    if data.extra:
        extra_items = []
        for k, v in data.extra.items():
            if v:
                extra_items.append(f"{k}: {v}")
        if extra_items:
            lines.append(" | ".join(extra_items[:4]))
    
    return "\n".join(lines)


STOCK_KEYWORDS = [
    "股票", "股价", "股市", "A股", "美股", "港股",
    "涨跌", "涨停", "跌停", "行情", "大盘",
    "基金", "净值", "指数",
    "茅台", "腾讯", "阿里", "特斯拉", "苹果", "英伟达",
]

STOCK_CODE_PATTERN = re.compile(r'\b(\d{6}|[A-Z]{1,5})\b')


def detect_stock_query(text: str) -> tuple[bool, List[str]]:
    text_lower = text.lower()
    has_keyword = any(kw in text for kw in STOCK_KEYWORDS)
    codes = []
    digit_codes = re.findall(r'\b(\d{6})\b', text)
    codes.extend(digit_codes)
    letter_codes = re.findall(r'\b([A-Z]{2,5})\b', text.upper())
    exclude_words = {'THE', 'AND', 'FOR', 'ARE', 'NOT', 'YOU', 'ALL', 'CAN', 'HER', 'WAS', 'ONE', 'OUR', 'OUT'}
    letter_codes = [c for c in letter_codes if c not in exclude_words]
    codes.extend(letter_codes)
    
    return has_keyword or len(codes) > 0, codes
