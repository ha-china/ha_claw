from __future__ import annotations
import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    content: Optional[str] = None
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "content": self.content,
            "metadata": self.metadata
        }

async def clean_text(text: str) -> str:
    if not text:
        return ""
    
    text = re.sub(r'[×…–—～]', lambda m: {'×': 'x', '…': '...', '–': '-', '—': '-', '～': '~'}[m.group()], text)
    text = re.sub(r'<[^>]+>|<script.*?</script>|<style.*?</style>', '', text)
    text = re.sub(r'首页\s*[|].*?[|]|登录\s*[|]\s*注册|Copyright © .*?All Rights Reserved|关于我们.*?联系我们|点击.*?详情|返回顶部|网站地图', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\n{2,}', '\n', text)
    
    return text.strip() 