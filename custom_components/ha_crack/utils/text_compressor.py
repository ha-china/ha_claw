"""文本压缩模块 - 用于压缩长文本以适应LLM上下文窗口
支持多种压缩策略：LLMLingua、摘要、关键句提取等
"""
from __future__ import annotations
import re
import logging
from typing import Optional, List
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

@dataclass
class CompressionResult:
    original_length: int
    compressed_length: int
    compression_ratio: float
    text: str
    method: str

class TextCompressor:
    def __init__(self, target_length: int = 2000, use_llmlingua: bool = False):
        self.target_length = target_length
        self.use_llmlingua = use_llmlingua
        self._llmlingua = None
    
    def _init_llmlingua(self):
        if self._llmlingua is None and self.use_llmlingua:
            try:
                from llmlingua import PromptCompressor
                self._llmlingua = PromptCompressor(
                    model_name="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
                    use_llmlingua2=True
                )
                _LOGGER.info("LLMLingua initialized")
            except ImportError:
                _LOGGER.warning("LLMLingua not installed, using fallback compression")
                self.use_llmlingua = False
            except Exception as e:
                _LOGGER.error(f"LLMLingua init failed: {e}")
                self.use_llmlingua = False
    
    def compress(self, text: str, target_ratio: float = 0.5) -> CompressionResult:
        if not text:
            return CompressionResult(0, 0, 1.0, "", "none")
        
        original_length = len(text)
        
        if original_length <= self.target_length:
            return CompressionResult(original_length, original_length, 1.0, text, "none")
        
        if self.use_llmlingua:
            result = self._compress_llmlingua(text, target_ratio)
            if result:
                return result
        
        result = self._compress_smart(text)
        return result
    
    def _compress_llmlingua(self, text: str, target_ratio: float) -> Optional[CompressionResult]:
        self._init_llmlingua()
        if not self._llmlingua:
            return None
        try:
            compressed = self._llmlingua.compress_prompt(
                text,
                rate=target_ratio,
                force_tokens=['\n', '.', '!', '?', ',']
            )
            compressed_text = compressed.get('compressed_prompt', text)
            return CompressionResult(
                original_length=len(text),
                compressed_length=len(compressed_text),
                compression_ratio=len(compressed_text) / len(text),
                text=compressed_text,
                method="llmlingua"
            )
        except Exception as e:
            _LOGGER.error(f"LLMLingua compression failed: {e}")
            return None
    
    def _compress_smart(self, text: str) -> CompressionResult:
        original_length = len(text)
        
        text = self._remove_redundancy(text)
        text = self._extract_key_sentences(text, self.target_length)
        
        return CompressionResult(
            original_length=original_length,
            compressed_length=len(text),
            compression_ratio=len(text) / original_length if original_length > 0 else 1.0,
            text=text,
            method="smart"
        )
    
    def _remove_redundancy(self, text: str) -> str:
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)
        text = re.sub(r'(.{20,}?)\1+', r'\1', text)
        
        noise_patterns = [
            r'点击.*?查看.*?详情',
            r'关注.*?获取.*?更多',
            r'分享.*?转发',
            r'Copyright.*?版权所有',
            r'备案号.*?\d+',
            r'ICP.*?\d+',
            r'网站地图|联系我们|关于我们|隐私政策',
            r'首页\s*[|｜]\s*.*?[|｜]',
            r'登录\s*[|｜]\s*注册',
            r'返回顶部|回到顶部',
            r'上一篇|下一篇|相关推荐|热门文章',
            r'广告|推广|赞助|sponsor',
            r'扫码.*?关注|微信公众号|QQ群',
            r'阅读\s*\d+|评论\s*\d+|点赞\s*\d+',
            r'发布时间|发表于|来源[:：]',
            r'免责声明|版权声明|转载请注明',
            r'客服电话|联系方式|在线咨询',
            r'下载APP|安装应用',
            r'[\u4e00-\u9fff]{0,5}推荐[\u4e00-\u9fff]{0,5}',
            r'热门标签|相关标签|标签[:：]',
        ]
        for pattern in noise_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        
        return text.strip()
    
    def _extract_key_sentences(self, text: str, max_length: int) -> str:
        sentences = re.split(r'(?<=[。！？.!?])\s*', text)
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]
        
        if not sentences:
            return text[:max_length]
        
        scored = []
        keywords = set()
        for s in sentences:
            words = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', s.lower()))
            keywords.update(words)
        
        keyword_freq = {}
        for s in sentences:
            for word in re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', s.lower()):
                keyword_freq[word] = keyword_freq.get(word, 0) + 1
        
        for i, s in enumerate(sentences):
            score = 0
            words = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', s.lower()))
            for word in words:
                score += keyword_freq.get(word, 0)
            
            if i < 3:
                score *= 1.5
            if i >= len(sentences) - 2:
                score *= 1.3
            
            if any(k in s for k in ['重要', '关键', '核心', '总结', '结论', '因此', '所以']):
                score *= 1.5
            
            scored.append((score, i, s))
        
        scored.sort(reverse=True)
        
        selected = []
        current_length = 0
        selected_indices = set()
        
        for score, idx, sentence in scored:
            if current_length + len(sentence) <= max_length:
                selected.append((idx, sentence))
                selected_indices.add(idx)
                current_length += len(sentence)
        
        selected.sort(key=lambda x: x[0])
        result = ''.join(s for _, s in selected)
        
        return result if result else text[:max_length]
    
    def compress_for_context(self, texts: List[str], max_total_length: int = 4000) -> List[str]:
        total_length = sum(len(t) for t in texts)
        if total_length <= max_total_length:
            return texts
        
        ratio = max_total_length / total_length
        target_per_text = int(max_total_length / len(texts))
        
        compressed = []
        for text in texts:
            if len(text) <= target_per_text:
                compressed.append(text)
            else:
                result = self.compress(text, target_ratio=ratio)
                compressed.append(result.text[:target_per_text])
        
        return compressed

def compress_search_results(results: list, max_length: int = 3000) -> str:
    if not results:
        return ""
    
    num_results = min(len(results), 5)
    per_result_length = max_length // num_results
    compressor = TextCompressor(target_length=per_result_length)
    
    parts = []
    for i, r in enumerate(results[:num_results], 1):
        title = getattr(r, 'title', '') or ''
        snippet = getattr(r, 'snippet', '') or ''
        content = getattr(r, 'content', '') or ''
        url = getattr(r, 'url', '') or ''
        metadata = getattr(r, 'metadata', {}) or {}
        
        text = content if content else snippet
        if text and len(text) > per_result_length - 100:
            result = compressor.compress(text)
            text = result.text
        
        time_str = metadata.get('time', '') or metadata.get('timestamp', '')
        header = f"[{i}] {title}"
        if time_str:
            header += f" ({time_str})"
        
        parts.append(f"{header}\n{text}")
    
    combined = '\n\n'.join(parts)
    
    if len(combined) > max_length:
        final_compressor = TextCompressor(target_length=max_length)
        result = final_compressor.compress(combined)
        combined = result.text
    
    return combined

async def compress_text_async(text: str, target_length: int = 2000) -> str:
    compressor = TextCompressor(target_length=target_length)
    result = compressor.compress(text)
    _LOGGER.debug(f"Compressed {result.original_length} -> {result.compressed_length} ({result.method})")
    return result.text
