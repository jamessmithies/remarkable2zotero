from remarkable2zotero import tex


def test_decodes_ligatures_always():
    assert tex.decode("signi\x0ccant di\x0berence, di\x0ecult", False) == (
        "significant difference, difficult"
    )


def test_decodes_quotes_and_dashes_in_tex_documents():
    text = 'within it | the \\loop invariants" and signi\x0ccance { the roles'
    assert tex.is_tex_encoded([text])
    assert tex.decode(text, True) == (
        "within it — the “loop invariants” and significance – the roles"
    )


def test_joins_words_split_before_j_in_tex_documents():
    assert tex.decode("the ob jects it pro jected, a sub ject of just one", True) == (
        "the objects it projected, a subject of just one"
    )
    assert tex.decode("Thatpro jection", True) == "Thatprojection"
    assert tex.decode("the ob jects", False) == "the ob jects"


def test_leaves_ordinary_text_alone():
    text = 'a "quoted" word | {braces}'
    assert not tex.is_tex_encoded([text])
    assert tex.decode(text, False) == text
