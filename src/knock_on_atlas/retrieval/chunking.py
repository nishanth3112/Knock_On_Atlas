# Words, not model tokens: English filings run about 1.35 BGE tokens per word, so a
# full chunk (tail + body) stays under BGE's 512-token input.
WORDS_PER_CHUNK = 260  # about 350 tokens
OVERLAP_WORDS = 40  # about 50 tokens


def chunk_section(text: str) -> list[str]:
    """Pack a section's paragraphs (one per line) into overlapping chunks.

    A chunk never mixes sections, since it is built from one section's text. Each
    chunk after the first starts with the last OVERLAP_WORDS words of the one
    before, so a sentence cut at a boundary is whole in one of them.
    """
    pieces = []
    for line in text.split("\n"):
        words = line.split()
        # A paragraph longer than a chunk is cut into chunk-sized pieces.
        pieces += [
            words[i : i + WORDS_PER_CHUNK]
            for i in range(0, len(words), WORDS_PER_CHUNK)
        ]
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    for piece in pieces:
        if current and size + len(piece) > WORDS_PER_CHUNK:
            chunks.append(current)
            tail = " ".join(current).split()[-OVERLAP_WORDS:]
            current, size = [" ".join(tail)], len(tail)
        current.append(" ".join(piece))
        size += len(piece)
    if current:
        chunks.append(current)
    return ["\n".join(chunk) for chunk in chunks]
