"""Public redaction — an allow-list by construction.

public_view() builds a NEW payload, copying only structural keys and nodes explicitly
marked vis="public". A node without vis is private; a key not on the allow-lists below is
dropped — so a field someone adds later without thinking about the public build never leaks.

Fail-closed policy: nested values must be JSON scalars (str, int, float, bool, None) or a
flat list of scalars; anything else is omitted. Row flags: only _stale survives as bool, and
_fmt (a per-row format override) only as strings for public columns.
Closed positions (rows with truthy _closed) are transaction history — dropped entirely.
"""
import copy

PUBLIC = "public"
_PANEL_KEYS = ("id", "n", "title", "type", "span", "rows_span", "key", "sort", "drives",
               "follows", "ranges", "legend", "yfmt", "xfmt", "rebase", "vlines", "vlabels",
               "horizons", "horizon", "today_idx", "cursor")
_NODE_KEYS = ("k", "label", "v", "v2", "fmt", "vis", "align", "op", "sep", "strong", "h", "body",
              "name", "y", "kind", "role", "text", "x", "hl", "lo", "wide", "hi", "color", "dash", "nolegend", "at")
_LISTS = {"kpi": "items", "ledger": "lines", "bars": "items", "scatter": "series"}
# Maximized detail lists. PORT SUMMARY `years` nodes carry a euro `gain`, which is not on
# _NODE_KEYS — the public view keeps the year's percentages (v, v2) and drops it.
_EXTRA_LISTS = {"bars": ("sectors", "countries", "regions"), "kpi": ("years",)}


def _is_scalar(x) -> bool:
    """True if x is a JSON scalar: str, int, float, bool, None."""
    return isinstance(x, (str, int, float, bool, type(None)))


def _scalar_or_scalar_list(x) -> bool:
    """True if x is a scalar or a flat list of scalars."""
    if _is_scalar(x):
        return True
    if isinstance(x, list):
        return all(_is_scalar(item) for item in x)
    return False


def _pub(node) -> bool:
    return isinstance(node, dict) and node.get("vis") == PUBLIC


def _nodes(nodes) -> list[dict]:
    out = []
    for n in nodes or []:
        if not _pub(n):
            continue
        row = {}
        for k in _NODE_KEYS:
            if k in n and _scalar_or_scalar_list(n[k]):
                row[k] = copy.deepcopy(n[k])
        out.append(row)
    return out


def _rows(rows, keep: list[str]) -> list[dict]:
    """`rows` with the public columns `keep` only (closed rows left out; a stale mark and per-row formats of public
    columns kept)."""
    out = []
    for r in rows:
        if not isinstance(r, dict) or r.get("_closed"):
            continue
        row = {}
        for k in keep:
            val = r.get(k)
            if _scalar_or_scalar_list(val):
                row[k] = copy.deepcopy(val)
            else:
                row[k] = None
        if r.get("_stale"):
            row["_stale"] = True
        fmts = r.get("_fmt")                      # per-row display format of a public column
        if isinstance(fmts, dict):
            fmts = {k: v for k, v in fmts.items() if k in keep and isinstance(v, str)}
            if fmts:
                row["_fmt"] = fmts
        out.append(row)
    return out


def _table(p: dict, out: dict) -> dict | None:
    cols = _nodes(p.get("cols"))
    # Only keep columns with str keys
    keep = [c["k"] for c in cols if isinstance(c.get("k"), str)]
    if p.get("key") not in keep:
        return None
    out["cols"] = [c for c in cols if isinstance(c.get("k"), str)]
    out["rows"] = _rows(p.get("rows", []), keep)
    # a table following another's cursor (OPT's REBALANCE?): each key's rows, public columns only; their header
    # strings may carry euros, so none travels
    by_key = p.get("rows_by_key")
    if p.get("follows") and isinstance(by_key, dict):
        out["rows_by_key"] = {str(k): {"rows": _rows(v.get("rows", []), keep), "context": ""}
                              for k, v in by_key.items() if isinstance(v, dict)}
    if p.get("total"):
        total = {}
        for k in keep:
            val = p["total"].get(k)
            if _scalar_or_scalar_list(val):
                total[k] = copy.deepcopy(val)
            else:
                total[k] = None
        out["total"] = total
    # Validate sort: must be [col, "asc"|"desc"] where col is in keep
    if out.get("sort"):
        sort_val = out["sort"]
        if isinstance(sort_val, list) and len(sort_val) >= 2 and sort_val[0] in keep and sort_val[1] in ("asc", "desc"):
            out["sort"] = [sort_val[0], sort_val[1]]
        else:
            out["sort"] = [p["key"], "asc"]
    return out


def _series_block(block: dict) -> dict | None:
    series = _nodes(block.get("series"))
    if not series:
        return None
    x = block.get("x", [])
    if not isinstance(x, list) or not all(isinstance(v, (int, float)) for v in x):
        return None
    return {"x": list(x), "series": series}


def _chart(p: dict, out: dict) -> dict | None:
    if "series_by_key" in p:
        by = {}
        for k, v in p["series_by_key"].items():
            if v.get("vis") != PUBLIC:               # absent = private
                continue
            if "x" in v:
                b = _series_block(v)
            else:                                    # shares the panel's x (a chart following a table)
                own = _nodes(v.get("series"))
                note = v.get("note") if isinstance(v.get("note"), str) else None
                b = {"series": own} if own or note else None    # a curve-less row: its note only
                if b is not None and note:
                    b["note"] = note
                hide = v.get("hide")                 # names of shared series the row replaces
                if b is not None and isinstance(hide, list) and hide and all(isinstance(h, str) for h in hide):
                    b["hide"] = list(hide)
            if b:
                by[k] = b
        if not by:
            return None
        out["series_by_key"] = by
        x = p.get("x")                               # the x and series every key shares
        if isinstance(x, list) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in x):
            out["x"] = list(x)
            shared = _nodes(p.get("series"))
            if shared:
                out["series"] = shared
        return out
    block = _series_block(p)
    if not block:                                    # no data yet: kept only when its header explains why
        return out if "context" in out else None    # (e.g. a live track before its first sessions)
    out.update(block)
    return out


def _panel(p: dict) -> dict | None:
    if not _pub(p):
        return None
    out = {}
    kind = p.get("type")
    for k in _PANEL_KEYS:
        if k not in p:
            continue
        val = p[k]
        if k in ("id", "title", "type", "key", "drives", "follows", "legend", "yfmt", "xfmt", "horizon", "cursor"):
            if isinstance(val, str):
                out[k] = val
        elif k in ("n", "span", "rows_span", "today_idx"):
            if isinstance(val, int) and not isinstance(val, bool):
                out[k] = val
        elif k in ("ranges", "horizons"):
            if isinstance(val, list) and all(isinstance(v, str) for v in val):
                out[k] = list(val)
        elif k == "rebase":
            if isinstance(val, bool):
                out[k] = val
        elif k in ("vlines", "vlabels"):                  # chart marks: epoch seconds / their labels
            typ = str if k == "vlabels" else (int, float)
            if isinstance(val, list) and all(isinstance(v, typ) and not isinstance(v, bool) for v in val):
                out[k] = list(val)
        elif k == "sort":
            # sort only for tables; defer to _table for validation
            if kind == "table" and isinstance(val, list) and len(val) >= 1:
                out[k] = val
    out["vis"] = PUBLIC
    if _pub(p.get("context")):
        ctx_text = p["context"].get("text")
        if isinstance(ctx_text, str):
            out["context"] = {"text": ctx_text, "vis": PUBLIC}
    if kind in _LISTS:
        items = _nodes(p.get(_LISTS[kind]))
        if not items:
            return None
        out[_LISTS[kind]] = items
        for extra in _EXTRA_LISTS.get(kind, ()):          # e.g. ALLOCATION's maximized detail
            nodes = _nodes(p.get(extra))
            if nodes:
                out[extra] = nodes
        return out
    if kind == "table":
        return _table(p, out)
    if kind == "chart":
        return _chart(p, out)
    if kind == "heatmap":
        labels = p.get("labels")
        cells = p.get("cells")
        if not (isinstance(labels, list) and labels and all(isinstance(l, str) for l in labels)
                and isinstance(cells, list)):
            return None
        out["labels"] = list(labels)
        out["cells"] = [[copy.deepcopy(v) if _is_scalar(v) else None for v in row] if isinstance(row, list) else []
                        for row in cells]
        if isinstance(p.get("fmt"), str):
            out["fmt"] = p["fmt"]
        return out
    return None                                     # unknown panel type: private by default


def public_view(payload: dict) -> dict:
    out = {}
    if isinstance(payload.get("screen"), str):
        out["screen"] = payload["screen"]
    if isinstance(payload.get("title"), str):
        out["title"] = payload["title"]
    if _pub(payload.get("context")):
        ctx_text = payload["context"].get("text")
        if isinstance(ctx_text, str):
            out["context"] = {"text": ctx_text, "vis": PUBLIC}
    meta = payload.get("meta") or {}
    out["meta"] = {}
    if isinstance(meta.get("computed_at"), str):
        out["meta"]["computed_at"] = meta["computed_at"]
    tiers = meta.get("tiers")
    if isinstance(tiers, dict):
        out["meta"]["tiers"] = {k: v for k, v in tiers.items() if isinstance(k, str) and (v is None or isinstance(v, str))}
    out["help"] = _nodes(payload.get("help"))
    # numbers stay as in the local view (a private panel leaves a gap): text such as "ALT+7 DETAIL"
    # names a panel by its number
    out["panels"] = [q for q in (_panel(p) for p in payload.get("panels", [])) if q]
    return out
