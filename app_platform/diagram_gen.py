"""Генерация диаграммы проекта в стиле gitdiagram (https://github.com/ahmedkhaleel2004/gitdiagram).

gitdiagram строит визуальные архитектуры репозиториев как mermaid-диаграммы
(см. third_party/gitdiagram/src/server/generate/graph.ts и mermaid-validator.ts)
и рендерит их через mermaid.js + экспорт в PNG/SVG.

Мы воспроизводим ту же идею на стороне платформы:
  1. по дереву gitingest строится mermaid `flowchart TD`, где папки — блоки,
     а файлы — узлы внутри них (подграфы), связи parent -> child;
  2. mermaid-код рендерится в браузере через mermaid.js (тот же рендерер,
     что использует gitdiagram — см. dependencies в package.json gitdiagram);
  3. готовую SVG-картинку пользователь может сохранить на свой ПК.
"""
from __future__ import annotations

import re

# Признак «настоящей» mermaid-диаграммы: наличие узлов/связей (a --> b).
# staticdiagram для пустого проекта отдаёт лишь заголовок и classDef без узлов —
# такой результат бесполезен, и его нужно заменять на дерево файлов.
_EDGE_RE = re.compile(r"-->|---|==>", re.MULTILINE)


def mermaid_has_nodes(code: str) -> bool:
    """True, если в mermaid-коде есть хотя бы одна связь/узел (а не только стили).

    Используется как общий критерий для fallback'а на дерево и в консоли
    (getting_started/core.py), и в веб-панели (app_platform/main.py).
    """
    if not code or not code.strip():
        return False
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("classDef", "class ", "%%", "%%{")):
            continue
        if _EDGE_RE.search(stripped):
            return True
    return False


# Символы tree-вывода gitingest (├── └── │ и др.)


def _sanitize(label: str) -> str:
    return label.replace('"', "'").replace("{", "(").replace("}", ")")


def _parse_line(raw: str) -> tuple[int, str] | None:
    """Возвращает (глубина, имя) для строки дерева вида '│   ├── file.py'."""
    pos = max(raw.find("├"), raw.find("└"))
    if pos < 0:
        return None
    name = raw[pos + 4:].strip()
    depth = pos // 4 + 1
    return depth, name


def tree_to_mermaid(tree_str: str, max_nodes: int = 250) -> str:
    """Преобразует дерево каталогов (вывод gitingest) в mermaid flowchart."""
    lines = [ln for ln in tree_str.splitlines() if ln.strip()]
    if not lines:
        return "flowchart TD\n    root[Пустой проект]"

    # Первая строка — корень дерева. gitingest печатает её как '└── name/'
    root_raw = lines[0].strip()
    parsed_root = _parse_line(root_raw)
    root_name = _sanitize((parsed_root[1] if parsed_root else root_raw).rstrip("/")) or "root"
    node_id = 0
    # стек: (depth, node_id, is_dir)
    stack: list[tuple[int, int, bool]] = [(0, 0, True)]
    edges: list[str] = []
    dir_nodes: set[int] = set()
    used = 0

    def new_id() -> int:
        nonlocal node_id
        node_id += 1
        return node_id

    for raw in lines[1:]:
        parsed = _parse_line(raw)
        if not parsed:
            continue
        depth, name = parsed
        is_dir = name.endswith("/")
        label = _sanitize(name.rstrip("/"))

        while stack and stack[-1][0] >= depth:
            stack.pop()
        if not stack:
            stack.append((0, 0, True))
        parent = stack[-1][1]

        if used >= max_nodes:
            break
        used += 1
        nid = new_id()
        if is_dir:
            dir_nodes.add(nid)
            edges.append(f'    {parent} --> {nid}[/"📁 {label}"/]')
        else:
            edges.append(f"    {parent} --> {nid}(&quot;📄 {label}&quot;)")
        stack.append((depth, nid, is_dir))

    # Собираем id папок/файлов по типам для стилей
    file_ids = [nid for nid in range(1, node_id + 1) if nid not in dir_nodes]

    body = "\n".join(edges)
    style = (
        "\n    classDef dir fill:#1f6feb22,stroke:#1f6feb,color:#58a6ff;"
        "\n    classDef file fill:#21262d,stroke:#30363d,color:#c9d1d9;"
    )
    classes = "\n    class 0," + ",".join(str(i) for i in sorted(dir_nodes)) + " dir;"
    if file_ids:
        classes += "\n    class " + ",".join(str(i) for i in file_ids) + " file;"

    header = "flowchart TD\n" f'    0[/"🗂 {root_name}"/]'
    return header + "\n" + body + style + classes


def mermaid_markdown(code: str) -> str:
    """Безопасно экранирует mermaid-код для вставки в HTML (рендеряется mermaid.js)."""
    escaped = (
        code.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return escaped
