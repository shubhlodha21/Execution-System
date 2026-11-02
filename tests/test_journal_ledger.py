from decimal import Decimal as D

from execsys.journal.journal import ticker_file_name
from execsys.journal.ledger import Ledger


DAY = "2026-11-02"


def test_average_cost_realised_pnl_and_commission():
    lg = Ledger()
    lg.apply_fill(DAY, "ADI.XNAS", "BUY", D(10), D(100), D("1.00"))
    rec = lg.apply_fill(DAY, "ADI.XNAS", "BUY", D(10), D(110), D("1.00"))
    assert rec.position_after == 20 and rec.avg_cost_after == 105 and rec.realized_gross == 0
    rec = lg.apply_fill(DAY, "ADI.XNAS", "SELL", D(15), D(120), D("1.50"))
    assert rec.realized_gross == D(225)  # 15 * (120 - 105)
    assert rec.realized_net == D("223.50")
    assert rec.day_realized_net == D("225") - D("3.50")  # all commissions of the day
    assert rec.position_after == 5 and rec.avg_cost_after == 105


def test_flip_and_short_pnl():
    lg = Ledger()
    lg.apply_fill(DAY, "MSFT.XNAS", "BUY", D(10), D(400), D(0))
    rec = lg.apply_fill(DAY, "MSFT.XNAS", "SELL", D(20), D(410), D(0))  # close 10, open short 10 @410
    assert rec.realized_gross == D(100) and rec.position_after == -10 and rec.avg_cost_after == 410
    rec = lg.apply_fill(DAY, "MSFT.XNAS", "BUY", D(10), D(405), D(0))  # cover short
    assert rec.realized_gross == D(50) and rec.position_after == 0


def test_days_and_tickers_are_separate():
    lg = Ledger()
    lg.apply_fill("2026-11-02", "ADI.XNAS", "BUY", D(10), D(100), D(0))
    lg.apply_fill("2026-11-03", "ADI.XNAS", "SELL", D(10), D(101), D(0))  # closes yesterday's position
    lg.apply_fill("2026-11-03", "MSFT.XNAS", "BUY", D(5), D(400), D(0))
    day1 = {r[1]: r for r in lg.summary_rows("2026-11-02")}
    day2 = {r[1]: r for r in lg.summary_rows("2026-11-03")}
    assert day1["ADI.XNAS"][7] == "0.00"
    assert day2["ADI.XNAS"][7] == "10.00" and day2["ADI.XNAS"][10] == "0"
    assert day2["MSFT.XNAS"][10] == "5" and day2["MSFT.XNAS"][2] == "1"


def test_seed_from_ib_then_close():
    lg = Ledger()
    lg.seed("ADI.XNAS", D(10), D("230.50"))
    rec = lg.apply_fill(DAY, "ADI.XNAS", "SELL", D(10), D("231.00"), D("0.35"))
    assert rec.realized_gross == D("5.00") and rec.realized_net == D("4.65")


def test_commission_in_other_currency_not_subtracted():
    lg = Ledger()
    lg.apply_fill(DAY, "USD/JPY.IDEALPRO", "BUY", D(20000), D(150), D(2), commission_in_quote_ccy=False)
    rec = lg.apply_fill(DAY, "USD/JPY.IDEALPRO", "SELL", D(20000), D("150.10"), D(2), commission_in_quote_ccy=False)
    assert rec.realized_net == rec.realized_gross == D(2000)


def test_roundtrip_and_file_names():
    lg = Ledger()
    lg.apply_fill(DAY, "ADI.XNAS", "BUY", D(3), D("99.5"), D("0.1"))
    restored = Ledger.from_dict(lg.to_dict({DAY}))
    assert restored.summary_rows(DAY) == lg.summary_rows(DAY)
    assert Ledger.from_dict(lg.to_dict(set())).days == {}
    assert ticker_file_name("ADI.XNAS") == "ADI.csv" and ticker_file_name("EUR/USD.IDEALPRO") == "EUR-USD.csv"
