"""Parse CSV or Excel uploads into normalized row dicts for bulk import."""
import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Optional Excel support
try:
    import openpyxl
    HAS_EXCEL = True
except ImportError:
    HAS_EXCEL = False


# Column name variants (first match wins)
HAULIER_COLS = {
    "name": ["name", "company", "company_name", "haulier"],
    "contact_email": ["contact_email", "email", "e-mail"],
    "contact_phone": ["contact_phone", "phone", "tel", "telephone"],
}
VEHICLE_COLS = {
    "haulier_id": ["haulier_id", "haulier id", "haulier"],
    "registration": ["registration", "reg", "reg_no", "vrm"],
    "vehicle_type": ["vehicle_type", "vehicle type", "type"],
    "trailer_type": ["trailer_type", "trailer type", "body_type", "body type"],
    "capacity_weight_kg": ["capacity_weight_kg", "capacity_weight", "weight_kg", "weight"],
    "capacity_volume_m3": ["capacity_volume_m3", "capacity_volume", "volume_m3", "volume"],
}
LOAD_COLS = {
    "shipper_name": ["shipper_name", "shipper", "consignor"],
    "pickup_postcode": ["pickup_postcode", "pickup", "origin", "from_postcode"],
    "delivery_postcode": ["delivery_postcode", "delivery", "destination", "to_postcode"],
    "pickup_window_start": ["pickup_window_start", "pickup_start", "pickup_from"],
    "pickup_window_end": ["pickup_window_end", "pickup_end", "pickup_to"],
    "delivery_window_start": ["delivery_window_start", "delivery_start"],
    "delivery_window_end": ["delivery_window_end", "delivery_end"],
    "weight_kg": ["weight_kg", "weight"],
    "volume_m3": ["volume_m3", "volume"],
    "budget_gbp": ["budget_gbp", "budget", "price"],
    "required_vehicle_type": ["required_vehicle_type", "vehicle_type", "vehicle type"],
    "required_trailer_type": ["required_trailer_type", "trailer_type", "trailer type", "trailer"],
}


def _normalize_header(h: str) -> str:
    return (h or "").strip().lower().replace(" ", "_").replace("-", "_")


def _map_row(headers: List[str], row: List[Any], col_map: Dict[str, List[str]]) -> Dict[str, Any]:
    """Map a row to normalized keys using col_map (target_key -> list of possible header names)."""
    header_to_idx = {_normalize_header(h): i for i, h in enumerate(headers) if h is not None}
    out: Dict[str, Any] = {}
    for target_key, variants in col_map.items():
        for v in variants:
            n = _normalize_header(v)
            if n in header_to_idx:
                idx = header_to_idx[n]
                if idx < len(row):
                    val = row[idx]
                    if isinstance(val, str):
                        val = val.strip()
                    if val != "" and val is not None:
                        out[target_key] = val
                break
    return out


def _parse_datetime(s: Optional[str]) -> Optional[datetime]:
    if not s or (isinstance(s, str) and not s.strip()):
        return None
    if isinstance(s, datetime):
        return s.replace(tzinfo=timezone.utc) if s.tzinfo is None else s
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%Y %H:%M"):
        try:
            dt = datetime.strptime(s[:19], fmt) if len(s) >= 10 else datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _rows_from_csv(content: bytes) -> List[Dict[str, Any]]:
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    headers = rows[0]
    return [dict(zip(headers, row)) for row in rows[1:] if any(c for c in row)]


def _rows_from_excel(content: bytes) -> List[Dict[str, Any]]:
    if not HAS_EXCEL:
        raise ValueError("Excel support requires openpyxl. Install with: pip install openpyxl")
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    if not ws:
        return []
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h) if h is not None else "" for h in rows[0]]
    return [dict(zip(headers, row)) for row in rows[1:] if any(c is not None and str(c).strip() for c in row)]


def parse_upload(content: bytes, filename: str) -> List[Dict[str, Any]]:
    """Parse CSV or Excel file into list of row dicts (raw headers as keys)."""
    fn = (filename or "").lower()
    if fn.endswith(".csv"):
        return _rows_from_csv(content)
    if fn.endswith(".xlsx") or fn.endswith(".xls"):
        return _rows_from_excel(content)
    raise ValueError("Unsupported file type. Use .csv or .xlsx")


def parse_hauliers(content: bytes, filename: str) -> List[Dict[str, Any]]:
    """Parse file into list of normalized haulier row dicts."""
    raw = parse_upload(content, filename)
    if not raw:
        return []
    headers = list(raw[0].keys()) if raw else []
    return [
        _map_row(headers, [r.get(h) for h in headers], HAULIER_COLS)
        for r in raw
    ]


def parse_vehicles(content: bytes, filename: str) -> List[Dict[str, Any]]:
    """Parse file into list of normalized vehicle row dicts."""
    raw = parse_upload(content, filename)
    if not raw:
        return []
    headers = list(raw[0].keys()) if raw else []
    return [
        _map_row(headers, [r.get(h) for h in headers], VEHICLE_COLS)
        for r in raw
    ]


def parse_loads(content: bytes, filename: str) -> List[Dict[str, Any]]:
    """Parse file into list of normalized load row dicts (dates as strings; caller converts)."""
    raw = parse_upload(content, filename)
    if not raw:
        return []
    headers = list(raw[0].keys()) if raw else []
    return [
        _map_row(headers, [r.get(h) for h in headers], LOAD_COLS)
        for r in raw
    ]


def parse_datetime_optional(s: Optional[str]) -> Optional[datetime]:
    """Parse optional datetime string for load windows."""
    return _parse_datetime(s)
