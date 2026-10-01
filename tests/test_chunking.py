from loreforge.services.chunking import chunk_text


def _para(n_sent: int, i: int) -> str:
    return " ".join(f"Sentence {i}-{k} walks slowly through the rain of the old city." for k in range(n_sent))


def test_chunks_respect_limit_and_end_on_sentences():
    text = "\n\n".join(_para(12, i) for i in range(20))
    chunks = chunk_text(text, limit=2500)
    assert all(len(c.text) <= 2500 for c in chunks)
    assert all(c.text.rstrip().endswith(".") for c in chunks)
    # No text lost or duplicated.
    joined = " ".join(c.text.replace("\n\n", " ") for c in chunks)
    assert joined.split() == text.replace("\n\n", " ").split()


def test_short_paragraphs_are_merged():
    text = "\n\n".join(_para(2, i) for i in range(10))
    chunks = chunk_text(text, limit=2500)
    assert len(chunks) == 1
    assert chunks[0].text.count("\n\n") == 9


def test_overlong_sentence_is_split_at_a_comma():
    long = "The bells rang, " * 300 + "and then silence."
    chunks = chunk_text(long, limit=500)
    assert all(len(c.text) <= 500 for c in chunks)
    assert all(c.text.endswith((",", ".")) for c in chunks)
