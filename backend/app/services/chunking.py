from dataclasses import dataclass
import re


PAGE_RE = re.compile(r"^\[Page\s+(\d+)\]\s*$", re.IGNORECASE)
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
PARAGRAPH_RE = re.compile(r"\n\s*\n+")


@dataclass(frozen=True)
class ChunkData:
    text: str
    page: int | None
    heading: str | None
    metadata: dict


@dataclass
class _Block:
    text: str
    page: int | None
    heading: str | None
    is_heading: bool


def build_chunks(
    text: str,
    metadata: dict,
    max_chars: int = 1800,
    overlap_chars: int = 200,
) -> list[ChunkData]:
    heading_texts = _metadata_heading_texts(metadata)
    chunks: list[ChunkData] = []
    current_parts: list[str] = []
    current_page: int | None = None
    current_heading: str | None = None
    current_strategy = "heading_paragraph"

    def flush() -> None:
        nonlocal current_parts, current_page, current_heading, current_strategy
        chunk_text = "\n\n".join(part for part in current_parts if part.strip()).strip()
        if not chunk_text:
            current_parts = []
            return
        chunks.append(
            ChunkData(
                text=chunk_text,
                page=current_page,
                heading=current_heading,
                metadata={
                    "char_count": len(chunk_text),
                    "strategy": current_strategy,
                },
            )
        )
        current_parts = []
        current_strategy = "heading_paragraph"

    for block in _iter_blocks(text, heading_texts):
        if block.is_heading and current_parts:
            flush()
        if block.page is not None and current_parts and block.page != current_page:
            flush()

        if block.heading:
            current_heading = block.heading
        if block.page is not None:
            current_page = block.page

        if len(block.text) > max_chars:
            flush()
            chunks.extend(_split_long_block(block, max_chars, overlap_chars))
            current_page = block.page if block.page is not None else current_page
            current_heading = block.heading or current_heading
            continue

        candidate = "\n\n".join([*current_parts, block.text]).strip()
        if current_parts and len(candidate) > max_chars:
            flush()

        current_parts.append(block.text)
        if block.page is not None and current_page is None:
            current_page = block.page
        if block.heading and current_heading is None:
            current_heading = block.heading

    flush()
    return chunks


def _iter_blocks(text: str, heading_texts: set[str]) -> list[_Block]:
    blocks: list[_Block] = []
    current_page: int | None = None
    current_heading: str | None = None

    for raw_block in PARAGRAPH_RE.split(text.strip()):
        lines: list[str] = []
        block_page = current_page
        for raw_line in raw_block.splitlines():
            line = raw_line.strip()
            page_match = PAGE_RE.match(line)
            if page_match:
                current_page = int(page_match.group(1))
                block_page = current_page
                continue
            lines.append(raw_line.rstrip())

        block_text = "\n".join(lines).strip()
        if not block_text:
            continue

        heading = _detect_heading(block_text, heading_texts)
        is_heading = heading is not None and len(block_text.splitlines()) == 1
        if heading:
            current_heading = heading

        blocks.append(
            _Block(
                text=block_text,
                page=block_page,
                heading=heading or current_heading,
                is_heading=is_heading,
            )
        )

    return blocks


def _split_long_block(block: _Block, max_chars: int, overlap_chars: int) -> list[ChunkData]:
    chunks: list[ChunkData] = []
    start = 0
    part_index = 0
    text = block.text
    overlap = max(0, min(overlap_chars, max_chars // 2))

    while start < len(text):
        end = min(len(text), start + max_chars)
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(
                ChunkData(
                    text=chunk_text,
                    page=block.page,
                    heading=block.heading,
                    metadata={
                        "char_count": len(chunk_text),
                        "strategy": "char_window",
                        "part_index": part_index,
                    },
                )
            )
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
        part_index += 1

    return chunks


def _detect_heading(text: str, heading_texts: set[str]) -> str | None:
    first_line = text.strip().splitlines()[0].strip()
    match = MARKDOWN_HEADING_RE.match(first_line)
    if match:
        return match.group(2).strip()
    if first_line in heading_texts:
        return first_line
    return None


def _metadata_heading_texts(metadata: dict) -> set[str]:
    headings = metadata.get("headings") or []
    values: set[str] = set()
    for heading in headings:
        if isinstance(heading, dict) and heading.get("text"):
            values.add(str(heading["text"]).strip())
    return values
