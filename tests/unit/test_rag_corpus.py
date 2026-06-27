from invoicer.rag.corpus import Chunk, load_corpus


def _write(dir_path, name, body):
    (dir_path / name).write_text(body, encoding="utf-8")


def test_loads_frontmatter_and_paragraph_chunks(tmp_path):
    _write(
        tmp_path,
        "vat-art-28b.md",
        "---\n"
        "source_id: vat-art-28b\n"
        'article_ref: "art. 28b ust. 1"\n'
        'title: "Ustawa o VAT - art. 28b"\n'
        'url: "https://isap.sejm.gov.pl/x"\n'
        "kind: ustawa\n"
        "---\n"
        "Pierwszy akapit przepisu.\n\nDrugi akapit przepisu.\n",
    )
    chunks = load_corpus(tmp_path)
    assert len(chunks) == 2
    assert all(isinstance(c, Chunk) for c in chunks)
    assert chunks[0].source_id == "vat-art-28b"
    assert chunks[0].article_ref == "art. 28b ust. 1"  # cudzyslowy usuniete
    assert chunks[0].title == "Ustawa o VAT - art. 28b"
    assert chunks[0].kind == "ustawa"
    assert chunks[0].text == "Pierwszy akapit przepisu."
    assert chunks[1].text == "Drugi akapit przepisu."


def test_content_hash_is_stable_and_text_derived(tmp_path):
    _write(
        tmp_path,
        "a.md",
        "---\nsource_id: a\narticle_ref: a1\ntitle: A\nurl: u\nkind: ustawa\n---\nTresc.\n",
    )
    [chunk] = load_corpus(tmp_path)
    assert (
        chunk.content_hash
        == Chunk(
            source_id="a", article_ref="a1", title="A", url="u", kind="ustawa", text="Tresc."
        ).content_hash
    )


def test_ignores_blank_paragraphs(tmp_path):
    _write(
        tmp_path,
        "a.md",
        "---\nsource_id: a\narticle_ref: a1\ntitle: A\nurl: u\nkind: ustawa\n---\n\nX\n\n\n\nY\n\n",
    )
    chunks = load_corpus(tmp_path)
    assert [c.text for c in chunks] == ["X", "Y"]
