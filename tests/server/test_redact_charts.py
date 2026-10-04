"""public_view of charts that follow a table: a following chart's shared x and series, the rebase flag,
window marks, and an empty chart that explains itself."""
import json

from monitor.server.redact import public_view


def P(**kw):
    return {"vis": "public", **kw}


def test_a_following_chart_keeps_shared_x_series_and_window_marks():
    """A chart following a table: per-row blocks carry only their own series; the panel's x and shared
    series (a book, its benchmark), the rebase flag and the train/val/test marks stay public."""
    p = P(id="eq", n=1, title="EQUITY", type="chart", follows="reg", rebase=True,
          vlines=[1640995200, 1704067200], vlabels=["VAL", "TEST"], x=[1, 2, 3],
          series=[P(name="BOOK", y=[100.0, 101.0, 102.0]), {"name": "SECRET", "y": [987654.32, 1.0, 1.0]}],
          series_by_key={"1": P(series=[P(name="REC", y=[100.0, 99.0, None])]),
                         "2": {"series": [P(name="HIDDEN", y=[987654.32, 1.0, 1.0])]},
                         "3": P(series=[{"name": "NOVIS", "y": [1.0, 2.0, 3.0]}])})
    out = public_view({"panels": [p]})["panels"][0]
    assert out["x"] == [1, 2, 3] and [s["name"] for s in out["series"]] == ["BOOK"]
    assert out["series_by_key"] == {"1": {"series": [{"name": "REC", "y": [100.0, 99.0, None], "vis": "public"}]}}
    assert out["rebase"] is True and out["vlines"] == [1640995200, 1704067200] and out["vlabels"] == ["VAL", "TEST"]
    bad = public_view({"panels": [{**p, "vlines": [{"x": 1}], "rebase": "yes"}]})["panels"][0]
    assert "vlines" not in bad and "rebase" not in bad
    assert "987654.32" not in json.dumps(out)


def test_an_empty_chart_survives_only_when_its_header_says_why():
    empty = P(id="live", n=2, title="LIVE", type="chart", x=[], series=[], context=P(text="3 LIVE SESSIONS"))
    assert public_view({"panels": [empty]})["panels"] == [
        {"id": "live", "n": 2, "title": "LIVE", "type": "chart", "vis": "public",
         "context": {"text": "3 LIVE SESSIONS", "vis": "public"}}]
    assert public_view({"panels": [{**empty, "context": {"text": "private"}}]})["panels"] == []


def test_a_following_block_keeps_its_note_and_the_shared_series_it_replaces():
    """A curve-less row's block is only a note (the shared book and benchmark still draw); the row that is
    the book names the shared series it replaces (`hide`), so the book is not drawn twice."""
    p = P(id="eq", n=1, title="EQUITY", type="chart", follows="reg", x=[1, 2], series=[P(name="BOOK", y=[1.0, 2.0])],
          series_by_key={"1": P(series=[P(name="ENS", role="primary", y=[1.0, 2.0])], hide=["BOOK"]),
                         "2": P(series=[], note="NO CURVE FOR Your portfolio"),
                         "3": P(series=[], note={"x": 987654.32}),
                         "4": P(series=[P(name="R", y=[1.0, 1.0])], hide=[{"bad": 1}])})
    out = public_view({"panels": [p]})["panels"][0]["series_by_key"]
    assert out["1"]["hide"] == ["BOOK"] and out["2"] == {"series": [], "note": "NO CURVE FOR Your portfolio"}
    assert "3" not in out and "hide" not in out["4"]
