from __future__ import annotations

import csv
import io
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx


STOCK_UNIVERSE = [
    "TVSMOTOR", "MARUTI", "M&M", "TATAMOTORS", "BAJAJ-AUTO",
    "EICHERMOT", "HEROMOTOCO", "TIINDIA", "ASHOKLEY", "BHARATFORG",
    "MRF", "MOTHERSON", "SONACOMS", "BALKRISIND", "BOSCHLTD",
    "TCS", "INFY", "HCLTECH", "WIPRO", "TECHM",
    "LTM", "PERSISTENT", "MPHASIS", "COFORGE", "OFSS",
    "ADANIENT", "APLAPOLLO", "HINDALCO", "HINDCOPPER", "HINDZINC",
    "JINDALSTEL", "JSL", "JSWSTEEL", "LLOYDSME", "NATIONALUM",
    "NMDC", "RATNAMANI", "SAIL", "TATASTEEL", "VEDL",
    "BAJFINANCE", "BAJAJFINSV", "HDFCLIFE", "ICICIPRULI", "SBILIFE",
    "HDFCAMC", "ICICIGI", "CHOLAFIN", "MUTHOOTFIN", "RECLTD",
    "PFC", "LICHSGFIN", "SHRIRAMFIN", "SUNDARMFIN", "CANFINHOME",
    "IIFL", "MANAPPURAM", "MOTILALOFS", "CREDITACC", "POLICYBZR",
    "M&MFIN", "MFSL", "IEX", "SBICARD", "PAYTM",
    "YESBANK", "BANDHANBNK", "MCX", "ANGELONE", "ABCAPITAL",
    "RBLBANK", "CDSL", "LTF", "UNIONBANK", "POONAWALLA",
    "BANKINDIA", "INDIANB", "PEL", "HUDCO", "CAMS",
    "IDFCFIRSTB", "FEDERALBNK", "AUBANK", "BSE", "FORTIS",
    "LUPIN", "AUROPHARMA", "SUNPHARMA", "ALKEM", "CIPLA",
    "BIOCON", "DRREDDY", "TORNTPHARM", "SYNGENE", "ZYDUSLIFE",
    "APOLLOHOSP", "ABBOTINDIA", "IPCALAB", "GLENMARK", "GRANULES",
    "DIVISLAB", "MAXHEALTH", "LAURUSLABS", "MANKIND", "MEDANTA",
    "JBCHEPHARM", "RAINBOW", "GLAXO", "PFIZER", "POLYMED",
    "PPLPHARMA", "ASTERDM", "GLAND", "LALPATHLAB", "APLLTD",
    "NH", "NEULANDLAB", "AJANTPHARM", "KIMS", "NATCOPHARM",
    "BANKBARODA", "MAHABANK", "CANBK", "PSB", "CENTRALBK",
    "IOB", "PNB", "SBIN", "UCOBANK", "CROMPTON",
    "VGUARD", "TITAN", "KALYANKJIL", "BATAINDIA", "DIXON",
    "HAVELLS", "KAJARIACER", "CERA", "BLUESTARCO", "PGEL",
    "VOLTAS", "WHIRLPOOL", "AMBER", "CENTURYPLY", "UNITDSPR",
    "HINDUNILVR", "DABUR", "GODREJCP", "ITC", "NESTLEIND",
    "MARICO", "TATACONSUM", "PATANJALI", "BRITANNIA", "UBL",
    "VBL", "RADICO", "EMAMILTD", "COLPAL", "HDFCBANK",
    "KOTAKBANK", "AXISBANK", "ICICIBANK", "INDUSINDBK", "RELIANCE",
    "NTPC", "ONGC", "POWERGRID", "COALINDIA", "ADANIPOWER",
    "IOC", "ADANIGREEN", "BPCL", "TATAPOWER", "GAIL",
    "ABB", "ADANIENSOL", "SIEMENS", "PETRONET", "CESC",
    "IGL", "JSWENERGY", "OIL", "MGL", "POWERINDIA",
    "GVT&D", "AEGISLOG", "THERMAX", "CGPOWER", "BHEL",
    "NLCINDIA", "JPPOWER", "SUZLON", "SJVN", "GSPL",
    "CASTROLIND", "HINDPETRO", "INOXWIND", "GUJGASLTD", "NHPC",
    "BEL", "NBCC", "COCHINSHIP", "SONATSOFTW", "BHARTIHEXA",
    "LTTS", "INDUSTOWER", "TATACOMM", "INTELLECT", "TATAELXSI",
    "TATATECH", "CYIENT", "IDEA", "KPITTECH", "BSOFT",
    "AFFLE", "HFCL", "TEJASNET", "ZENSARTECH", "HAL",
    "MAZDOCK", "BEML", "BDL", "SOLARINDS", "GRSE",
    "ASTRAMICRO", "UNIMECH", "DYNAMATECH", "DATAPATTNS", "MTARTECH",
    "CYIENTDLM", "ZENTEC", "MIDHANI", "DCXINDIA", "ZEEL",
    "NAZARA", "DBCORP", "TIPSMUSIC", "PVRINOX", "NETWORK18",
    "HATHWAY", "SUNTV", "SAREGAMA", "BHARTIARTL", "INDIAMART",
    "IRCTC", "NAUKRI", "NYKAA", "SWIGGY", "ETERNAL",
    "JUBLFOOD", "DEVYANI", "INDHOTEL", "INDIGO", "DBREALTY",
    "SAPPHIRE", "TBOTEK", "EIHOTEL", "LEMONTREE", "GMRAIRPORT",
    "CHALET", "BLS", "WESTLIFE", "NAM-INDIA", "360ONE",
    "ANANDRATHI", "NUVAMA", "ABSLAMC", "UTIAMC", "KFINTECH",
    "ATGL", "UPL", "COROMANDEL", "DEEPAKNTR", "EXIDEIND",
    "CUMMINSIND", "POLYCAB", "TATACHEM", "ESCORTS", "SRF",
    "PIIND", "KPRMILL", "KEI", "ASTRAL", "HSCL",
    "AIAENG", "PIDILITIND", "PAGEIND", "HONAUT", "ABREL",
    "KAYNES", "SUPREMEIND", "FLUOROCHEM","GESHIP","TMPV","TRITURBINE","SOLARA","CHAMBLFERT","KEC","GALLANTT","MAPMYINDIA","GESHIP",
    "PERSISTENT","TORNTPOWER","SCI" ,"SAILIFE","PGEL","KIMS","NCC","KAYNES"

]


@dataclass(frozen=True)
class StockUniverseEntry:
    symbol: str
    label: str
    security_id: str
    exchange_segment: str = "NSE_EQ"
    instrument_type: str = "EQUITY"


class StockUniverseService:
    master_url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    cache_ttl = timedelta(hours=12)

    def __init__(self) -> None:
        self.allowed_symbols = tuple(dict.fromkeys(symbol.strip().upper() for symbol in STOCK_UNIVERSE if symbol.strip()))
        self._lock = threading.RLock()
        self._resolved_entries: dict[str, StockUniverseEntry] = {}
        self._remote_master_loaded = False
        self._cache_path = Path(__file__).resolve().parents[1] / "data" / "stock_universe_cache.json"
        self._warmup_started = False
        self._load_cache_locked()

    def search(self, query: str, limit: int = 20) -> list[StockUniverseEntry]:
        self._start_warmup()
        normalized = (query or "").strip().upper()
        candidates = list(self.allowed_symbols)
        if normalized:
            candidates = [symbol for symbol in candidates if normalized in symbol]
        candidates = candidates[: max(limit, 1)]
        return [self._preview_entry(symbol) for symbol in candidates]

    def preview(self, symbol: str) -> StockUniverseEntry:
        normalized = (symbol or "").strip().upper()
        if normalized not in self.allowed_symbols:
            raise ValueError(f"{symbol or 'Stock'} is not in the configured stock universe.")
        return self._preview_entry(normalized)

    def resolve(self, symbol: str) -> StockUniverseEntry:
        normalized = (symbol or "").strip().upper()
        if normalized not in self.allowed_symbols:
            raise ValueError(f"{symbol or 'Stock'} is not in the configured stock universe.")
        with self._lock:
            cached = self._resolved_entries.get(normalized)
            if cached is not None:
                return cached
            if not self._remote_master_loaded:
                self._load_master_locked()
            cached = self._resolved_entries.get(normalized)
            if cached is None:
                raise ValueError(f"Could not resolve a Dhan NSE cash security id for {normalized}.")
            return cached

    def _preview_entry(self, symbol: str) -> StockUniverseEntry:
        with self._lock:
            cached = self._resolved_entries.get(symbol)
            if cached is not None:
                return cached
        return StockUniverseEntry(symbol=symbol, label=symbol, security_id="")

    def _start_warmup(self) -> None:
        with self._lock:
            if self._warmup_started or self._remote_master_loaded:
                return
            self._warmup_started = True
        worker = threading.Thread(target=self._warmup_master, name="stock-universe-warmup", daemon=True)
        worker.start()

    def _warmup_master(self) -> None:
        try:
            with self._lock:
                if not self._remote_master_loaded:
                    self._load_master_locked()
        finally:
            with self._lock:
                self._warmup_started = False

    def _load_cache_locked(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except Exception:
            return
        updated_at_raw = str(payload.get("updated_at") or "").strip()
        if not updated_at_raw:
            return
        try:
            updated_at = datetime.fromisoformat(updated_at_raw)
        except ValueError:
            return
        if datetime.now() - updated_at > self.cache_ttl:
            return
        entries = payload.get("entries")
        if not isinstance(entries, list):
            return
        resolved: dict[str, StockUniverseEntry] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            security_id = str(item.get("security_id") or "").strip()
            if symbol not in self.allowed_symbols or not security_id:
                continue
            resolved[symbol] = StockUniverseEntry(
                symbol=symbol,
                label=str(item.get("label") or symbol).strip() or symbol,
                security_id=security_id,
            )
        if resolved:
            self._resolved_entries.update(resolved)

    def _write_cache_locked(self, resolved: dict[str, StockUniverseEntry]) -> None:
        payload = {
            "updated_at": datetime.now().isoformat(),
            "entries": [
                {
                    "symbol": entry.symbol,
                    "label": entry.label,
                    "security_id": entry.security_id,
                }
                for entry in resolved.values()
            ],
        }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_master_locked(self) -> None:
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.get(self.master_url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Unable to download Dhan scrip master CSV: {exc}") from exc

        rows = csv.DictReader(io.StringIO(response.text))
        resolved: dict[str, StockUniverseEntry] = {}
        for row in rows:
            symbol = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
            if symbol not in self.allowed_symbols:
                continue
            exchange = str(row.get("SEM_EXM_EXCH_ID", "")).strip().upper()
            segment = str(row.get("SEM_SEGMENT", "")).strip().upper()
            series = str(row.get("SEM_SERIES", "")).strip().upper()
            security_id = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()
            if exchange != "NSE" or segment != "E" or series != "EQ" or not security_id:
                continue
            label = str(row.get("SM_SYMBOL_NAME", "")).strip() or symbol
            resolved[symbol] = StockUniverseEntry(
                symbol=symbol,
                label=label,
                security_id=security_id,
            )
        self._resolved_entries.update(resolved)
        self._remote_master_loaded = True
        self._write_cache_locked(resolved)
