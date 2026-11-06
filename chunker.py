import re

DEFAULT_CHUNK_SIZE = 3500
DEFAULT_CHUNK_OVERLAP = 350


def chunk_text(text, chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=DEFAULT_CHUNK_OVERLAP):
    """
    Divides text into manageable chunks preserving paragraph and sentence boundaries.
    
    Args:
        text (str): The article content to divide.
        chunk_size (int): Target maximum size of each chunk in characters.
        chunk_overlap (int): Overlap character count to retain context across boundaries.
        
    Returns:
        list[str]: Sequential list of non-empty text chunks.
    """
    if not text or not isinstance(text, str):
        return []

    text = text.strip()
    if not text:
        return []

    # If content fits in a single chunk, no division needed
    if len(text) <= chunk_size:
        return [text]

    # Ensure overlap is strictly less than chunk_size to guarantee forward progress
    if chunk_overlap >= chunk_size:
        chunk_overlap = chunk_size // 5

    # Split initial text into logical blocks (paragraphs first)
    raw_paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    # If any paragraph is larger than chunk_size, subdivide it by sentences
    blocks = []
    for para in raw_paragraphs:
        if len(para) <= chunk_size:
            blocks.append(para)
        else:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if len(s) <= chunk_size:
                    blocks.append(s)
                else:
                    # Very long sentence fallback: divide by words
                    words = s.split(" ")
                    buf = []
                    buf_len = 0
                    for w in words:
                        if buf_len + len(w) + 1 > chunk_size and buf:
                            blocks.append(" ".join(buf))
                            buf = [w]
                            buf_len = len(w)
                        else:
                            buf.append(w)
                            buf_len += len(w) + 1
                    if buf:
                        blocks.append(" ".join(buf))

    chunks = []
    current_blocks = []
    current_length = 0

    for block in blocks:
        block_len = len(block)

        # Check if adding this block exceeds target chunk size
        if current_blocks and (current_length + block_len + 2 > chunk_size):
            chunk_content = "\n\n".join(current_blocks).strip()
            if chunk_content:
                chunks.append(chunk_content)

            # Determine overlap blocks from the end of current_blocks
            overlap_blocks = []
            overlap_len = 0
            for b in reversed(current_blocks):
                if overlap_len + len(b) <= chunk_overlap:
                    overlap_blocks.insert(0, b)
                    overlap_len += len(b) + 2
                else:
                    break

            current_blocks = list(overlap_blocks)
            current_length = sum(len(b) + 2 for b in current_blocks)

        current_blocks.append(block)
        current_length += block_len + 2

    # Add remaining blocks as the final chunk
    if current_blocks:
        final_chunk = "\n\n".join(current_blocks).strip()
        # Avoid duplicate if final_chunk matches the last chunk
        if final_chunk and (not chunks or final_chunk != chunks[-1]):
            chunks.append(final_chunk)

    return [c for c in chunks if c.strip()]
