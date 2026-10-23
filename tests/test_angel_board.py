import time

import pytest

from backend.app.services.data import angel_board, live_prices as lp, market_data


@pytest.fixture
def board(monkeypatch):
    monkeypatch.setattr(angel_board, "available", lambda: True)
    monkeypatch.setattr(market_data.bars, "latest_date", lambda: None)
    lp._board.clear()
    now = time.time()
    for sym, price, prev, vol in [("AAA", 110.0, 100.0, 1_000_000), ("BBB", 90.0, 100.0, 5_000_000), ("CCC", 100.0, 100.0, 10)]:
        lp._board[sym] = {"price": price, "prev_close": prev, "open": prev, "high": price, "low": price, "volume": vol,
                          "avg_price": price, "feed_time": None, "w52_high": None, "w52_low": None, "at": now}
    yield
    lp._board.clear()


def test_movers_come_from_angel_board_without_db(board):
    s = market_data.snapshot()
    assert s.mode == "live" and s.n_dates == 0
    g = market_data.movers("gainers", min_turnover_cr=0)
    assert [r["symbol"] for r in g["rows"]] == ["AAA"]
    assert [r["symbol"] for r in market_data.movers("losers", min_turnover_cr=0)["rows"]] == ["BBB"]
    assert market_data.movers("most_active", min_turnover_cr=0)["rows"][0]["symbol"] == "BBB"


def test_empty_board_says_loading(monkeypatch):
    monkeypatch.setattr(angel_board, "available", lambda: True)
    monkeypatch.setattr(market_data.bars, "latest_date", lambda: None)
    lp._board.clear()
    with pytest.raises(market_data.NoMarketData, match="still loading"):
        market_data.snapshot()