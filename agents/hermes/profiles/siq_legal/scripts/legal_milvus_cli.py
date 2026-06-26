#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from urllib.error import URLError


PROFILE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_COLLECTION = "ic_legal_scanner"
DEFAULT_VECTOR_FIELD = "vector"
DEFAULT_OUTPUT_FIELDS = ["id", "project_tag", "metadata"]
TERM_RE = re.compile(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")


LAW_ALIASES = {
    "公司法": ["中华人民共和国公司法", "公司法"],
    "证券法": ["中华人民共和国证券法", "证券法"],
    "民法典": ["中华人民共和国民法典", "民法典"],
    "刑法": ["中华人民共和国刑法", "刑法"],
    "民事诉讼法": ["中华人民共和国民事诉讼法", "民事诉讼法", "民诉法"],
    "刑事诉讼法": ["中华人民共和国刑事诉讼法", "刑事诉讼法", "刑诉法"],
    "合伙企业法": ["中华人民共和国合伙企业法", "合伙企业法"],
    "证券投资基金法": ["中华人民共和国证券投资基金法", "证券投资基金法", "基金法"],
    "上市规则": ["股票上市规则", "上海证券交易所股票上市规则", "深圳证券交易所股票上市规则", "北京证券交易所股票上市规则"],
    "信披办法": ["上市公司信息披露管理办法", "信息披露管理办法"],
    "治理准则": ["上市公司治理准则"],
    "独董办法": ["上市公司独立董事管理办法", "独立董事管理办法"],
    "重组办法": ["上市公司重大资产重组管理办法", "重大资产重组管理办法"],
    "收购办法": ["上市公司收购管理办法", "收购管理办法"],
}

SOURCE_BOOST_RULES = {
    "governance": ["公司法", "上市公司治理准则", "上市公司独立董事管理办法", "股票上市规则", "监管指引"],
    "disclosure": ["证券法", "上市公司信息披露管理办法", "股票上市规则", "监管指引", "格式准则"],
    "transaction": [
        "公司法",
        "证券法",
        "上市公司信息披露管理办法",
        "上市公司重大资产重组管理办法",
        "上市公司收购管理办法",
        "股票上市规则",
        "监管指引",
    ],
    "enforcement": ["证券法", "行政处罚", "股票上市规则", "纪律处分", "退市", "监管措施"],
    "compliance": ["公司法", "证券法", "内部控制", "数据安全法", "个人信息保护法", "网络安全法", "环境保护法", "安全生产法"],
    "law": ["公司法", "证券法", "民法典", "刑法", "条例", "办法", "规定", "规则", "指引"],
    "default": ["民法典", "公司法", "证券法", "刑法", "条例", "办法", "规定"],
}


LISTED_COMPANY_SYNONYMS = {
    "上市公司治理": ["公司治理", "治理结构", "三会运作", "董事会", "股东大会", "监事会", "审计委员会"],
    "独立董事": ["独董", "上市公司独立董事", "独立董事任职", "独立董事履职", "独立性"],
    "董监高": ["董事", "监事", "高级管理人员", "高级管理层", "忠实义务", "勤勉义务"],
    "信息披露": ["临时公告", "定期报告", "重大事项披露", "真实准确完整", "虚假记载", "误导性陈述", "重大遗漏"],
    "关联交易": ["关联方交易", "关联关系", "利益输送", "资金占用", "关联担保", "关联采购", "关联销售"],
    "同业竞争": ["竞业禁止", "同业禁止", "避免同业竞争", "业务独立性", "横向竞争"],
    "实际控制人": ["控股股东", "控制股东", "实控人", "最终控制人", "一致行动人"],
    "股权激励": ["员工持股计划", "限制性股票", "股票期权", "激励对象", "业绩考核"],
    "股份减持": ["减持", "大股东减持", "董监高减持", "短线交易", "窗口期", "限售股"],
    "股份回购": ["回购股份", "库存股", "回购注销", "稳定股价"],
    "重大资产重组": ["资产重组", "并购重组", "发行股份购买资产", "重大资产购买", "借壳上市"],
    "再融资": ["定向增发", "向特定对象发行", "可转债", "配股", "募集资金"],
    "募集资金": ["募投项目", "募集资金用途", "专户存储", "闲置募集资金", "变更募投"],
    "内幕交易": ["内幕信息", "内幕信息知情人", "敏感期交易", "泄露内幕信息", "操纵市场"],
    "财务造假": ["虚增收入", "虚增利润", "会计差错", "审计意见", "非标意见", "内部控制缺陷"],
    "内部控制": ["内控", "内控制度", "内控缺陷", "财务报告内部控制", "合规管理"],
    "行政处罚": ["监管处罚", "责令改正", "警示函", "监管函", "纪律处分", "立案调查"],
    "诉讼仲裁": ["重大诉讼", "仲裁", "执行案件", "冻结", "查封", "或有负债"],
    "担保": ["对外担保", "违规担保", "关联担保", "保证", "抵押", "质押"],
    "数据合规": ["个人信息保护", "数据安全", "网络安全", "数据出境", "隐私政策", "算法备案"],
    "ESG": ["环境信息披露", "社会责任", "可持续发展", "安全生产", "环保处罚", "碳排放"],
    "退市风险": ["退市", "风险警示", "ST", "重大违法强制退市", "财务类退市"],
}

GENERAL_LEGAL_SYNONYMS = {
    "民法典": ["民事责任", "民事法律行为", "合同编", "物权编", "侵权责任编", "人格权编"],
    "合同纠纷": ["合同解除", "违约责任", "合同无效", "定金", "违约金", "继续履行"],
    "借款纠纷": ["民间借贷", "借款合同", "利息", "逾期利息", "保证责任"],
    "担保": ["保证", "抵押", "质押", "留置", "定金", "担保责任"],
    "劳动争议": ["劳动合同", "经济补偿", "违法解除", "加班费", "工伤", "竞业限制"],
    "交通事故": ["机动车交通事故", "赔偿责任", "交强险", "商业三者险", "误工费", "伤残赔偿"],
    "婚姻家事": ["离婚", "夫妻共同财产", "抚养权", "抚养费", "继承", "遗嘱"],
    "侵权责任": ["人身损害", "财产损害", "过错责任", "无过错责任", "精神损害赔偿"],
    "消费者权益": ["退一赔三", "欺诈", "产品质量", "网络购物", "格式条款"],
    "行政处罚": ["罚款", "责令改正", "行政复议", "行政诉讼", "听证"],
    "刑事犯罪": ["诈骗罪", "职务侵占罪", "挪用资金罪", "合同诈骗罪", "非法经营罪"],
}

QUERY_CLASSIFICATION = {
    "governance": ["治理", "董事会", "股东大会", "监事会", "独立董事", "董监高", "章程", "专门委员会"],
    "disclosure": ["信息披露", "披露", "公告", "定期报告", "临时报告", "虚假记载", "重大遗漏", "误导性陈述"],
    "transaction": ["关联交易", "重大资产重组", "并购", "收购", "再融资", "募集资金", "担保", "减持", "回购"],
    "enforcement": ["处罚", "监管函", "警示函", "立案调查", "纪律处分", "诉讼", "仲裁", "执行", "退市"],
    "compliance": ["内控", "合规", "数据", "网络安全", "个人信息", "ESG", "环保", "安全生产", "资质"],
    "law": ["第", "条", "法条", "公司法", "证券法", "上市规则", "监管指引", "管理办法", "条例"],
}

WEIGHT_PROFILES = {
    "governance": {"vector": 0.55, "keyword": 0.45, "rrf": 0.35, "desc": "上市公司治理"},
    "disclosure": {"vector": 0.45, "keyword": 0.55, "rrf": 0.40, "desc": "信息披露"},
    "transaction": {"vector": 0.55, "keyword": 0.45, "rrf": 0.35, "desc": "资本运作/交易合规"},
    "enforcement": {"vector": 0.50, "keyword": 0.50, "rrf": 0.45, "desc": "监管处罚/争议风险"},
    "compliance": {"vector": 0.65, "keyword": 0.35, "rrf": 0.30, "desc": "专项合规"},
    "law": {"vector": 0.45, "keyword": 0.55, "rrf": 0.45, "desc": "法条精确查询"},
    "default": {"vector": 0.65, "keyword": 0.35, "rrf": 0.25, "desc": "综合法律检索"},
}

RERANK_INSTRUCTIONS = {
    "governance": "上市公司治理评估：优先匹配董事会、股东大会、独立董事、董监高义务、控制权和章程治理相关法规。",
    "disclosure": "信息披露合规评估：优先匹配定期报告、临时公告、重大事项、虚假记载、误导性陈述和重大遗漏相关规则。",
    "transaction": "资本运作合规评估：优先匹配关联交易、重大资产重组、再融资、募集资金、担保、股份减持和回购规则。",
    "enforcement": "监管处罚与争议风险评估：优先匹配行政处罚、监管措施、纪律处分、诉讼仲裁、退市风险和法律责任。",
    "compliance": "上市公司专项合规评估：优先匹配内控、数据安全、个人信息、网络安全、ESG、环保、安全生产和资质许可要求。",
    "law": "法条精确检索：优先匹配法规名称、条款号、限定词和直接可引用的规范性条文。",
    "default": "综合法律检索：优先选择能直接回答问题、来源清晰、可引用的法规片段；如问题涉及上市公司，再关注治理、披露和交易合规。",
}


def load_env() -> None:
    env_path = os.path.join(PROFILE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run_node(js: str, *, timeout: int = 120) -> dict:
    proc = subprocess.run(
        ["docker", "exec", "-i", "milvus-attu", "node", "-"],
        input=js,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip() or "docker/node command failed")
    output = proc.stdout.strip()
    if not output:
        return {}
    return json.loads(output)


def node_header() -> str:
    address = os.environ.get("MILVUS_DOCKER_NETWORK_HOST", "standalone:19530")
    return f"""
const {{ MilvusClient }} = require("@zilliz/milvus2-sdk-node");
const client = new MilvusClient({{ address: {json.dumps(address)} }});
"""


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


@dataclass
class Candidate:
    id: str
    project_tag: str
    metadata: dict
    vector_score: float | None = None
    keyword_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    exact_reason: str | None = None
    source_boost: float = 0.0

    @property
    def text(self) -> str:
        return str(self.metadata.get("text") or "")

    @property
    def source_path(self) -> str:
        return str(self.metadata.get("source_path") or "")

    @property
    def chunk_index(self) -> int | str:
        return self.metadata.get("chunk_index", "")

    @property
    def source(self) -> str:
        return str(self.metadata.get("source") or "")

    @property
    def dedupe_key(self) -> str:
        if self.source_path or self.chunk_index != "":
            return f"{self.source_path}#{self.chunk_index}"
        return self.id

    def to_dict(self, rank: int | None = None) -> dict:
        data = {
            "id": self.id,
            "project_tag": self.project_tag,
            "source": self.metadata.get("source"),
            "source_path": self.source_path,
            "chunk_index": self.chunk_index,
            "total_chunks": self.metadata.get("total_chunks"),
            "vector_score": self.vector_score,
            "keyword_score": self.keyword_score,
            "rrf_score": self.rrf_score,
            "rerank_score": self.rerank_score,
            "exact_reason": self.exact_reason,
            "source_boost": self.source_boost,
            "text": self.text,
        }
        if rank is not None:
            data["rank"] = rank
        return data


def collection_name(args: argparse.Namespace) -> str:
    return args.collection or os.environ.get("LEGAL_MILVUS_COLLECTION", DEFAULT_COLLECTION)


def vector_field_name() -> str:
    return os.environ.get("LEGAL_MILVUS_VECTOR_FIELD", DEFAULT_VECTOR_FIELD)


def vector_metric_type() -> str:
    return os.environ.get("LEGAL_MILVUS_METRIC_TYPE", "IP")


def classify_query(query: str) -> str:
    q = query.lower()
    scores = {
        name: sum(1 for keyword in keywords if keyword.lower() in q)
        for name, keywords in QUERY_CLASSIFICATION.items()
    }
    if any(term in query for term in ("信息披露", "披露", "公告", "定期报告", "临时报告")):
        scores["disclosure"] = scores.get("disclosure", 0) + 2
    if not scores or max(scores.values()) == 0:
        return "default"
    return max(scores, key=scores.get)


def query_terms(query: str) -> list[str]:
    terms = []
    compact_query = query.strip().replace(" ", "")
    if 2 <= len(compact_query) <= 12:
        terms.append(compact_query)
    terms.extend(re.findall(r"[\u4e00-\u9fff]{2,24}?(?:法|条例|规定|解释|办法|决定|规则|指引)", query))
    terms.extend(re.findall(r"第[一二三四五六七八九十百千万零〇两\d]+条(?:之一|之二|之三)?", query))
    for core, synonyms in {**LISTED_COMPANY_SYNONYMS, **GENERAL_LEGAL_SYNONYMS}.items():
        if core in query:
            terms.append(core)
            terms.extend(synonyms)
        elif any(syn in query for syn in synonyms):
            terms.append(core)
            terms.extend([syn for syn in synonyms if syn in query])
    for alias, names in LAW_ALIASES.items():
        if alias in query or any(name in query for name in names):
            terms.extend(names)

    for term in TERM_RE.findall(query):
        if 2 <= len(term) <= 8:
            terms.append(term)

    seen = set()
    cleaned = []
    stop = {"请问", "如何", "什么", "哪些", "相关", "规定", "需要", "怎么办", "评估", "风险"}
    for term in terms:
        term = term.strip().replace(" ", "")
        if len(term) < 2 or term in stop or term in seen:
            continue
        seen.add(term)
        cleaned.append(term)
    return cleaned[:14]


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def normalize_milvus_hits(payload: dict, *, score_field: str = "vector_score") -> list[Candidate]:
    raw_hits = payload.get("results") or payload.get("data") or []
    if raw_hits and isinstance(raw_hits[0], list):
        raw_hits = raw_hits[0]

    candidates: list[Candidate] = []
    for hit in raw_hits:
        entity = hit.get("entity") or hit
        metadata = entity.get("metadata") or hit.get("metadata") or {}
        score = hit.get("score", hit.get("distance", hit.get("similarity")))
        candidate = Candidate(
            id=str(entity.get("id") or hit.get("id") or ""),
            project_tag=str(entity.get("project_tag") or hit.get("project_tag") or ""),
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        if score is not None:
            setattr(candidate, score_field, float(score))
        candidates.append(candidate)
    return candidates


def search_candidates(collection: str, vector: list[float], top_k: int) -> list[Candidate]:
    return search_candidates_batch(collection, [{"vector": vector, "limit": top_k}])


def search_candidates_batch(collection: str, specs: list[dict]) -> list[Candidate]:
    if not specs:
        return []
    data = run_node(node_header() + f"""
(async () => {{
  const specs = {json.dumps(specs, ensure_ascii=False)};
  const batches = [];
  for (const spec of specs) {{
    try {{
      const request = {{
        collection_name: {json.dumps(collection)},
        vector: spec.vector,
        anns_field: {json.dumps(vector_field_name())},
        limit: spec.limit,
        metric_type: {json.dumps(vector_metric_type())},
        params: {{ ef: 256 }},
        output_fields: {json.dumps(DEFAULT_OUTPUT_FIELDS)}
      }};
      if (spec.expr) request.expr = spec.expr;
      const res = await client.search(request);
      batches.push({{spec, res}});
    }} catch (e) {{
      batches.push({{spec, error: e.stack || e.message || String(e)}});
    }}
  }}
  console.log(JSON.stringify({{batches}}));
}})().catch(e => {{ console.error(e.stack || e.message); process.exit(1); }});
""")
    results: list[Candidate] = []
    for batch in data.get("batches", []):
        spec = batch.get("spec") or {}
        if batch.get("error"):
            print(f"warning: vector query skipped: {spec.get('expr') or '<all>'} ({batch['error']})", file=sys.stderr)
            continue
        hits = normalize_milvus_hits(batch.get("res") or {}, score_field="vector_score")
        for hit in hits:
            if spec.get("reason"):
                hit.exact_reason = spec.get("reason")
            hit.source_boost = max(hit.source_boost, float(spec.get("source_boost") or 0.0))
        results.extend(hits)
    return results


def status(_: argparse.Namespace) -> None:
    data = run_node(node_header() + """
(async () => {
  const res = await client.listCollections();
  console.log(JSON.stringify({
    ok: res.status && res.status.error_code === "Success",
    status: res.status,
    collection_count: (res.collection_names || []).length,
    collections: res.collection_names || []
  }));
})().catch(e => { console.error(e.stack || e.message); process.exit(1); });
""")
    print_json(data)


def collections(_: argparse.Namespace) -> None:
    data = run_node(node_header() + """
(async () => {
  const res = await client.listCollections();
  console.log(JSON.stringify(res));
})().catch(e => { console.error(e.stack || e.message); process.exit(1); });
""")
    print_json(data)


def schema(args: argparse.Namespace) -> None:
    collection = collection_name(args)
    data = run_node(node_header() + f"""
(async () => {{
  const res = await client.describeCollection({{ collection_name: {json.dumps(collection)} }});
  console.log(JSON.stringify(res));
}})().catch(e => {{ console.error(e.stack || e.message); process.exit(1); }});
""")
    print_json(data)


def sample(args: argparse.Namespace) -> None:
    collection = collection_name(args)
    limit = max(1, min(args.limit, 20))
    data = run_node(node_header() + f"""
(async () => {{
  const res = await client.query({{
    collection_name: {json.dumps(collection)},
    limit: {limit},
    output_fields: ["id", "project_tag", "metadata"],
    expr: ""
  }});
  console.log(JSON.stringify(res));
}})().catch(e => {{ console.error(e.stack || e.message); process.exit(1); }});
""")
    print_json(data)


def embed(text: str) -> list[float]:
    api_url = os.environ.get("LEGAL_EMBEDDING_API_URL", "").rstrip("/")
    model = os.environ.get("LEGAL_EMBEDDING_MODEL", "")
    api_key = os.environ.get("LEGAL_EMBEDDING_API_KEY", "")
    if not api_url or not model:
        raise SystemExit(
            "LEGAL_EMBEDDING_API_URL/LEGAL_EMBEDDING_MODEL 未配置；"
            "当前可用 status/collections/schema/sample，语义 search 需要 1024 维 embedding 服务。"
        )
    if api_url.startswith("docker://"):
        payload = embed_via_docker(api_url, model, text)
    else:
        endpoint = api_url if api_url.endswith("/embeddings") else f"{api_url}/embeddings"
        req = urllib.request.Request(
            endpoint,
            data=json.dumps({"model": model, "input": text}, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key or 'EMPTY'}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except URLError as exc:
            raise SystemExit(f"embedding 服务不可用：{endpoint} ({exc})") from exc
    vector = payload["data"][0]["embedding"]
    dim = int(os.environ.get("LEGAL_MILVUS_DIM", "1024"))
    if len(vector) != dim:
        raise SystemExit(f"embedding 维度不匹配：返回 {len(vector)}，Milvus 需要 {dim}")
    return vector


def embed_via_docker(api_url: str, model: str, text: str) -> dict:
    target = api_url.removeprefix("docker://")
    container, _, path = target.partition("/")
    if not container:
        raise SystemExit("LEGAL_EMBEDDING_API_URL docker endpoint 缺少容器名")
    path = "/" + (path or "v1").strip("/")
    endpoint = f"http://127.0.0.1:8000{path}/embeddings"
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "curl",
            "-s",
            "-X",
            "POST",
            endpoint,
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps({"model": model, "input": text}, ensure_ascii=False),
        ],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip() or f"docker embedding call failed: {container}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"embedding 服务返回非 JSON：{proc.stdout[:500]}") from exc
    if "data" not in payload:
        raise SystemExit(f"embedding 服务返回异常：{json.dumps(payload, ensure_ascii=False)[:500]}")
    return payload


def embedding_configured() -> bool:
    return bool(os.environ.get("LEGAL_EMBEDDING_API_URL", "").strip() and os.environ.get("LEGAL_EMBEDDING_MODEL", "").strip())


def search(args: argparse.Namespace) -> None:
    collection = collection_name(args)
    vector = embed(args.query)
    top_k = max(1, min(args.top_k, 100))
    print_json([item.to_dict(i + 1) for i, item in enumerate(search_candidates(collection, vector, top_k))])


def keyword_score_for(term: str, query: str) -> float:
    if term == query.strip().replace(" ", ""):
        return 0.95
    if len(term) >= 4:
        return 0.75
    return 0.55


def milvus_keyword_query(collection: str, expr: str, *, limit: int, reason: str, score: float) -> list[Candidate]:
    return milvus_keyword_queries(
        collection,
        [{"expr": expr, "limit": limit, "reason": reason, "score": score}],
    )


def milvus_keyword_queries(collection: str, specs: list[dict]) -> list[Candidate]:
    if not specs:
        return []
    data = run_node(node_header() + f"""
(async () => {{
  const specs = {json.dumps(specs, ensure_ascii=False)};
  const batches = [];
  for (const spec of specs) {{
    try {{
      const res = await client.query({{
        collection_name: {json.dumps(collection)},
        expr: spec.expr,
        limit: spec.limit,
        output_fields: {json.dumps(DEFAULT_OUTPUT_FIELDS)}
      }});
      batches.push({{spec, res}});
    }} catch (e) {{
      batches.push({{spec, error: e.stack || e.message || String(e)}});
    }}
  }}
  console.log(JSON.stringify({{batches}}));
}})().catch(e => {{ console.error(e.stack || e.message); process.exit(1); }});
""")
    results: list[Candidate] = []
    for batch in data.get("batches", []):
        spec = batch.get("spec") or {}
        if batch.get("error"):
            print(f"warning: keyword query skipped: {spec.get('expr')} ({batch['error']})", file=sys.stderr)
            continue
        hits = normalize_milvus_hits(batch.get("res") or {}, score_field="keyword_score")
        for hit in hits:
            hit.keyword_score = max(hit.keyword_score or 0.0, float(spec.get("score") or 0.0))
            hit.exact_reason = spec.get("reason")
            hit.source_boost = max(hit.source_boost, float(spec.get("source_boost") or 0.0))
        results.extend(hits)
    return results


def add_keyword_spec(specs: list[dict], expr: str, *, limit: int, reason: str, score: float, source_boost: float = 0.0) -> None:
    specs.append(
        {
            "expr": expr,
            "limit": limit,
            "reason": reason,
            "score": score,
            "source_boost": source_boost,
        }
    )


def keyword_candidates(collection: str, query: str, *, top_k: int) -> list[Candidate]:
    specs: list[dict] = []
    per_expr = max(1, min(8, top_k))
    terms = query_terms(query)[:8]
    for term_index, term in enumerate(terms):
        escaped = escape_like(term)
        exprs = [
            f'metadata["source"] like "%{escaped}%"',
        ]
        if term_index < 4 and len(term) <= 8:
            exprs.append(f'metadata["text"] like "%{escaped}%"')
        for expr in exprs:
            if len(specs) >= 12:
                return milvus_keyword_queries(collection, specs)[: top_k * 2]
            add_keyword_spec(
                specs,
                expr,
                limit=per_expr,
                reason=f"keyword:{term}",
                score=keyword_score_for(term, query),
            )
    return milvus_keyword_queries(collection, specs)[: top_k * 2]


def source_focused_candidates(collection: str, query: str, query_type: str, *, top_k: int) -> list[Candidate]:
    specs: list[dict] = []
    source_terms = SOURCE_BOOST_RULES.get(query_type, SOURCE_BOOST_RULES["default"])[:6]
    content_terms = [term for term in query_terms(query) if 2 <= len(term) <= 10][:4]
    per_expr = max(1, min(4, top_k))
    seen_expr = set()
    for source_term in source_terms:
        for content_term in content_terms:
            expr = (
                f'metadata["source"] like "%{escape_like(source_term)}%" '
                f'and metadata["text"] like "%{escape_like(content_term)}%"'
            )
            if expr in seen_expr:
                continue
            seen_expr.add(expr)
            if len(specs) >= 12:
                return milvus_keyword_queries(collection, specs)[:top_k]
            add_keyword_spec(
                specs,
                expr,
                limit=per_expr,
                reason=f"source_focus:{source_term}:{content_term}",
                score=0.92,
                source_boost=0.22,
            )
    return milvus_keyword_queries(collection, specs)[:top_k]


def source_vector_candidates(collection: str, vector: list[float], query_type: str, *, top_k: int) -> list[Candidate]:
    source_terms = SOURCE_BOOST_RULES.get(query_type, SOURCE_BOOST_RULES["default"])[:4]
    if not source_terms or top_k <= 0:
        return []
    per_expr = max(1, min(4, top_k))
    specs = [
        {
            "vector": vector,
            "limit": per_expr,
            "expr": f'metadata["source"] like "%{escape_like(term)}%"',
            "reason": f"source_vector:{term}",
            "source_boost": 0.24,
        }
        for term in source_terms
    ]
    return search_candidates_batch(collection, specs)[:top_k]


CN_NUM = "零一二三四五六七八九"


def arabic_to_cn(num: int) -> str:
    if num <= 0:
        return str(num)
    units = ["", "十", "百", "千"]
    parts = []
    digits = list(map(int, str(num)))
    length = len(digits)
    for i, digit in enumerate(digits):
        pos = length - i - 1
        if digit == 0:
            if parts and parts[-1] != "零":
                parts.append("零")
            continue
        if digit == 1 and pos == 1 and not parts:
            parts.append("十")
        else:
            parts.append(CN_NUM[digit] + units[pos])
    return "".join(parts).rstrip("零")


def article_variants(article: str) -> list[str]:
    match = re.search(r"第([0-9]+)条", article)
    if not match:
        return [article]
    suffix = ""
    for candidate in ("之一", "之二", "之三"):
        if candidate in article:
            suffix = candidate
    cn = arabic_to_cn(int(match.group(1)))
    return [article, f"第{cn}条{suffix}"]


def extract_article_queries(query: str) -> list[tuple[str | None, str]]:
    results: list[tuple[str | None, str]] = []
    article_pattern = re.compile(r"第[一二三四五六七八九十百千万零〇两\d]+条(?:之一|之二|之三)?")
    law_pattern = re.compile(r"(?:《([^》]+)》|([\u4e00-\u9fff]{2,24}?(?:法|条例|规定|解释|办法|决定|规则|指引)))$")
    for match in article_pattern.finditer(query):
        prefix = query[max(0, match.start() - 36):match.start()]
        law_match = law_pattern.search(prefix)
        law_name = (law_match.group(1) or law_match.group(2)).strip() if law_match else None
        law_names = expand_law_names(law_name) if law_name else [None]
        for name in law_names:
            results.append((name, match.group(0)))
    compact = query.replace(" ", "")
    for match in re.finditer(r"([\u4e00-\u9fff]{2,24}?(?:法|条例|规定|解释|办法|决定|规则|指引))第([0-9]+)条", compact):
        for name in expand_law_names(match.group(1)):
            results.append((name, f"第{match.group(2)}条"))
    return results


def expand_law_names(law_name: str | None) -> list[str]:
    if not law_name:
        return []
    names = [law_name]
    for alias, variants in LAW_ALIASES.items():
        if law_name == alias or law_name in variants or alias in law_name:
            names.extend(variants)
    seen = set()
    return [name for name in names if name and not (name in seen or seen.add(name))]


def exact_article_candidates(collection: str, query: str, *, top_k: int) -> list[Candidate]:
    results: list[Candidate] = []
    seen_expr = set()
    for law_name, article in extract_article_queries(query):
        for variant in article_variants(article):
            escaped_article = escape_like(variant)
            if law_name:
                escaped_law = escape_like(law_name)
                exprs = [
                    f'metadata["source"] like "%{escaped_law}%" and metadata["text"] like "%{escaped_article}%"',
                    f'metadata["source_path"] like "%{escaped_law}%" and metadata["text"] like "%{escaped_article}%"',
                ]
            else:
                exprs = [f'metadata["text"] like "%{escaped_article}%"']
            for expr in exprs:
                if expr in seen_expr:
                    continue
                seen_expr.add(expr)
                results.extend(
                    milvus_keyword_query(
                        collection,
                        expr,
                        limit=max(2, min(top_k, 10)),
                        reason=f"article:{law_name or ''}{variant}",
                        score=1.0,
                    )
                )
    return results


def neighbor_candidates(collection: str, candidates: list[Candidate], *, radius: int = 1) -> list[Candidate]:
    results: list[Candidate] = []
    seen_expr = set()
    for candidate in candidates:
        source_path = candidate.source_path
        try:
            chunk_index = int(candidate.chunk_index)
        except (TypeError, ValueError):
            continue
        if not source_path:
            continue
        for neighbor in range(chunk_index - radius, chunk_index + radius + 1):
            if neighbor <= 0 or neighbor == chunk_index:
                continue
            expr = (
                f'metadata["source_path"] == "{escape_like(source_path)}" '
                f'and metadata["chunk_index"] == {neighbor}'
            )
            if expr in seen_expr:
                continue
            seen_expr.add(expr)
            try:
                hits = milvus_keyword_query(
                    collection,
                    expr,
                    limit=2,
                    reason=f"neighbor:{candidate.source}#{chunk_index}",
                    score=max((candidate.keyword_score or 0.0) - 0.08, 0.55),
                )
            except SystemExit as exc:
                print(f"warning: neighbor query skipped: {expr} ({exc})", file=sys.stderr)
                continue
            results.extend(hits)
    return results


def rerank_candidates(query: str, candidates: list[Candidate], *, top_n: int) -> list[Candidate]:
    api_url = os.environ.get("LEGAL_RERANKER_API_URL", "").rstrip("/")
    model = os.environ.get("LEGAL_RERANKER_MODEL", "Qwen3-VL-Reranker-2B")
    query_type = classify_query(query)
    if not api_url or not candidates:
        return candidates

    endpoint = api_url if api_url.endswith("/rerank") else f"{api_url}/rerank"
    documents = [{"text": f"[{item.metadata.get('source', '')}] {item.text[:8000]}"} for item in candidates]
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(
            {
                "model": model,
                "query": query,
                "documents": documents,
                "top_n": min(top_n, len(documents)),
                "return_sigmoid": True,
                "instruction": RERANK_INSTRUCTIONS.get(query_type, RERANK_INSTRUCTIONS["default"]),
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except URLError as exc:
        print(f"warning: reranker unavailable: {endpoint} ({exc})", file=sys.stderr)
        return candidates

    reranked: list[Candidate] = []
    for item in payload.get("data", payload.get("results", [])):
        idx = int(item.get("index", -1))
        if idx < 0 or idx >= len(candidates):
            continue
        candidate = candidates[idx]
        score = item.get("relevance_score", item.get("score"))
        if score is not None:
            candidate.rerank_score = float(score)
        reranked.append(candidate)
    return reranked or candidates


def merge_candidates(groups: list[list[Candidate]]) -> list[Candidate]:
    merged: dict[str, Candidate] = {}
    for group in groups:
        for candidate in group:
            key = candidate.dedupe_key
            if key not in merged:
                merged[key] = candidate
                continue
            existing = merged[key]
            existing.vector_score = max(
                [score for score in (existing.vector_score, candidate.vector_score) if score is not None],
                default=None,
            )
            existing.keyword_score = max(
                [score for score in (existing.keyword_score, candidate.keyword_score) if score is not None],
                default=None,
            )
            existing.rrf_score = max(
                [score for score in (existing.rrf_score, candidate.rrf_score) if score is not None],
                default=None,
            )
            existing.rerank_score = max(
                [score for score in (existing.rerank_score, candidate.rerank_score) if score is not None],
                default=None,
            )
            existing.source_boost = max(existing.source_boost, candidate.source_boost)
            if not existing.exact_reason and candidate.exact_reason:
                existing.exact_reason = candidate.exact_reason
    return list(merged.values())


def apply_rrf(groups: list[list[Candidate]], *, rrf_k: int) -> None:
    scores: defaultdict[str, float] = defaultdict(float)
    for group in groups:
        for rank, item in enumerate(group, start=1):
            scores[item.dedupe_key] += 1.0 / (rrf_k + rank)
    for group in groups:
        for item in group:
            item.rrf_score = scores[item.dedupe_key]


def source_mismatch_penalty(item: Candidate, query_type: str, query: str) -> float:
    source_text = f"{item.source} {item.source_path}"
    if "税" not in query and any(term in source_text for term in ("企业所得税", "增值税", "税收", "关税")):
        return 0.45
    if query_type in {"governance", "disclosure", "transaction", "enforcement"}:
        preferred = SOURCE_BOOST_RULES.get(query_type, [])
        if preferred and not any(term in source_text for term in preferred):
            return 0.12
    return 0.0


def fallback_score(item: Candidate, profile: dict, query_type: str = "default", query: str = "") -> float:
    exact = 1.0 if item.exact_reason and item.exact_reason.startswith("article:") else 0.0
    vector = item.vector_score or 0.0
    keyword = item.keyword_score or 0.0
    rrf = min((item.rrf_score or 0.0) * 10.0, 1.0)
    penalty = source_mismatch_penalty(item, query_type, query)
    return exact + (item.source_boost * 1.4) + profile["vector"] * vector + profile["keyword"] * keyword + profile["rrf"] * rrf - penalty


def final_score(item: Candidate, profile: dict, query_type: str, query: str) -> float:
    if item.rerank_score is None:
        return fallback_score(item, profile, query_type, query)
    exact = 1.0 if item.exact_reason and item.exact_reason.startswith("article:") else 0.0
    penalty = source_mismatch_penalty(item, query_type, query)
    return item.rerank_score + exact + (item.source_boost * 0.9) - penalty


def source_boost(item: Candidate, query_type: str) -> float:
    source_text = f"{item.source} {item.source_path}"
    rules = SOURCE_BOOST_RULES.get(query_type, SOURCE_BOOST_RULES["default"])
    boost = 0.0
    for term in rules:
        if term in source_text:
            boost = max(boost, 0.25)
    if any(name in source_text for names in LAW_ALIASES.values() for name in names):
        boost = max(boost, 0.10)
    if item.exact_reason and item.exact_reason.startswith("article:"):
        boost = max(boost, 0.25)
    return boost


def apply_source_boost(items: list[Candidate], query_type: str) -> None:
    for item in items:
        item.source_boost = max(item.source_boost, source_boost(item, query_type))


def source_diverse(items: list[Candidate], *, per_doc_chunks: int) -> list[Candidate]:
    groups: defaultdict[str, list[Candidate]] = defaultdict(list)
    for item in items:
        key = str(item.metadata.get("source") or item.source_path or item.id)
        groups[key].append(item)
    diverse = []
    for chunks in groups.values():
        diverse.extend(chunks[:per_doc_chunks])
    return diverse


def hybrid_search_result(args: argparse.Namespace) -> dict:
    collection = collection_name(args)
    query_type = args.profile or classify_query(args.query)
    profile = WEIGHT_PROFILES.get(query_type, WEIGHT_PROFILES["default"])
    vector_top_k = max(0, min(args.vector_top_k, 300))
    keyword_top_k = max(0, min(args.keyword_top_k, 120))
    source_top_k = max(0, min(getattr(args, "source_top_k", 0), 40))
    final_top_k = max(1, min(args.top_k, 50))

    vector_groups: list[list[Candidate]] = []
    main_vector: list[float] | None = None
    expanded_queries = [args.query] + [term for term in query_terms(args.query) if term != args.query][: args.expansion_limit]
    if vector_top_k and not embedding_configured():
        print("warning: embedding is not configured; hybrid_search falls back to Milvus keyword/article retrieval", file=sys.stderr)
        vector_top_k = 0
    if vector_top_k:
        per_query = max(1, vector_top_k // max(1, len(expanded_queries)))
        vector_specs = []
        for query in expanded_queries:
            vector = embed(query)
            if main_vector is None:
                main_vector = vector
            vector_specs.append({"vector": vector, "limit": per_query})
        vector_groups.append(search_candidates_batch(collection, vector_specs))

    exact_hits = exact_article_candidates(collection, args.query, top_k=keyword_top_k) if keyword_top_k else []
    neighbor_hits = neighbor_candidates(collection, exact_hits, radius=args.neighbor_radius) if args.neighbor_radius > 0 else []
    source_hits = []
    if source_top_k and main_vector:
        source_hits.extend(source_vector_candidates(collection, main_vector, query_type, top_k=source_top_k))
    if source_top_k and keyword_top_k >= 8:
        source_hits.extend(source_focused_candidates(collection, args.query, query_type, top_k=max(1, source_top_k // 2)))
    keyword_hits = keyword_candidates(collection, args.query, top_k=keyword_top_k) if keyword_top_k else []
    groups = vector_groups + [exact_hits, neighbor_hits, source_hits, keyword_hits]
    apply_rrf(groups, rrf_k=args.rrf_k)
    merged = merge_candidates(groups)
    apply_source_boost(merged, query_type)

    ranked_pre = sorted(merged, key=lambda item: fallback_score(item, profile, query_type, args.query), reverse=True)
    if args.per_doc_chunks > 0:
        ranked_pre = source_diverse(ranked_pre, per_doc_chunks=args.per_doc_chunks)
        ranked_pre = sorted(ranked_pre, key=lambda item: fallback_score(item, profile, query_type, args.query), reverse=True)

    if args.no_rerank:
        ranked = ranked_pre[: args.rerank_pool]
    else:
        rerank_pool = ranked_pre[: args.rerank_pool]
        ranked = rerank_candidates(args.query, rerank_pool, top_n=min(args.rerank_pool, len(rerank_pool)))
        ranked = sorted(ranked, key=lambda item: final_score(item, profile, query_type, args.query), reverse=True)

    return {
        "query": args.query,
        "collection": collection,
        "query_type": query_type,
        "profile_desc": profile["desc"],
        "expanded_queries": expanded_queries,
        "vector_hits": sum(len(group) for group in vector_groups),
        "keyword_hits": len(keyword_hits),
        "exact_article_hits": len(exact_hits),
        "neighbor_hits": len(neighbor_hits),
        "source_focused_hits": len(source_hits),
        "merged_hits": len(merged),
        "rerank_enabled": not args.no_rerank and bool(os.environ.get("LEGAL_RERANKER_API_URL", "")),
        "results": [item.to_dict(i + 1) for i, item in enumerate(ranked[:final_top_k])],
    }


def hybrid_search(args: argparse.Namespace) -> None:
    print_json(hybrid_search_result(args))


BENCHMARK_CASES = [
    {"query": "公司法第136条独立董事", "expect": ["中华人民共和国公司法", "第一百三十六条", "独立董事"]},
    {"query": "上市公司关联交易披露要求", "expect": ["关联交易", "信息披露", "上市公司"]},
    {"query": "控股股东资金占用有什么合规风险", "expect": ["控股股东", "资金占用", "上市公司"]},
    {"query": "上市公司重大资产重组需要履行哪些程序", "expect": ["重大资产重组", "上市公司"]},
    {"query": "董监高减持窗口期有什么限制", "expect": ["减持", "董事", "监事", "高级管理人员"]},
    {"query": "合同解除后违约金怎么处理", "expect": ["合同", "违约金", "民法典"]},
]


def benchmark(args: argparse.Namespace) -> None:
    cases = BENCHMARK_CASES[: max(1, min(args.max_cases, len(BENCHMARK_CASES)))]
    summaries = []
    hit_top1 = 0
    hit_top5 = 0
    for case in cases:
        ns = argparse.Namespace(
            query=case["query"],
            collection=args.collection,
            top_k=args.top_k,
            vector_top_k=args.vector_top_k,
            keyword_top_k=args.keyword_top_k,
            source_top_k=args.source_top_k,
            rerank_pool=args.rerank_pool,
            per_doc_chunks=args.per_doc_chunks,
            rrf_k=args.rrf_k,
            expansion_limit=args.expansion_limit,
            neighbor_radius=args.neighbor_radius,
            profile=None,
            no_rerank=args.no_rerank,
        )
        result = hybrid_search_result(ns)
        results = result["results"]
        expected = case["expect"]

        def result_text(item: dict) -> str:
            return f"{item.get('source') or ''} {item.get('text') or ''}"

        top1_ok = bool(results and any(token in result_text(results[0]) for token in expected))
        top5_ok = any(any(token in result_text(item) for token in expected) for item in results[:5])
        hit_top1 += int(top1_ok)
        hit_top5 += int(top5_ok)
        summaries.append(
            {
                "query": case["query"],
                "expect": expected,
                "query_type": result["query_type"],
                "top1_ok": top1_ok,
                "top5_ok": top5_ok,
                "top1_source": results[0].get("source") if results else None,
                "top1_chunk": results[0].get("chunk_index") if results else None,
                "top1_rerank_score": results[0].get("rerank_score") if results else None,
                "vector_hits": result["vector_hits"],
                "keyword_hits": result["keyword_hits"],
                "exact_article_hits": result["exact_article_hits"],
                "neighbor_hits": result["neighbor_hits"],
                "source_focused_hits": result["source_focused_hits"],
            }
        )

    print_json(
        {
            "cases": len(cases),
            "top1_hit_rate": hit_top1 / max(len(cases), 1),
            "top5_hit_rate": hit_top5 / max(len(cases), 1),
            "results": summaries,
        }
    )


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        description="Read-only Milvus helper for siq_legal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              legal_milvus_cli.py status
              legal_milvus_cli.py collections
              legal_milvus_cli.py schema
              legal_milvus_cli.py sample --limit 3
              legal_milvus_cli.py search "公司法 独立董事 任期" --top-k 8
              legal_milvus_cli.py hybrid_search "上市公司关联交易披露要求" --top-k 8
              legal_milvus_cli.py benchmark --top-k 5
            """
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status").set_defaults(func=status)
    sub.add_parser("collections").set_defaults(func=collections)

    schema_parser = sub.add_parser("schema")
    schema_parser.add_argument("--collection")
    schema_parser.set_defaults(func=schema)

    sample_parser = sub.add_parser("sample")
    sample_parser.add_argument("--collection")
    sample_parser.add_argument("--limit", type=int, default=3)
    sample_parser.set_defaults(func=sample)

    search_parser = sub.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--collection")
    search_parser.add_argument("--top-k", type=int, default=8)
    search_parser.set_defaults(func=search)

    hybrid_parser = sub.add_parser("hybrid_search")
    hybrid_parser.add_argument("query")
    hybrid_parser.add_argument("--collection")
    hybrid_parser.add_argument("--top-k", type=int, default=8)
    hybrid_parser.add_argument("--vector-top-k", type=int, default=100)
    hybrid_parser.add_argument("--keyword-top-k", type=int, default=30)
    hybrid_parser.add_argument("--source-top-k", type=int, default=12)
    hybrid_parser.add_argument("--rerank-pool", type=int, default=30)
    hybrid_parser.add_argument("--per-doc-chunks", type=int, default=2)
    hybrid_parser.add_argument("--rrf-k", type=int, default=40)
    hybrid_parser.add_argument("--expansion-limit", type=int, default=7)
    hybrid_parser.add_argument("--neighbor-radius", type=int, default=1)
    hybrid_parser.add_argument(
        "--profile",
        choices=["governance", "disclosure", "transaction", "enforcement", "compliance", "law", "default"],
    )
    hybrid_parser.add_argument("--no-rerank", action="store_true")
    hybrid_parser.set_defaults(func=hybrid_search)

    benchmark_parser = sub.add_parser("benchmark")
    benchmark_parser.add_argument("--collection")
    benchmark_parser.add_argument("--max-cases", type=int, default=4)
    benchmark_parser.add_argument("--top-k", type=int, default=3)
    benchmark_parser.add_argument("--vector-top-k", type=int, default=16)
    benchmark_parser.add_argument("--keyword-top-k", type=int, default=4)
    benchmark_parser.add_argument("--source-top-k", type=int, default=4)
    benchmark_parser.add_argument("--rerank-pool", type=int, default=6)
    benchmark_parser.add_argument("--per-doc-chunks", type=int, default=2)
    benchmark_parser.add_argument("--rrf-k", type=int, default=40)
    benchmark_parser.add_argument("--expansion-limit", type=int, default=3)
    benchmark_parser.add_argument("--neighbor-radius", type=int, default=1)
    benchmark_parser.add_argument("--no-rerank", action="store_true")
    benchmark_parser.add_argument("--with-rerank", dest="no_rerank", action="store_false")
    benchmark_parser.set_defaults(func=benchmark, no_rerank=True)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
