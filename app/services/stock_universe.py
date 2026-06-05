from __future__ import annotations

import csv
import io
import json
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
        "FORCEMOT", "NYKAA", "FORTIS", "GAIL", "GVT&D", "GMRAIRPORT", "GLENMARK",
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
        "PREMIERENE", "PRESTIGE", "PNB", "RBLBANK", "RECLTD", "RADICO", "RVNL", "RELIANCE",
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


@dataclass(frozen=True)
class StockFutureContract:
    symbol: str
    label: str
    trading_symbol: str
    security_id: str
    expiry: date | None
    lot_size: int
    exchange_segment: str = "NSE_FNO"
    instrument_type: str = "FUTSTK"


class StockUniverseService:
    master_url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    cache_ttl = timedelta(hours=12)

    def __init__(self) -> None:
        self.allowed_symbols = tuple(dict.fromkeys(symbol.strip().upper() for symbol in STOCK_UNIVERSE if symbol.strip()))
        self._lock = threading.RLock()
        self._resolved_entries: dict[str, StockUniverseEntry] = {}
        self._resolved_futures: dict[str, StockFutureContract] = {}
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

    def resolve_current_future(self, symbol: str, *, reference_date: date | None = None) -> StockFutureContract:
        normalized = (symbol or "").strip().upper()
        if normalized not in DERIVATIVE_SYMBOLS:
            raise ValueError(f"{normalized or 'Stock'} is not in the configured F&O stock list.")
        reference = reference_date or date.today()
        with self._lock:
            cached = self._resolved_futures.get(normalized)
            if cached is not None and (cached.expiry is None or cached.expiry >= reference):
                return cached
            if not self._remote_master_loaded:
                self._load_master_locked()
            cached = self._resolved_futures.get(normalized)
            if cached is None:
                raise ValueError(f"Could not resolve a Dhan stock future contract for {normalized}.")
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
                    try:
                        self._load_master_locked()
                    except RuntimeError:
                        pass
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
        resolved_futures: dict[str, StockFutureContract] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            security_id = str(item.get("security_id") or "").strip()
            if symbol not in self.allowed_symbols:
                continue
            if security_id:
                resolved[symbol] = StockUniverseEntry(
                    symbol=symbol,
                    label=str(item.get("label") or symbol).strip() or symbol,
                    security_id=security_id,
                )
            future_payload = item.get("future")
            if isinstance(future_payload, dict):
                future_security_id = str(future_payload.get("security_id") or "").strip()
                if future_security_id:
                    resolved_futures[symbol] = StockFutureContract(
                        symbol=symbol,
                        label=str(future_payload.get("label") or symbol).strip() or symbol,
                        trading_symbol=str(future_payload.get("trading_symbol") or symbol).strip() or symbol,
                        security_id=future_security_id,
                        expiry=self._parse_expiry_date(future_payload.get("expiry")),
                        lot_size=max(self._coerce_int(future_payload.get("lot_size"), 1), 1),
                    )
        if resolved:
            self._resolved_entries.update(resolved)
        if resolved_futures:
            self._resolved_futures.update(resolved_futures)

    def _write_cache_locked(self, resolved: dict[str, StockUniverseEntry]) -> None:
        payload = {
            "updated_at": datetime.now().isoformat(),
            "entries": [
                {
                    "symbol": entry.symbol,
                    "label": entry.label,
                    "security_id": entry.security_id,
                    "future": (
                        {
                            "label": future.label,
                            "trading_symbol": future.trading_symbol,
                            "security_id": future.security_id,
                            "expiry": future.expiry.isoformat() if future.expiry else None,
                            "lot_size": future.lot_size,
                        }
                        if (future := self._resolved_futures.get(entry.symbol)) is not None
                        else None
                    ),
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
        future_candidates: dict[str, list[StockFutureContract]] = {}
        for row in rows:
            symbol = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
            exchange = str(row.get("SEM_EXM_EXCH_ID", "")).strip().upper()
            segment = str(row.get("SEM_SEGMENT", "")).strip().upper()
            series = str(row.get("SEM_SERIES", "")).strip().upper()
            security_id = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()
            if symbol in self.allowed_symbols and exchange == "NSE" and segment == "E" and series == "EQ" and security_id:
                label = str(row.get("SM_SYMBOL_NAME", "")).strip() or symbol
                resolved[symbol] = StockUniverseEntry(
                    symbol=symbol,
                    label=label,
                    security_id=security_id,
                )
                continue

            future_contract = self._future_contract_from_master_row(row)
            if future_contract is not None:
                future_candidates.setdefault(future_contract.symbol, []).append(future_contract)

        today = date.today()
        for symbol, contracts in future_candidates.items():
            valid = [contract for contract in contracts if contract.expiry is None or contract.expiry >= today]
            chosen_pool = valid or contracts
            chosen = sorted(chosen_pool, key=lambda item: item.expiry or date.max)[0]
            self._resolved_futures[symbol] = chosen
        self._resolved_entries.update(resolved)
        self._remote_master_loaded = True
        self._write_cache_locked(resolved)

    def _future_contract_from_master_row(self, row: dict) -> StockFutureContract | None:
        security_id = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()
        if not security_id:
            return None
        exchange = str(row.get("SEM_EXM_EXCH_ID", "")).strip().upper()
        segment = str(row.get("SEM_SEGMENT", "")).strip().upper()
        instrument_name = str(row.get("SEM_INSTRUMENT_NAME", "") or row.get("SEM_INSTRUMENT_TYPE", "")).strip().upper()
        series = str(row.get("SEM_SERIES", "")).strip().upper()
        if exchange != "NSE":
            return None
        row_text = " ".join(str(value or "").upper() for value in row.values())
        if "FUT" not in row_text:
            return None
        if "OPTSTK" in row_text or "OPTIDX" in row_text or series in {"CE", "PE"}:
            return None
        if instrument_name and "FUT" not in instrument_name:
            return None
        if segment and segment not in {"D", "M", "FNO"} and "FNO" not in row_text:
            return None

        trading_symbol = str(row.get("SEM_TRADING_SYMBOL", "") or row.get("SEM_CUSTOM_SYMBOL", "")).strip().upper()
        symbol = self._future_underlying_symbol(row, trading_symbol)
        if symbol not in DERIVATIVE_SYMBOLS:
            return None
        expiry = self._parse_expiry_date(
            row.get("SEM_EXPIRY_DATE")
            or row.get("SEM_EXPIRY")
            or row.get("EXPIRY_DATE")
            or row.get("EXPIRY")
        )
        lot_size = max(
            self._coerce_int(
                row.get("SEM_LOT_UNITS")
                or row.get("SEM_LOT_SIZE")
                or row.get("LOT_SIZE")
                or row.get("LOT_UNITS")
                or row.get("SEM_BOARD_LOT_QUANTITY"),
                1,
            ),
            1,
        )
        label = str(row.get("SM_SYMBOL_NAME", "") or row.get("SEM_CUSTOM_SYMBOL", "") or trading_symbol or symbol).strip()
        return StockFutureContract(
            symbol=symbol,
            label=label or symbol,
            trading_symbol=trading_symbol or label or symbol,
            security_id=security_id,
            expiry=expiry,
            lot_size=lot_size,
        )

    def _future_underlying_symbol(self, row: dict, trading_symbol: str) -> str:
        for key in ("SEM_UNDERLYING_SYMBOL", "SEM_SYMBOL", "SEM_TRADING_SYMBOL"):
            candidate = str(row.get(key, "")).strip().upper()
            if candidate in DERIVATIVE_SYMBOLS:
                return candidate
        haystack = " ".join(str(value or "").upper() for value in row.values())
        for symbol in sorted(DERIVATIVE_SYMBOLS, key=len, reverse=True):
            if trading_symbol.startswith(symbol) or f" {symbol} " in f" {haystack} ":
                return symbol
        return trading_symbol.split("-")[0].split()[0]

    def _parse_expiry_date(self, raw_value: object) -> date | None:
        value = str(raw_value or "").strip()
        if not value:
            return None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d%b%Y"):
            try:
                return datetime.strptime(value.title(), fmt).date()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None

    def _coerce_int(self, raw_value: object, default: int) -> int:
        try:
            return int(float(str(raw_value).strip()))
        except (TypeError, ValueError):
            return default
