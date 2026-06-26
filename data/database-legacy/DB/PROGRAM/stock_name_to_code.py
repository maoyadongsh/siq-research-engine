"""
A-share stock name -> code mapping.

Populated from Sina Finance API (no API key needed).
Usage:
    python -m DB.PROGRAM.stock_name_to_code --fetch   # fetch latest from Sina
    python -m DB.PROGRAM.stock_name_to_code          # print mapping dict

The mapping supports lookup by:
    - Exact company name: "信达证券" -> "601059"
    - Full company name: "信达证券股份有限公司" -> "601059"
    - Stock code (forward): "601059" -> "信达证券"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Built-in fallback: commonly-used companies (manually curated / historically used)
# ---------------------------------------------------------------------------
_FALLBACK: dict[str, str] = {
    # 券商/金融
    "信达证券": "601059",
    "信达证券股份有限公司": "601059",
    "华安证券": "600909",
    "华安证券股份有限公司": "600909",
    "华林证券": "002945",
    "华林证券股份有限公司": "002945",
    "中信建投": "601066",
    "中信建投证券": "601066",
    "方正证券": "601901",
    "方正证券股份有限公司": "601901",
    "中国中车": "601766",
    "中国中车股份有限公司": "601766",
    "新华文轩": "601811",
    "上海医药": "601607",
    "上海医药集团股份有限公司": "601607",
    "龙江交通": "601188",
    "国投资本": "600061",
    "国投资本股份有限公司": "600061",
    "中国铁建": "601186",
    "中国铁建股份有限公司": "601186",
    "中国能建": "601868",
    "中国能源建设股份有限公司": "601868",
    "中国中车": "601766",
    # 制造/科技
    "工业富联": "601138",
    "富士康工业互联网股份有限公司": "601138",
    "工业富联工业互联网股份有限公司": "601138",
    "比亚迪": "002594",
    "比亚迪股份有限公司": "002594",
    "东鹏饮料": "605499",
    "东鹏饮料（集团）股份有限公司": "605499",
    "法拉电子": "600580",
    "福建法拉电子股份有限公司": "600580",
    "拓普集团": "601689",
    "拓普集团股份有限公司": "601689",
    "宁德时代": "300750",
    "宁德时代新能源科技股份有限公司": "300750",
    "澜起科技": "688008",
    "澜起科技股份有限公司": "688008",
    "中兴通讯": "000063",
    "中兴通讯股份有限公司": "000063",
    "美的集团": "000333",
    "美的集团股份有限公司": "000333",
    "亿纬锂能": "300014",
    "深圳亿纬锂能股份有限公司": "300014",
    "藏格矿业": "000762",
    "西藏藏格矿业股份有限公司": "000762",
    "新宙邦": "300037",
    "深圳新宙邦科技股份有限公司": "300037",
    "光库科技": "300620",
    "广州光库科技股份有限公司": "300620",
    "新国都": "300130",
    "深圳市新国都技术股份有限公司": "300130",
    "硅宝科技": "300019",
    "成都硅宝科技股份有限公司": "300019",
    "大族数控": "301200",
    "大族数码技术股份有限公司": "301200",
    "建科院": "300675",
    "深圳市建科集团股份有限公司": "300675",
    "盟科药业": "688373",
    "盟科药业股份有限公司": "688373",
    "赛诺医疗": "688108",
    "赛诺医疗科技股份公司": "688108",
    "翠微股份": "603123",
    "北京翠微大厦股份有限公司": "603123",
    "舍得酒业": "600702",
    "舍得酒业股份有限公司": "600702",
    "武商集团": "000501",
    "武汉武商集团股份有限公司": "000501",
    "中国重汽": "000951",
    "中国重型汽车集团有限公司": "000951",
    "甘肃能源": "000791",
    "黄河上游水电能源股份有限公司": "000791",
    "亚太股份": "002284",
    "浙江亚太机电股份有限公司": "002284",
    "东方钽业": "000962",
    "东方钽业股份有限公司": "000962",
    "广州酒家": "603043",
    "广州酒家集团股份有限公司": "603043",
    "宁沪高速": "600377",
    "江苏宁沪高速公路股份有限公司": "600377",
    "扬农化工": "600486",
    "江苏扬农化工股份有限公司": "600486",
    "浦发银行": "600000",
    "上海浦东发展银行股份有限公司": "600000",
    "中国银行": "601988",
    "中国银行股份有限公司": "601988",
    "中国农业银行": "601288",
    "中国农业银行股份有限公司": "601288",
    "中国建设银行": "601939",
    "中国建设银行股份有限公司": "601939",
    "交通银行": "601328",
    "交通银行股份有限公司": "601328",
    "招商银行": "600036",
    "招商银行股份有限公司": "600036",
    "万科A": "000002",
    "万科企业股份有限公司": "000002",
    "同洲电子": "002052",
    "深圳同洲电子股份有限公司": "002052",
    "东港股份": "002117",
    "东港股份有限公司": "002117",
    "云南铜业": "000878",
    "云南铜业股份有限公司": "000878",
    # 其他
    "汇通能源": "600605",
    "上海汇通能源股份有限公司": "600605",
    "赤峰黄金": "600988",
    "赤峰吉隆黄金矿业股份公司": "600988",
    "东鹏饮料": "605499",
    "_ST花王": "603003",
    "ST花王": "603003",
    "花王股份": "603003",
    "广东粤海控股集团有限公司": "603003",
}


def _build_mappings() -> tuple[dict[str, str], dict[str, str]]:
    """Build name->code and code->name dicts."""
    name_to_code: dict[str, str] = {}
    code_to_name: dict[str, str] = {}
    for name, code in _FALLBACK.items():
        name_to_code[name.lower()] = code
        name_to_code[name] = code
        code_to_name.setdefault(code, name)
    return name_to_code, code_to_name


NAME_TO_CODE, CODE_TO_NAME = _build_mappings()


def infer_exchange_from_code(code: str | None) -> str | None:
    """Infer A-share exchange from stock code prefix."""
    if not code:
        return None
    if code.startswith("6"):
        return "SSE"
    if code.startswith(("0", "3")):
        return "SZSE"
    if code.startswith("8"):
        return "BSE"
    return None


def resolve_stock_code(name: str | None) -> tuple[str | None, str | None]:
    """
    Look up stock code by company name.

    Returns (stock_code, exchange).
    Exchange is inferred from the stock code prefix.
    """
    if not name:
        return None, None
    # Try exact match first (case-insensitive)
    key = name.lower().strip()
    code = NAME_TO_CODE.get(key)
    if not code:
        # Try partial match
        for mapped_name, mapped_code in NAME_TO_CODE.items():
            if key in mapped_name or mapped_name in key:
                code = mapped_code
                break
    if not code:
        # Try extracting 6-digit code from name (some names embed it)
        m = re.search(r"(?<!\d)([036]\d{5})(?!\d)", name)
        if m:
            code = m.group(1)
    return code, infer_exchange_from_code(code)


def fetch_from_sina() -> dict[str, str]:
    """
    Fetch the full A-share stock list from Sina Finance HTTP API.
    Returns {stock_name: stock_code} mapping.
    """
    import math
    import urllib.parse
    import urllib.request

    count_url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount?node=hs_a"
    try:
        req = urllib.request.Request(count_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw_count = resp.read().decode("utf-8", errors="replace").strip().strip('"')
        total = int(raw_count)
    except Exception as e:
        print(f"[WARN] Sina count fetch failed: {e}", file=sys.stderr)
        return {}

    page_size = 100
    pages = max(1, math.ceil(total / page_size))
    result: dict[str, str] = {}
    base_url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    for page in range(1, pages + 1):
        params = {
            "page": str(page),
            "num": str(page_size),
            "sort": "symbol",
            "asc": "1",
            "node": "hs_a",
            "symbol": "",
            "_s_r_a": "page",
        }
        url = base_url + "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                rows = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            print(f"[WARN] Sina page {page} fetch failed: {e}", file=sys.stderr)
            continue

        for row in rows or []:
            code = str(row.get("code") or "").strip()
            name = str(row.get("name") or "").strip()
            if code and name and re.match(r"^[03689]\d{5}$", code):
                result[name] = code
                short = name.replace("股份有限公司", "").replace("有限公司", "").strip()
                if short:
                    result.setdefault(short, code)
    return result


def fetch_from_eastmoney() -> dict[str, str]:
    """Fetch full A-share stock list from Eastmoney quote API."""
    import urllib.parse
    import urllib.request

    fields = "f12,f14"
    params = {
        "pn": "1",
        "pz": "10000",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23",
        "fields": fields,
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"[WARN] Eastmoney fetch failed: {e}", file=sys.stderr)
        return {}

    rows = ((payload.get("data") or {}).get("diff") or [])
    result: dict[str, str] = {}
    for row in rows:
        code = str(row.get("f12") or "").strip()
        name = str(row.get("f14") or "").strip()
        if code and name and re.match(r"^[0368]\d{5}$", code):
            result[name] = code
            short = name.replace("股份有限公司", "").replace("有限公司", "").strip()
            if short:
                result.setdefault(short, code)
    return result


def fetch_full_mapping() -> tuple[dict[str, str], str]:
    """Fetch A-share mapping, preferring Sina and falling back to Eastmoney."""
    mapping = fetch_from_sina()
    if mapping:
        return mapping, "sina_finance"
    mapping = fetch_from_eastmoney()
    if mapping:
        return mapping, "eastmoney_quote_api"
    return {}, "none"


def update_mapping_file(mapping: dict[str, str], path: Path | None = None, source: str = "unknown") -> None:
    """Write mapping to stock_name_to_code_data.json."""
    if path is None:
        path = Path(__file__).with_name("stock_name_to_code_data.json")
    data = {
        "schema_version": 1,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(mapping),
        "mapping": dict(sorted(mapping.items())),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {path} with {len(mapping)} entries from {source}")


def load_external_mapping(path: Path | None = None) -> dict[str, str]:
    """Load externally-sourced mapping (fetched from API)."""
    if path is None:
        path = Path(__file__).with_name("stock_name_to_code_data.json")
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("mapping", {})
        except Exception:
            return {}
    return {}


def name_to_code_with_external(name: str | None) -> tuple[str | None, str | None]:
    """Resolve stock code using both fallback + external mapping."""
    if not name:
        return None, None

    # 1. Try built-in fallback
    code, exchange = resolve_stock_code(name)
    if code:
        return code, exchange

    # 2. Try external mapping
    external = load_external_mapping()
    key = name.lower().strip()
    for ext_name, ext_code in external.items():
        if key == ext_name.lower() or key in ext_name.lower() or ext_name.lower() in key:
            return ext_code, infer_exchange_from_code(ext_code)

    # 3. Try extracting 6-digit code from name
    m = re.search(r"(?<!\d)([036]\d{5})(?!\d)", name)
    if m:
        code = m.group(1)
        return code, infer_exchange_from_code(code)

    return None, None


def name_to_code_detail(name: str | None) -> dict[str, str | None]:
    """
    Resolve stock code without network access and return trace details.

    This function intentionally does not call fetch_from_sina(); imports stay
    deterministic/offline. Refresh stock_name_to_code_data.json explicitly with
    the --fetch command when a live mapping update is desired.
    """
    result: dict[str, str | None] = {
        "stock_code": None,
        "exchange": None,
        "matched_name": None,
        "source": None,
    }
    if not name:
        return result

    key = name.lower().strip()
    code = NAME_TO_CODE.get(key)
    if code:
        result.update({
            "stock_code": code,
            "exchange": infer_exchange_from_code(code),
            "matched_name": name,
            "source": "fallback_exact",
        })
        return result

    for mapped_name, mapped_code in NAME_TO_CODE.items():
        if key in mapped_name or mapped_name in key:
            result.update({
                "stock_code": mapped_code,
                "exchange": infer_exchange_from_code(mapped_code),
                "matched_name": mapped_name,
                "source": "fallback_partial",
            })
            return result

    external = load_external_mapping()
    for ext_name, ext_code in external.items():
        ext_key = ext_name.lower()
        if key == ext_key or key in ext_key or ext_key in key:
            result.update({
                "stock_code": ext_code,
                "exchange": infer_exchange_from_code(ext_code),
                "matched_name": ext_name,
                "source": "external_mapping",
            })
            return result

    m = re.search(r"(?<!\d)([036]\d{5})(?!\d)", name)
    if m:
        code = m.group(1)
        result.update({
            "stock_code": code,
            "exchange": infer_exchange_from_code(code),
            "matched_name": code,
            "source": "embedded_code",
        })
    return result


def code_to_name_detail(code: str | None) -> dict[str, str | None]:
    """
    Resolve stock short name from a 6-digit A-share code without network access.

    Prefer short securities names over legal full names so downstream storage
    keys stay stable as filenames or PDF cover titles change.
    """
    result: dict[str, str | None] = {
        "stock_name": None,
        "stock_code": None,
        "exchange": None,
        "source": None,
    }
    if not code:
        return result

    normalized_code = str(code).strip()
    if not re.match(r"^[0368]\d{5}$", normalized_code):
        return result

    candidates: list[tuple[str, str]] = []
    for name, mapped_code in CODE_TO_NAME.items():
        if mapped_code == normalized_code:
            candidates.append((name, "fallback_code"))

    external = load_external_mapping()
    for name, mapped_code in external.items():
        if str(mapped_code).strip() == normalized_code:
            candidates.append((name, "external_mapping"))

    if not candidates:
        result.update({
            "stock_code": normalized_code,
            "exchange": infer_exchange_from_code(normalized_code),
        })
        return result

    def rank(item: tuple[str, str]) -> tuple[int, int, str]:
        name, source = item
        legal_suffix = int(any(suffix in name for suffix in ("股份有限公司", "有限责任公司", "有限公司")))
        source_rank = 0 if source == "external_mapping" else 1
        return (legal_suffix, source_rank, len(name), name)

    stock_name, source = sorted(candidates, key=rank)[0]
    result.update({
        "stock_name": stock_name,
        "stock_code": normalized_code,
        "exchange": infer_exchange_from_code(normalized_code),
        "source": source,
    })
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A-share stock name to code mapping.")
    parser.add_argument("--fetch", action="store_true", help="Fetch latest A-share mapping, with Sina then Eastmoney fallback")
    parser.add_argument("--fetch-sina", action="store_true", help="Fetch latest mapping from Sina Finance only")
    parser.add_argument("--fetch-eastmoney", action="store_true", help="Fetch latest mapping from Eastmoney quote API only")
    parser.add_argument("--output", type=Path, help="Output file path (default: stock_name_to_code_data.json)")
    args = parser.parse_args()

    if args.fetch or args.fetch_sina or args.fetch_eastmoney:
        if args.fetch_sina:
            mapping = fetch_from_sina()
            source = "sina_finance"
        elif args.fetch_eastmoney:
            mapping = fetch_from_eastmoney()
            source = "eastmoney_quote_api"
        else:
            mapping, source = fetch_full_mapping()

        if mapping:
            update_mapping_file(mapping, args.output, source=source)
        else:
            print("No data fetched. Check network connectivity.", file=sys.stderr)
            sys.exit(1)
    else:
        # Print current mapping stats
        external = load_external_mapping(args.output)
        print(f"Built-in fallback: {len(NAME_TO_CODE)} entries")
        print(f"External mapping:  {len(external)} entries")
        print(f"Total:             {len(NAME_TO_CODE) + len(external)} entries")
