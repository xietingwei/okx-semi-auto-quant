from qis.okx import OkxClient


def test_infer_inst_type() -> None:
    assert OkxClient._infer_inst_type("BTC-USDT-SWAP") == "SWAP"
    assert OkxClient._infer_inst_type("BTC-USDT") == "SPOT"


def test_contract_size_from_base_rounds_down() -> None:
    assert OkxClient.contract_size_from_base(0.027525, "0.01", "1", "1") == "2"
    assert OkxClient.contract_size_from_base(1.234, "0.1", "0.1", "0.1") == "12.3"
