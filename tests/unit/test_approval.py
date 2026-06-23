from invoicer.adapters.stub_approval import StubApprovalChannel
from invoicer.ports import ApprovalChannel

_PAYLOAD = {
    "number": "FV/1",
    "seller": "ACME",
    "seller_nip": "5260001246",
    "total_gross": "1230.00",
    "currency": "PLN",
    "treatment": "krajowa",
}


def test_stub_records_calls():
    ch = StubApprovalChannel()
    ch.request_approval(_PAYLOAD)
    assert ch.sent == [_PAYLOAD]


def test_stub_satisfies_approval_channel_protocol():
    assert isinstance(StubApprovalChannel(), ApprovalChannel)
