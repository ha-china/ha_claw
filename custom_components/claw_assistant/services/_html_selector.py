from __future__ import annotations

from typing import Any, Callable, Optional
from lxml import html as lxml_html
from lxml.html import HtmlElement, tostring
from lxml.cssselect import CSSSelector
from lxml.etree import HTMLParser, _Element, _ElementUnicodeResult


class Selectors(list):

    @property
    def first(self) -> Optional[Selector]:
        return self[0] if self else None

    @property
    def last(self) -> Optional[Selector]:
        return self[-1] if self else None


class Selector:

    __slots__ = ("_root", "url", "encoding")

    def __init__(
        self,
        content: str | bytes | None = None,
        url: str = "",
        encoding: str = "utf-8",
        *,
        _root: HtmlElement | None = None,
    ):
        self.url = url
        self.encoding = encoding
        if _root is not None:
            self._root = _root
        elif content is not None:
            if isinstance(content, str):
                body = content.strip().replace("\x00", "") or "<html/>"
            elif isinstance(content, bytes):
                body = content.replace(b"\x00", b"")
            else:
                raise TypeError(f"content must be str or bytes, got {type(content)}")
            parser = HTMLParser(
                recover=True,
                remove_blank_text=True,
                encoding=encoding,
                huge_tree=True,
            )
            self._root = lxml_html.fromstring(body or "<html/>", parser=parser, base_url=url or "")
        else:
            raise ValueError("Selector needs content or _root")

    def _wrap(self, el: HtmlElement) -> Selector:
        return Selector(_root=el, url=self.url, encoding=self.encoding)

    def _wrap_many(self, elements: list) -> Selectors:
        return Selectors(self._wrap(el) for el in elements if isinstance(el, _Element) and not isinstance(el, _ElementUnicodeResult))

    @property
    def tag(self) -> str:
        return str(self._root.tag) if isinstance(self._root.tag, str) else ""

    @property
    def text(self) -> str:
        return self._root.text or ""

    @property
    def attrib(self) -> dict[str, str]:
        return dict(self._root.attrib)

    def css(self, selector: str) -> Selectors:
        try:
            sel = CSSSelector(selector)
            return self._wrap_many(sel(self._root))
        except Exception:
            return Selectors()

    def find_all(self, *tags: str) -> Selectors:
        results = []
        for tag in tags:
            if isinstance(tag, (list, tuple)):
                for t in tag:
                    results.extend(self._root.iter(t))
            else:
                results.extend(self._root.iter(tag))
        return self._wrap_many(results)

    def find(self, *tags: str) -> Optional[Selector]:
        nodes = self.find_all(*tags)
        return nodes[0] if nodes else None

    def get_all_text(
        self,
        separator: str = "\n",
        strip: bool = False,
        ignore_tags: tuple[str, ...] = ("script", "style"),
        valid_values: bool = True,
    ) -> str:
        ignored_elements: set = set()
        if ignore_tags:
            for tag in ignore_tags:
                ignored_elements.update(self._root.iter(tag))

        parts: list[str] = []

        def _is_visible(node: _ElementUnicodeResult) -> bool:
            parent = node.getparent()
            if parent is None:
                return False
            owner = parent.getparent() if node.is_tail else parent
            while owner is not None:
                if owner in ignored_elements:
                    return False
                owner = owner.getparent()
            return True

        for node in self._root.iter():
            if node in ignored_elements:
                continue
            for text_val in (node.text, node.tail):
                if text_val is None:
                    continue
                if isinstance(text_val, _ElementUnicodeResult):
                    if not _is_visible(text_val):
                        continue
                t = text_val.strip() if strip else text_val
                if valid_values and not t.strip():
                    continue
                parts.append(t)

        return separator.join(parts)

    def find_ancestor(self, func: Callable[[Selector], bool]) -> Optional[Selector]:
        el = self._root.getparent()
        while el is not None:
            wrapped = self._wrap(el)
            if func(wrapped):
                return wrapped
            el = el.getparent()
        return None

    @property
    def html_content(self) -> str:
        content = tostring(self._root, encoding=self.encoding, method="html", with_tail=False)
        if isinstance(content, bytes):
            return content.decode(self.encoding)
        return content

    def __str__(self) -> str:
        return self.html_content

    def __repr__(self) -> str:
        tag = self.tag
        text = (self.text or "")[:30]
        return f"<Selector tag={tag} text={text!r}>"
