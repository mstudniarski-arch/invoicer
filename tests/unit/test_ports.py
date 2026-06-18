from invoicer.ports import AccountingSink, EmailSource


def test_email_source_accepts_conforming_impl():
    class _Fake:
        def fetch(self, sender: str):
            return []

    assert isinstance(_Fake(), EmailSource)


def test_email_source_rejects_nonconforming_impl():
    class _NoFetch:
        pass

    assert not isinstance(_NoFetch(), EmailSource)


def test_accounting_sink_accepts_conforming_impl():
    class _Fake:
        def post(self, payload):
            return None

    assert isinstance(_Fake(), AccountingSink)


def test_accounting_sink_rejects_nonconforming_impl():
    class _NoPost:
        pass

    assert not isinstance(_NoPost(), AccountingSink)
