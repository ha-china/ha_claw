import datetime
import re
from typing import Optional, Tuple

def parse_query_time(query: str) -> Tuple[str, Optional[str], Optional[datetime.datetime], Optional[datetime.datetime]]:
    now = datetime.datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = (now - datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_end = yesterday_start.replace(hour=23, minute=59, second=59, microsecond=999999)
    tomorrow_start = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_end = tomorrow_start.replace(hour=23, minute=59, second=59, microsecond=999999)
    day_after_tomorrow_start = (now + datetime.timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    day_after_tomorrow_end = day_after_tomorrow_start.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    this_week_start = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    last_week_start = (now - datetime.timedelta(days=now.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    last_week_end = (now - datetime.timedelta(days=now.weekday() + 1)).replace(hour=23, minute=59, second=59, microsecond=999999)
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_start = (now.replace(day=1) - datetime.timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = (now.replace(day=1) - datetime.timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)

    relative_date_patterns = {
        r'\b(今日|今天)\b': (lambda m: now.strftime('%Y-%m-%d'), today_start, now),
        r'\b(昨日|昨天)\b': (lambda m: (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d'), yesterday_start, yesterday_end),
        r'\b(明天|明日)\b': (lambda m: (now + datetime.timedelta(days=1)).strftime('%Y-%m-%d'), tomorrow_start, tomorrow_end),
        r'\b(后天|後天)\b': (lambda m: (now + datetime.timedelta(days=2)).strftime('%Y-%m-%d'), day_after_tomorrow_start, day_after_tomorrow_end),
    }

    duration_patterns = {
        r'(?:前|最近)?(\d+)\s*(?:分钟|分鐘)(?:前|内)?': lambda m: (now - datetime.timedelta(minutes=int(m.group(1))), now),
        r'(?:前|最近)?(\d+)\s*(?:小时|小時)(?:前|内)?': lambda m: (now - datetime.timedelta(hours=int(m.group(1))), now),
        r'\b本周\b': lambda m: (this_week_start, now),
        r'\b上周\b': lambda m: (last_week_start, last_week_end),
        r'\b本月\b': lambda m: (this_month_start, now),
        r'\b上月\b': lambda m: (last_month_start, last_month_end),
    }

    modified_query = query
    formatted_date_str = None
    start_time = None
    end_time = None
    pattern_matched = False

    for pattern, (formatter, st, et) in relative_date_patterns.items():
        match = re.search(pattern, modified_query)
        if match:
            date_str = formatter(match)
            formatted_date_str = date_str
            start, end = match.span()
            modified_query = modified_query[:start] + date_str + modified_query[end:]
            start_time = st
            end_time = et
            pattern_matched = True
            break

    if not pattern_matched:
        for pattern, time_func in duration_patterns.items():
            match = re.search(pattern, query) 
            if match:
                st, et = time_func(match)
                start_time = st
                end_time = et
                pattern_matched = True
                
                modified_query = query 
                formatted_date_str = None
                break

    if not pattern_matched:
        start_time = now - datetime.timedelta(days=1)
        end_time = now
        modified_query = query
        formatted_date_str = None

    return modified_query, formatted_date_str, start_time, end_time 