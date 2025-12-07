#!/usr/bin/env python3
"""Extract listing data from Kleinanzeigen HTML."""

import json
import re
from html import entities
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class Node:
    """Simple representation of an HTML element."""

    def __init__(self, tag: Optional[str] = None, attrs: Optional[Dict[str, str]] = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.contents: List[Union["Node", str]] = []

    @property
    def children(self) -> List["Node"]:
        return [item for item in self.contents if isinstance(item, Node)]

    def add_child(self, node: "Node") -> None:
        self.contents.append(node)

    def add_text(self, text: str) -> None:
        if text:
            self.contents.append(text)

    def iter_text(self) -> Iterable[str]:
        for item in self.contents:
            if isinstance(item, Node):
                yield from item.iter_text()
            else:
                yield item

    def class_list(self) -> List[str]:
        value = self.attrs.get("class")
        if not value:
            return []
        return value.split()


class SimpleHTMLTreeBuilder(HTMLParser):
    """Builds a lightweight DOM-like tree for HTML content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root = Node("document", {})
        self.stack: List[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._start_node(tag, attrs, is_self_closing=tag in VOID_TAGS)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._start_node(tag, attrs, is_self_closing=True)

    def _start_node(self, tag: str, attrs: List[Tuple[str, Optional[str]]], *, is_self_closing: bool) -> None:
        attr_dict: Dict[str, str] = {}
        for key, value in attrs:
            if value is None:
                value = ""
            # Keep the first occurrence of an attribute.
            attr_dict.setdefault(key, value)
        node = Node(tag, attr_dict)
        self.stack[-1].add_child(node)
        if not is_self_closing:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        # Pop the stack until the matching tag is found.
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].add_text(data)

    def handle_entityref(self, name: str) -> None:
        char = chr(entities.name2codepoint.get(name, 0)) if name in entities.name2codepoint else f"&{name};"
        self.stack[-1].add_text(char)

    def handle_charref(self, name: str) -> None:
        try:
            if name.lower().startswith("x"):
                char = chr(int(name[1:], 16))
            else:
                char = chr(int(name))
        except ValueError:
            char = f"&#{name};"
        self.stack[-1].add_text(char)

    def handle_comment(self, data: str) -> None:  # pragma: no cover - comments are ignored
        pass


def parse_html(html: str) -> Node:
    parser = SimpleHTMLTreeBuilder()
    parser.feed(html)
    parser.close()
    return parser.root


def find_by_id(node: Node, element_id: str) -> Optional[Node]:
    if node.attrs.get("id") == element_id:
        return node
    for child in node.children:
        match = find_by_id(child, element_id)
        if match is not None:
            return match
    return None


def parse_simple_selector(part: str) -> Tuple[Optional[str], List[str], Optional[int]]:
    part = part.strip()
    if not part:
        return None, [], None
    nth_match = re.search(r":nth-child\((\d+)\)$", part)
    nth_value: Optional[int] = None
    if nth_match:
        nth_value = int(nth_match.group(1))
        part = part[: nth_match.start()]
    part = part.strip()
    if not part:
        return None, [], nth_value
    segments = part.split(".")
    tag = segments[0] if segments[0] else None
    classes = [seg for seg in segments[1:] if seg]
    return tag, classes, nth_value


def match_node(node: Node, tag: Optional[str], classes: Sequence[str]) -> bool:
    if tag is not None and node.tag != tag:
        return False
    if classes:
        node_classes = set(node.class_list())
        for cls in classes:
            if cls not in node_classes:
                return False
    return True


def select_node(root: Node, selector: str) -> Optional[Node]:
    parts = [part.strip() for part in selector.split(">")]
    if not parts:
        return None
    first = parts[0]
    if not first.startswith("#"):
        return None
    node = find_by_id(root, first[1:])
    if node is None:
        return None
    for part in parts[1:]:
        if not part:
            continue
        tag, classes, nth_value = parse_simple_selector(part)
        if tag is None and nth_value is None:
            # No tag to match; cannot proceed.
            return None
        children = node.children
        if nth_value is not None:
            index = nth_value - 1
            if not (0 <= index < len(children)):
                return None
            candidate = children[index]
            if tag is not None and candidate.tag != tag:
                return None
            if not match_node(candidate, tag, classes):
                return None
            node = candidate
        else:
            next_node = None
            for child in children:
                if match_node(child, tag, classes):
                    next_node = child
                    break
            if next_node is None:
                return None
            node = next_node
    return node


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def extract_text(root: Node, selector: str) -> Optional[str]:
    node = select_node(root, selector)
    if node is None:
        return None
    text = "".join(node.iter_text())
    return normalize_whitespace(text)


def extract_list(root: Node, selector: str) -> List[str]:
    node = select_node(root, selector)
    if node is None:
        return []
    items: List[str] = []
    for child in node.children:
        if child.tag != "li":
            continue
        text = normalize_whitespace("".join(child.iter_text()))
        if text:
            items.append(text)
    return items


def iter_descendants(node: Node) -> Iterable[Node]:
    for child in node.children:
        yield child
        yield from iter_descendants(child)


def extract_images(root: Node) -> List[str]:
    container = select_node(root, "#viewad-product") or root
    primary: List[str] = []
    fallback: List[str] = []
    seen: set[str] = set()
    for node in iter_descendants(container):
        if node.tag != "img":
            continue
        url = node.attrs.get("data-imgsrc") or node.attrs.get("src") or ""
        url = url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        if "rule=$_59" in url:
            primary.append(url)
        else:
            fallback.append(url)
    return primary if primary else fallback


def extract_breadcrumb(root: Node) -> Optional[str]:
    node = select_node(root, "#vap-brdcrmb")
    if node is None:
        return None
    parts: List[str] = []
    for child in node.children:
        text = normalize_whitespace("".join(child.iter_text()))
        if text:
            parts.append(text)
    if parts and parts[0].lower() == "kleinanzeigen":
        parts = parts[1:]
    if not parts:
        return None
    return " > ".join(parts)


def extract_userid(root: Node) -> Optional[str]:
    node = select_node(root, "#viewad-contact > div > ul > li:nth-child(1) > i > a")
    if node is None:
        return None
    href = node.attrs.get("href", "")
    match = re.search(r"userId=(\d+)", href)
    if match:
        return match.group(1)
    text = normalize_whitespace("".join(node.iter_text()))
    return text or None


def extract_location(root: Node) -> Optional[str]:
    street = extract_text(root, "#street-address")
    locality = extract_text(root, "#viewad-locality")
    if street:
        street = street.rstrip(",")
    if street and locality:
        return f"{street}, {locality}"
    return street or locality


def render_inner_html(node: Node, *, skip_ids: Optional[Sequence[str]] = None) -> str:
    skip_ids = tuple(skip_ids or [])

    def render(node: Node) -> str:
        if node.attrs.get("id") in skip_ids:
            return ""
        if node.tag in VOID_TAGS:
            if node.tag == "br":
                return "<br>"
            attrs = "".join(
                f" {name}=\"{value}\"" for name, value in node.attrs.items() if name != "id" or value not in skip_ids
            )
            return f"<{node.tag}{attrs}>"
        attrs = "".join(
            f" {name}=\"{value}\"" for name, value in node.attrs.items() if node.attrs.get("id") not in skip_ids
        )
        inner = []
        for item in node.contents:
            if isinstance(item, Node):
                inner.append(render(item))
            else:
                inner.append(item)
        return f"<{node.tag}{attrs}>{''.join(inner)}</{node.tag}>"

    parts: List[str] = []
    for item in node.contents:
        if isinstance(item, Node):
            parts.append(render(item))
        else:
            parts.append(item)
    html = "".join(parts)
    return html.strip()


def build_listing(root: Node) -> Dict[str, object]:
    fahrzeug_fields = {
        "title": "#viewad-title",
        "marke": "#viewad-details > div > ul:nth-child(1) > li:nth-child(1) > span",
        "modell": "#viewad-details > div > ul:nth-child(1) > li:nth-child(2) > span",
        "kilometerstand": "#viewad-details > div > ul:nth-child(1) > li:nth-child(3) > span",
        "fahrzeugzustand": "#viewad-details > div > ul:nth-child(1) > li:nth-child(4) > span",
        "erstzulassung": "#viewad-details > div > ul:nth-child(1) > li:nth-child(5) > span",
        "kraftstoffart": "#viewad-details > div > ul:nth-child(1) > li:nth-child(6) > span",
        "leistung": "#viewad-details > div > ul:nth-child(1) > li:nth-child(7) > span",
        "getriebe": "#viewad-details > div > ul:nth-child(2) > li:nth-child(1) > span",
        "fahrzeugtyp": "#viewad-details > div > ul:nth-child(2) > li:nth-child(2) > span",
        "anzahl_tueren": "#viewad-details > div > ul:nth-child(2) > li:nth-child(3) > span",
        "umweltplakette": "#viewad-details > div > ul:nth-child(2) > li:nth-child(4) > span",
        "schadstoffklasse": "#viewad-details > div > ul:nth-child(2) > li:nth-child(5) > span",
        "aussenfarbe": "#viewad-details > div > ul:nth-child(2) > li:nth-child(6) > span",
        "material_innenausstattung": "#viewad-details > div > ul:nth-child(2) > li:nth-child(7) > span",
    }

    fahrzeug: Dict[str, object] = {}
    for key, selector in fahrzeug_fields.items():
        value = extract_text(root, selector)
        if value is not None:
            fahrzeug[key] = value

    preis_wert = extract_text(root, "#viewad-price")
    if preis_wert is not None:
        fahrzeug["preis"] = {"wert": preis_wert}

    fahrzeug["ausstattung"] = extract_list(root, "#viewad-configuration > div > ul")

    description_node = select_node(root, "#viewad-description-text")
    if description_node is not None:
        description_html = render_inner_html(description_node, skip_ids=("mobile-link",))
        # Kleinanzeigen uses <br /> markup; normalise to <br> for consistency.
        description_html = description_html.replace("<br />", "<br>").replace("<br/>", "<br>")
        fahrzeug["Beschreibung"] = description_html

    fahrzeug["images"] = extract_images(root)

    anzeige: Dict[str, object] = {}
    kategorie = extract_breadcrumb(root)
    if kategorie is not None:
        anzeige["kategorie"] = kategorie
    anzeige_id = extract_text(root, "#viewad-ad-id-box > ul > li:nth-child(2)")
    if anzeige_id is not None:
        anzeige["anzeige_id"] = anzeige_id

    verkaeufer_fields = {
        "name": "#viewad-contact > div > ul > li:nth-child(1) > span > span.text-body-regular-strong.text-force-linebreak.userprofile-vip > a",
        "nutzertyp": "#viewad-contact > div > ul > li:nth-child(1) > span > span:nth-child(2) > span",
        "aktiv_seit": "#viewad-contact > div > ul > li:nth-child(1) > span > span:nth-child(3) > span",
    }
    verkaeufer: Dict[str, object] = {}
    for key, selector in verkaeufer_fields.items():
        value = extract_text(root, selector)
        if value is not None:
            if key == "aktiv_seit" and value.lower().startswith("aktiv seit "):
                value = value[len("Aktiv seit ") :]
            verkaeufer[key] = value

    userid = extract_userid(root)
    if userid is not None:
        verkaeufer["userid"] = userid

    ort = extract_location(root)
    if ort is not None:
        verkaeufer["ort"] = ort

    result: Dict[str, object] = {}
    if fahrzeug:
        result["fahrzeug"] = fahrzeug
    if anzeige:
        result["anzeige"] = anzeige
    if verkaeufer:
        result["verkaeufer"] = verkaeufer
    return result


def main() -> None:
    try:
        html_path = input("HTML dosya adini girin: ").strip()
    except EOFError:
        print("Gecerli bir dosya adi girilmedi.")
        return
    if not html_path:
        print("Gecerli bir dosya adi girilmedi.")
        return
    try:
        with open(html_path, "r", encoding="utf-8") as fh:
            html = fh.read()
    except OSError as exc:  # pragma: no cover - defensive error handling
        print(f"Dosya okunamadı: {exc}")
        return

    root = parse_html(html)
    listing = build_listing(root)
    print(json.dumps(listing, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
