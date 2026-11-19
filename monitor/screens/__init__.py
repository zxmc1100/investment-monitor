"""Screen registry: every mnemonic the terminal knows, in key order (PORT = 1 … TRADES = 6, BOOKS = 7), plus the
screens a local add-on registers (monitor.plugins applies them). `soon` screens appear greyed in the
command bar and the key strip until they are built."""
from monitor import plugins
from monitor.screens import alrt, books, mkt, opt, port, risk, sec, trades
from monitor.screens.base import Ctx, Screen  # noqa: F401 — public API of the package

SCREENS: dict[str, Screen] = {s.id: s for s in (
    port.SCREEN,
    opt.SCREEN,
    risk.SCREEN,
    sec.SCREEN,
    mkt.SCREEN,
    alrt.SCREEN,
    trades.SCREEN,
    books.SCREEN,
    trades.BOOK,
)}
plugins.apply_screens(SCREENS)
