from confluo_crm.knowledge import any_word_query, chunk_text


def test_short_item_is_one_chunk_with_its_title() -> None:
    assert chunk_text("Parking", "Free parking behind the salon.") == [
        "Parking\nFree parking behind the salon."
    ]


def test_long_text_splits_on_paragraphs_and_sentences() -> None:
    body = "\n\n".join(f"Paragraph {i}. " + "Words go here. " * 30 for i in range(4))
    chunks = chunk_text("Policy", body, max_chars=300)
    assert len(chunks) > 4
    assert all(c.startswith("Policy\n") for c in chunks)
    assert all(len(c) <= len("Policy\n") + 300 for c in chunks)
    # Nothing is lost.
    joined = " ".join(c.removeprefix("Policy\n") for c in chunks)
    assert joined.count("Words go here.") == 120


def test_small_paragraphs_are_merged() -> None:
    chunks = chunk_text("Hours", "Mon 9-18\n\nTue 9-18\n\nWed closed", max_chars=300)
    assert chunks == ["Hours\nMon 9-18\n\nTue 9-18\n\nWed closed"]


def test_a_huge_sentence_is_cut() -> None:
    chunks = chunk_text("X", "a" * 1000, max_chars=300)
    assert [len(c) for c in chunks] == [302, 302, 302, 102]


def test_question_becomes_an_any_word_query() -> None:
    assert any_word_query("Is there free parking?") == "free:* | parking:*"
    assert any_word_query("Où se garer à Gent ?") == "garer:* | gent:* | où:*"
    # Filler words don't count, so the word that matters decides the ranking.
    assert any_word_query("How much is a colour and when are you open?") == "colour:* | open:*"
    assert any_word_query("?!") == ""
