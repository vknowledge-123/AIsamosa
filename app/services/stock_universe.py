from __future__ import annotations

import csv
import io
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx


STOCK_UNIVERSE =[
    "ICICIBANK", "BSE", "TCS", "INFY", "HDFCBANK", "GESHIP", "IDEA", "ADANIGREEN",
    "ADANIENT", "RELIANCE", "COFORGE", "BHARTIARTL", "TECHM", "JAINREC", "SBIN",
    "MCX", "HSCL", "ADANIPOWER", "VEDL", "AMBER", "TEJASNET", "ETERNAL", "HCLTECH",
    "TATASTEEL", "DIXON", "AXISBANK", "OLAELEC", "COHANCE", "SOLARINDS", "M&M",
    "SAIL", "GROWW", "FIRSTCRY", "LT", "HFCL", "HAL", "OFSS", "GLAND", "PERSISTENT",
    "POLICYBZR", "KAYNES", "GVT&D", "TMPV", "ITC", "ADANIPORTS", "ADANIENSOL",
    "ANGELONE", "WIPRO", "BEL", "KOTAKBANK", "NLCINDIA", "TVSMOTOR", "BAJFINANCE",
    "ASTRAL", "GRSE", "SCI", "BHEL", "IOC", "COALINDIA", "POWERGRID", "DATAPATTNS",
    "SUNPHARMA", "MPHASIS", "POWERINDIA", "NATIONALUM", "HYUNDAI", "ASHOKLEY",
    "GAIL", "UNOMINDA", "HINDALCO", "GODFRYPHLP", "MARUTI", "LATENTVIEW", "TMCV",
    "HINDCOPPER", "ATGL", "TATATECH", "LENSKART", "JSWENERGY", "SUZLON", "FSL",
    "NETWEB", "SWIGGY", "SHRIRAMFIN", "ACUTAAS", "ATHERENERG", "CIPLA",
    "HEROMOTOCO", "WAAREEENER", "AFCONS", "RADICO", "NTPC", "MOTHERSON",
    "INDUSTOWER", "WOCKPHARMA", "ECLERX", "LODHA", "HINDZINC", "JIOFIN", "ABB",
    "AUROPHARMA", "NESTLEIND", "MUTHOOTFIN", "LTM", "DLF", "VBL", "COCHINSHIP",
    "CANBK", "TATAPOWER", "CDSL", "VOLTAS", "ONGC", "LAURUSLABS", "PAYTM",
    "INDIGO", "BPCL", "DEEPAKFERT", "TRENT", "JPPOWER", "DRREDDY", "SAREGAMA",
    "FORCEMOT", "TATACONSUM", "INDHOTEL", "EICHERMOT", "KEI", "MAZDOCK",
    "POLYCAB", "KEC", "HINDPETRO", "LUPIN", "PFC", "BIOCON", "GLENMARK",
    "BANKBARODA", "DELHIVERY", "TITAN", "PNB", "PGEL", "CGPOWER", "KPITTECH",
    "ENGINERSIN", "PATANJALI", "BAJAJ-AUTO", "TATAELXSI", "BANKINDIA",
    "APOLLOHOSP", "ICICIPRULI", "HINDUNILVR", "MAXHEALTH", "RVNL", "CGCL",
    "MANKIND", "HEXT", "KALYANKJIL", "NUVAMA", "IFCI", "PAGEIND", "SBILIFE",
    "DIVISLAB", "SAILIFE", "FEDERALBNK", "YESBANK", "CUMMINSIND", "ENRIN",
    "ASIANPAINT", "TRITURBINE", "INDIANB", "GRAPHITE", "PREMIERENE", "ZYDUSLIFE",
    "GMDCLTD", "NAM-INDIA", "GODREJCP", "SAMMAANCAP", "JSWSTEEL", "ULTRACEMCO",
    "LALPATHLAB", "ABCAPITAL", "NHPC", "BSOFT", "GRASIM", "CHOLAFIN", "BDL",
    "MMTC", "DMART", "CARTRADE", "DEEPAKNTR", "INDUSINDBK", "NMDC", "BRITANNIA",
    "KFINTECH", "PINELABS", "APARINDS", "NEULANDLAB", "ZEEL", "GODREJPROP",
    "BLS", "HDFCAMC", "TORNTPOWER", "NAUKRI", "MFSL", "BANDHANBNK", "RECLTD",
    "HDFCLIFE", "TORNTPHARM", "INOXWIND", "NAVINFLUOR", "AUBANK", "MEESHO",
    "HBLENGINE", "ANANTRAJ", "HEG", "RPOWER", "IRFC", "INTELLECT", "BAJAJFINSV",
    "CROMPTON", "COROMANDEL", "SAGILITY", "RBLBANK", "UNIONBANK", "BHARATFORG",
    "IDBI", "OIL", "NBCC", "IDFCFIRSTB", "WELCORP", "APOLLOTYRE", "LTF", "UPL",
    "JYOTICNC", "BOSCHLTD", "IREDA", "MRF", "VMM", "LTTS", "SIEMENS", "MRPL",
    "CHENNPETRO", "COLPAL", "OLECTRA", "CAMS", "PIRAMALFIN", "FORTIS",
    "GMRAIRPORT", "GRANULES", "PIIND", "ALKEM", "MAHABANK", "TIMKEN", "MARICO",
    "JUBLFOOD", "KIRLOSENG", "SYRMA", "SRF", "JINDALSTEL", "GPIL", "NATCOPHARM",
    "PIDILITIND", "LGEINDIA", "HUDCO", "NH", "PPLPHARMA", "JINDALSAW",
    "PRESTIGE", "REDINGTON", "AMBUJACEM", "PWL", "ANANDRATHI", "LICI", "TARIL",
    "PHOENIXLTD", "DALBHARAT", "MEDANTA", "MANAPPURAM", "PARADEEP", "ZENTEC",
    "JBMA", "SHYAMMETL", "KPIL", "SYNGENE", "BLUESTARCO", "HDBFS", "UNITDSPR",
    "EMMVEE", "EXIDEIND", "RRKABEL", "THERMAX", "BALRAMCHIN", "IGL",
    "CHAMBLFERT", "NYKAA", "TATACHEM", "ICICIAMC", "MOTILALOFS", "APLAPOLLO",
    "OBEROIRLTY", "HAVELLS", "ESCORTS", "LLOYDSME", "URBANCO", "CHOICEIN",
    "IIFL", "SONACOMS", "TATACAP", "LICHSGFIN", "CUB", "TATACOMM", "DABUR",
    "BEML", "SONATSOFTW", "IGIL", "JBCHEPHARM", "IRCTC", "AEGISLOG", "NAVA",
    "SUMICHEM", "JKTYRE", "ITCHOTELS", "TIINDIA", "NTPCGREEN", "BELRISE",
    "AFFLE", "NEWGEN", "SUPREMEIND", "PNBHOUSING", "TITAGARH", "CAPLIPOINT",
    "CESC", "KIMS", "ARE&M", "PETRONET", "SJVN", "POONAWALLA", "BHARTIHEXA",
    "CEATLTD", "FINCABLES", "IPCALAB", "JWL", "BALKRISIND", "KARURVYSYA",
    "AJANTPHARM", "ZENSARTECH", "IRCON", "ICICIGI", "CPPLUS", "CEMPRO", "AWL",
    "IEX", "EIDPARRY", "ZYDUSWELL", "CYIENT", "J&KBANK", "NCC", "ABDL",
    "SCHNEIDER", "ANTHEM", "GODREJIND", "TECHNOE", "CRAFTSMAN", "ANURAS",
    "EMCURE", "JSWINFRA", "PCBL", "GRAVITA", "SIGNATURE", "CONCOR", "LEMONTREE",
    "ONESOURCE", "BAJAJHLDNG", "NIVABUPA", "MGL", "CHOLAHLDNG", "TATAINVEST",
    "SBICARD", "DEVYANI", "ASTERDM", "CARBORUNIV", "LINDEINDIA", "STARHEALTH",
    "ABSLAMC", "DOMS", "MAPMYINDIA", "CRISIL", "RAILTEL", "GLAXO", "JMFINANCIL",
    "360ONE", "ZFCVINDIA", "JSL", "M&MFIN", "IKS", "FLUOROCHEM", "3MINDIA",
    "SARDAEN", "SHREECEM", "ACMESOLAR", "BRIGADE", "AARTIIND", "ABREL",
    "CIEINDIA", "TEGA", "WELSPUNLIV", "BAJAJHFL", "ATUL", "NSLNISP", "FACT",
    "KPRMILL", "SWANCORP", "ACE", "SUNDARMFIN", "TENNIND", "BATAINDIA",
    "FIVESTAR", "HONASA", "GALLANTT", "MSUMI", "VIJAYA", "GILLETTE",
    "ENDURANCE", "USHAMART", "IRB", "SAPPHIRE", "CREDITACC", "AIAENG", "CLEAN",
    "ELGIEQUIP", "BLUEJET", "CENTRALBK", "WHIRLPOOL", "GABRIEL", "BERGEPAINT",
    "JUBLPHARMA", "UBL", "ELECON", "TRIDENT", "ITI", "RAINBOW", "ABBOTINDIA",
    "APTUS", "CCL", "TTML", "ACC", "UCOBANK", "INDIAMART", "PVRINOX", "VTL",
    "SCHAEFFLER", "HOMEFIRST", "RITES", "SBFC", "JUBLINGREA", "UTIAMC", "AIIL",
    "ABFRL", "RKFORGE", "GODIGIT", "SOBHA", "PFIZER", "AADHARHFC", "SUNTV",
    "KAJARIACER", "CHALET", "JSWCEMENT", "JKCEMENT", "CASTROLIND", "TBOTEK",
    "LTFOODS", "HONAUT", "NIACL", "RAMCOCEM", "AEGISVOPAK", "PTCIL", "EMAMILTD",
    "CANFINHOME", "BAYERCROP", "GICRE", "CONCORDBIO", "POLYMED", "ERIS",
    "EIHOTEL", "THELEELA", "BLUEDART", "IOB", "MINDACORP", "NUVOCO", "BBTC",
    "INDIACEM", "INDGN", "ASAHIINDIA", "CANHLIFE", "BIKAJI", "ABLBL", "RHIM",
    "TRAVELFOOD", "DCMSHRIRAM", "JSWDULUX", "AAVAS", "SPLPETRO"
]

DERIVATIVE_SYMBOLS = frozenset(
    {
        "360ONE", "ABB", "APLAPOLLO", "AUBANK", "ADANIENSOL", "ADANIENT", "ADANIGREEN",
        "ADANIPORTS", "ADANIPOWER", "ABCAPITAL", "ALKEM", "AMBER", "AMBUJACEM",
        "ANGELONE", "APOLLOHOSP", "ASHOKLEY", "ASIANPAINT", "ASTRAL", "AUROPHARMA",
        "DMART", "AXISBANK", "BSE", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV",
        "BAJAJHLDNG", "BANDHANBNK", "BANKBARODA", "BANKINDIA", "BDL", "BEL",
        "BHARATFORG", "BHEL", "BPCL", "BHARTIARTL", "BIOCON", "BLUESTARCO",
        "BOSCHLTD", "BRITANNIA", "CGPOWER", "CANBK", "CDSL", "CHOLAFIN", "CIPLA",
        "COALINDIA", "COCHINSHIP", "COFORGE", "COLPAL", "CAMS", "CONCOR",
        "CROMPTON", "CUMMINSIND", "DLF", "DABUR", "DALBHARAT", "DELHIVERY",
        "DIVISLAB", "DIXON", "DRREDDY", "ETERNAL", "EICHERMOT", "EXIDEIND",
        "FORCEMOT", "NYKAA", "FORTIS", "GAIL", "GMRAIRPORT", "GLENMARK",
        "GODFRYPHLP", "GODREJCP", "GODREJPROP", "GRASIM", "HCLTECH", "HDFCAMC",
        "HDFCBANK", "HDFCLIFE", "HAVELLS", "HEROMOTOCO", "HINDALCO", "HAL",
        "HINDPETRO", "HINDUNILVR", "HINDZINC", "POWERINDIA", "HYUNDAI",
        "ICICIBANK", "ICICIGI", "ICICIPRULI", "IDFCFIRSTB", "ITC", "INDIANB",
        "IEX", "IOC", "IRFC", "IREDA", "INDUSTOWER", "INDUSINDBK", "NAUKRI",
        "INFY", "INOXWIND", "INDIGO", "JINDALSTEL", "JSWENERGY", "JSWSTEEL",
        "JIOFIN", "JUBLFOOD", "KEI", "KPITTECH", "KALYANKJIL", "KAYNES",
        "KFINTECH", "KOTAKBANK", "LTF", "LICHSGFIN", "LTM", "LT", "LAURUSLABS",
        "LICI", "LODHA", "LUPIN", "M&M", "MANAPPURAM", "MANKIND", "MARICO",
        "MARUTI", "MFSL", "MAXHEALTH", "MAZDOCK", "MOTILALOFS", "MPHASIS", "MCX",
        "MUTHOOTFIN", "NBCC", "NHPC", "NMDC", "NTPC", "NATIONALUM", "NESTLEIND",
        "NAM-INDIA", "NUVAMA", "OBEROIRLTY", "ONGC", "OIL", "PAYTM", "OFSS",
        "POLICYBZR", "PGEL", "PIIND", "PNBHOUSING", "PAGEIND", "PATANJALI",
        "PERSISTENT", "PETRONET", "PIDILITIND", "POLYCAB", "PFC", "POWERGRID",
        "PREMIERENE", "PRESTIGE", "PNB", "RBLBANK", "RECLTD", "RVNL", "RELIANCE",
        "SBICARD", "SBILIFE", "SHREECEM", "SRF", "SAMMAANCAP", "MOTHERSON",
        "SHRIRAMFIN", "SIEMENS", "SOLARINDS", "SONACOMS", "SBIN", "SAIL",
        "SUNPHARMA", "SUPREMEIND", "SUZLON", "SWIGGY", "TATACONSUM", "TVSMOTOR",
        "TCS", "TATAELXSI", "TMPV", "TATAPOWER", "TATASTEEL", "TECHM",
        "FEDERALBNK", "INDHOTEL", "PHOENIXLTD", "TITAN", "TORNTPHARM", "TRENT",
        "TIINDIA", "UNOMINDA", "UPL", "ULTRACEMCO", "UNIONBANK", "UNITDSPR",
        "VBL", "VEDL", "VMM", "IDEA", "VOLTAS", "WAAREEENER", "WIPRO", "YESBANK",
        "ZYDUSLIFE",
    }
)


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

    def is_derivative_symbol(self, symbol: str) -> bool:
        normalized = (symbol or "").strip().upper()
        return normalized in DERIVATIVE_SYMBOLS

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
