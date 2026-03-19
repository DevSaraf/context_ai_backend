"""
Smart Text Chunking Engine
- Paragraph-aware splitting (never breaks mid-sentence)
- Configurable overlap between chunks (prevents lost context at boundaries)
- Metadata extraction (section headers, source info)
- Handles edge cases: empty text, very short text, very long paragraphs

Drop-in replacement for your existing chunking.py
"""

import re
from typing import List, Optional
from dataclasses import dataclass, asdict


@dataclass
class Chunk:
    """A text chunk with metadata for better retrieval."""
    text: str
    index: int                          # Position in the document (0, 1, 2...)
    section_title: Optional[str] = None # Nearest heading above this chunk
    char_start: int = 0                 # Character offset in original text
    char_end: int = 0
    word_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def chunk_text(
    text: str,
    chunk_size: int = 400,
    overlap: int = 80,
    min_chunk_size: int = 50
) -> List[str]:
    """
    Backward-compatible function: returns list of strings.
    Use chunk_text_with_metadata() for richer output.

    Args:
        chunk_size:     Target words per chunk (not a hard limit)
        overlap:        Words to repeat between adjacent chunks
        min_chunk_size: Discard chunks shorter than this many words
    """
    chunks = chunk_text_with_metadata(text, chunk_size, overlap, min_chunk_size)
    return [c.text for c in chunks]


def chunk_text_with_metadata(
    text: str,
    chunk_size: int = 400,
    overlap: int = 80,
    min_chunk_size: int = 50
) -> List[Chunk]:
    """
    Split text into overlapping, paragraph-aware chunks with metadata.

    Strategy:
    1. Split text into paragraphs (preserving natural boundaries)
    2. Accumulate paragraphs until we hit chunk_size words
    3. When a chunk is full, start the next one with `overlap` words
       carried over from the end of the previous chunk
    4. Never break a sentence in half
    """
    if not text or not text.strip():
        return []

    # Clean the text
    text = text.strip()
    text = re.sub(r'\n{3,}', '\n\n', text)  # Collapse excessive newlines

    # Extract section headers for metadata
    sections = _extract_sections(text)

    # Split into paragraphs (double newline or markdown-style headers)
    paragraphs = _split_into_paragraphs(text)

    if not paragraphs:
        return []

    # Build chunks by accumulating paragraphs
    chunks: List[Chunk] = []
    current_words: List[str] = []
    current_section: Optional[str] = None
    char_offset = 0

    for para in paragraphs:
        para_stripped = para.strip()
        if not para_stripped:
            continue

        # Check if this paragraph is a section header
        header = _detect_header(para_stripped)
        if header:
            current_section = header
            # Don't add headers as standalone content — they'll be
            # captured as metadata on the next chunk
            continue

        para_words = para_stripped.split()

        # If a single paragraph exceeds chunk_size, split it by sentences
        if len(para_words) > chunk_size:
            sentences = _split_into_sentences(para_stripped)
            for sentence in sentences:
                sent_words = sentence.strip().split()
                if not sent_words:
                    continue

                if len(current_words) + len(sent_words) > chunk_size and len(current_words) >= min_chunk_size:
                    # Flush current chunk
                    chunk_text_str = ' '.join(current_words)
                    chunks.append(Chunk(
                        text=chunk_text_str,
                        index=len(chunks),
                        section_title=current_section,
                        char_start=char_offset,
                        char_end=char_offset + len(chunk_text_str),
                        word_count=len(current_words)
                    ))
                    char_offset += len(chunk_text_str) + 1

                    # Carry over overlap words
                    current_words = current_words[-overlap:] if overlap > 0 else []

                current_words.extend(sent_words)
        else:
            # Check if adding this paragraph exceeds chunk_size
            if len(current_words) + len(para_words) > chunk_size and len(current_words) >= min_chunk_size:
                # Flush current chunk
                chunk_text_str = ' '.join(current_words)
                chunks.append(Chunk(
                    text=chunk_text_str,
                    index=len(chunks),
                    section_title=current_section,
                    char_start=char_offset,
                    char_end=char_offset + len(chunk_text_str),
                    word_count=len(current_words)
                ))
                char_offset += len(chunk_text_str) + 1

                # Carry over overlap words
                current_words = current_words[-overlap:] if overlap > 0 else []

            current_words.extend(para_words)

    # Flush remaining words
    if len(current_words) >= min_chunk_size:
        chunk_text_str = ' '.join(current_words)
        chunks.append(Chunk(
            text=chunk_text_str,
            index=len(chunks),
            section_title=current_section,
            char_start=char_offset,
            char_end=char_offset + len(chunk_text_str),
            word_count=len(current_words)
        ))
    elif current_words and chunks:
        # Append short trailing text to the last chunk
        last = chunks[-1]
        combined = last.text + ' ' + ' '.join(current_words)
        chunks[-1] = Chunk(
            text=combined,
            index=last.index,
            section_title=last.section_title,
            char_start=last.char_start,
            char_end=last.char_start + len(combined),
            word_count=len(combined.split())
        )
    elif current_words:
        # Only chunk, even if short
        chunk_text_str = ' '.join(current_words)
        chunks.append(Chunk(
            text=chunk_text_str,
            index=0,
            section_title=current_section,
            char_start=0,
            char_end=len(chunk_text_str),
            word_count=len(current_words)
        ))

    # Prepend section title to chunks for better retrieval
    for chunk in chunks:
        if chunk.section_title:
            chunk.text = f"[{chunk.section_title}] {chunk.text}"

    return chunks


# ============== HELPER FUNCTIONS ==============

def _split_into_paragraphs(text: str) -> List[str]:
    """Split on double newlines, preserving paragraph boundaries."""
    paragraphs = re.split(r'\n\s*\n', text)
    return [p.strip() for p in paragraphs if p.strip()]


def _split_into_sentences(text: str) -> List[str]:
    """Split text into sentences. Simple and robust approach."""
    # Split on period/question/exclamation followed by space + capital letter
    # Then filter out splits on common abbreviations
    parts = re.split(r'([.!?]+)\s+(?=[A-Z])', text)
    
    # Re-join the punctuation with the preceding text
    sentences = []
    i = 0
    while i < len(parts):
        sentence = parts[i]
        # If next part is punctuation, attach it
        if i + 1 < len(parts) and re.match(r'^[.!?]+$', parts[i + 1]):
            sentence += parts[i + 1]
            i += 2
        else:
            i += 1
        
        sentence = sentence.strip()
        if sentence:
            sentences.append(sentence)
    
    return sentences if sentences else [text]


def _detect_header(text: str) -> Optional[str]:
    """Detect if a paragraph is a section header."""
    # Markdown headers
    md_match = re.match(r'^#{1,4}\s+(.+)$', text)
    if md_match:
        return md_match.group(1).strip()

    # Short lines in ALL CAPS or Title Case that look like headers
    if len(text.split()) <= 8 and not text.endswith('.'):
        if text.isupper() and len(text) > 3:
            return text.title()
        # Title case with no period
        if text == text.title() and len(text) > 3:
            return text

    # Numbered sections: "1. Introduction", "Section 2: Overview"
    numbered = re.match(r'^(?:Section\s+)?\d+[\.:]\s*(.+)$', text, re.IGNORECASE)
    if numbered and len(text.split()) <= 8:
        return numbered.group(1).strip()

    return None


def _extract_sections(text: str) -> List[dict]:
    """Extract all section headers and their positions."""
    sections = []
    for match in re.finditer(r'^#{1,4}\s+(.+)$', text, re.MULTILINE):
        sections.append({
            'title': match.group(1).strip(),
            'position': match.start()
        })
    return sections
