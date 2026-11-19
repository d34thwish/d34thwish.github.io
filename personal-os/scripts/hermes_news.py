#!/usr/bin/env python3
"""Hermes News: deterministic collection -> resilient AI review -> atomic publication."""
from __future__ import annotations
import argparse, hashlib, json, logging, os, random, re, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import feedparser, requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
from dateutil import parser as dateparser
from jsonschema import Draft202012Validator
import warnings
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
ROOT=Path(__file__).resolve().parents[1]; LOG=logging.getLogger("hermes")
TOPICS={"alert","security","politics","economy","services","environment","other"}
AI_ITEM={"type":"object","additionalProperties":False,"required":["id","category","region","priority","summary","why_it_matters","relevant"],"properties":{"id":{"type":"string"},"category":{"enum":sorted(TOPICS)},"region":{"enum":["peru","world"]},"priority":{"type":"integer","minimum":0,"maximum":100},"summary":{"type":"string","maxLength":420},"why_it_matters":{"type":"string","maxLength":420},"relevant":{"type":"boolean"}}}
AI_SCHEMA={"type":"object","additionalProperties":False,"required":["articles"],"properties":{"articles":{"type":"array","items":AI_ITEM}}}
class PipelineError(RuntimeError): pass
class ProviderError(RuntimeError):
 def __init__(self,msg,status=None,retry_after=None,permanent=False): super().__init__(msg); self.status=status; self.retry_after=retry_after; self.permanent=permanent

def clean_text(v): return re.sub(r"\s+"," ",BeautifulSoup(v or "","html.parser").get_text(" ",strip=True)).strip()
def canonical_url(raw):
 p=urlparse(raw.strip()); q=[(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k.lower() not in {"fbclid","gclid"}]
 return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/") or "/","",urlencode(q),""))
def article_id(url): return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:20]
def iso_date(entry):
 try:
  d=dateparser.parse(entry.get("published") or entry.get("updated") or ""); d=d if d.tzinfo else d.replace(tzinfo=timezone.utc); return d.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
 except Exception: return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def read_json(path,default):
 try: return json.loads(path.read_text(encoding="utf-8"))
 except FileNotFoundError: return default

def collect(config,session):
 out=[]; failures=[]; headers={"User-Agent":"HermesNews/3.0 (+https://d34thwish.github.io/personal-os/news.html)"}
 for feed in config["feeds"]:
  try:
   r=session.get(feed["url"],headers=headers,timeout=(10,25)); r.raise_for_status(); parsed=feedparser.parse(r.content)
   if parsed.bozo and not parsed.entries: raise PipelineError(str(parsed.bozo_exception))
   for e in parsed.entries[:config.get("max_items_per_feed",40)]:
    url=canonical_url(e.get("link","")); title=clean_text(e.get("title","")); desc=clean_text(e.get("summary","") or e.get("description",""))
    if url.startswith(("http://","https://")) and title: out.append({"id":article_id(url),"title":title,"url":url,"source":feed["name"],"published_at":iso_date(e),"raw_summary":desc[:900],"region_hint":feed.get("region","world")})
  except Exception as exc: failures.append({"source":feed["name"],"error":str(exc)[:240]}); LOG.error("Feed failed: %s: %s",feed["name"],exc)
 if not out: raise PipelineError("All feeds failed or returned no usable articles")
 unique={x["url"]:x for x in out}; by_title={}
 for x in unique.values(): by_title.setdefault(re.sub(r"\W+","",x["title"].lower())[:140],x)
 return list(by_title.values()),failures

def schema_prompt(payload,repair=None):
 example={"articles":[{"id":"COPY_INPUT_ID","category":"other","region":"world","priority":50,"summary":"Resumen breve en español.","why_it_matters":"Importancia breve en español.","relevant":True}]}
 base="Review public news for a reader in Peru. Return JSON only. Preserve every input ID exactly once. Do not invent facts. category must be one of alert, security, politics, economy, services, environment, other. region is peru only for primarily Peruvian/local events, otherwise world. priority is integer 0-100. summary and why_it_matters are concise Spanish strings. relevant is boolean. Exact shape example: "+json.dumps(example,ensure_ascii=False)
 if repair: base+=" Your previous response was invalid: "+repair+". Correct it without omitting fields."
 return base+"\nINPUT:\n"+json.dumps(payload,ensure_ascii=False)

def retry_after(resp):
 h=resp.headers.get("retry-after")
 if h:
  try: return min(180,max(1,float(h)))
  except ValueError: pass
 m=re.search(r"try again in\s+([0-9.]+)s",resp.text,re.I)
 return min(180,float(m.group(1))) if m else None

def check_response(resp):
 if resp.ok: return
 status=resp.status_code; permanent=status in {400,401,403,404,422}; raise ProviderError(f"HTTP {status}: {resp.text[:300]}",status,retry_after(resp),permanent)

def call_provider(provider,prompt,session):
 name=provider["name"]; model=provider["model"]; timeout=provider.get("timeout_seconds",60)
 if name=="gemini":
  key=os.getenv("GEMINI_API_KEY");
  if not key: raise ProviderError("secret not configured",permanent=True)
  url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
  body={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":0,"responseMimeType":"application/json","responseJsonSchema":AI_SCHEMA,"maxOutputTokens":4096}}
  r=session.post(url,json=body,timeout=timeout); check_response(r); return r.json()["candidates"][0]["content"]["parts"][0]["text"]
 if name=="cloudflare":
  aid=os.getenv("CLOUDFLARE_ACCOUNT_ID"); token=os.getenv("CLOUDFLARE_API_TOKEN")
  if not aid or not token: raise ProviderError("secrets not configured",permanent=True)
  url=f"https://api.cloudflare.com/client/v4/accounts/{aid}/ai/run/{model}"
  r=session.post(url,headers={"Authorization":f"Bearer {token}"},json={"messages":[{"role":"user","content":prompt}],"temperature":0,"max_tokens":4096,"response_format":{"type":"json_schema","json_schema":AI_SCHEMA}},timeout=timeout); check_response(r); data=r.json(); result=data.get("result",{}); return result.get("response") or result.get("result") or ""
 keys={"groq":"GROQ_API_KEY","openrouter":"OPENROUTER_API_KEY"}; key=os.getenv(keys[name])
 if not key: raise ProviderError("secret not configured",permanent=True)
 url={"groq":"https://api.groq.com/openai/v1/chat/completions","openrouter":"https://openrouter.ai/api/v1/chat/completions"}[name]
 headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"}
 if name=="openrouter": headers.update({"HTTP-Referer":"https://d34thwish.github.io/personal-os/news.html","X-Title":"Hermes News"})
 body={"model":model,"temperature":0,"max_tokens":4096,"messages":[{"role":"user","content":prompt}],"response_format":{"type":"json_schema","json_schema":{"name":"hermes_review","strict":True,"schema":AI_SCHEMA}}}
 r=session.post(url,headers=headers,json=body,timeout=timeout); check_response(r); return r.json()["choices"][0]["message"]["content"]

def validate_ai(text,payload):
 if not text: raise PipelineError("AI returned empty content")
 data=json.loads(text); errors=list(Draft202012Validator(AI_SCHEMA).iter_errors(data))
 if errors: raise PipelineError("Invalid AI schema: "+errors[0].message)
 ids=[x["id"] for x in data["articles"]]; expected={x["id"] for x in payload}
 if len(ids)!=len(payload) or set(ids)!=expected: raise PipelineError("AI response IDs do not exactly match request")
 return data["articles"]

def ai_review(items,config,session=None,sleep=time.sleep):
 session=session or requests.Session(); payload=[{k:x[k] for k in ("id","title","source","published_at","raw_summary","region_hint")} for x in items]
 providers=[p for p in config["ai"]["providers"] if p.get("enabled",True)]; failures=[]
 for p in providers:
  repair=None
  for schema_try in range(2):
   try:
    text=call_provider(p,schema_prompt(payload,repair),session); return validate_ai(text,payload)
   except PipelineError as exc:
    if schema_try==0: repair=str(exc); LOG.warning("%s schema repair attempt: %s",p["name"],exc); continue
    failures.append(f"{p['name']}: {exc}"); break
   except ProviderError as exc:
    if exc.permanent: LOG.warning("Skipping %s: %s",p["name"],exc); failures.append(f"{p['name']}: {exc}"); break
    waits=p.get("max_transient_retries",1)
    recovered=False
    for attempt in range(waits):
     delay=exc.retry_after or min(60,2**attempt+random.random()); LOG.warning("%s temporary failure; waiting %.1fs: %s",p["name"],delay,exc); sleep(delay)
     try: return validate_ai(call_provider(p,schema_prompt(payload,repair),session),payload)
     except ProviderError as nxt: exc=nxt
     except PipelineError as nxt: repair=str(nxt); break
    if recovered: break
    failures.append(f"{p['name']}: {exc}"); break
   except (requests.RequestException,KeyError,ValueError,json.JSONDecodeError) as exc: failures.append(f"{p['name']}: {exc}"); LOG.warning("Provider %s failed: %s",p["name"],exc); break
 raise PipelineError("All configured AI providers failed: "+" | ".join(failures))

def merge_review(items,reviews):
 rev={x["id"]:x for x in reviews}; out=[]
 for x in items:
  r=rev[x["id"]]
  if r["relevant"]: out.append({"id":x["id"],"title":x["title"],"url":x["url"],"source":x["source"],"published_at":x["published_at"],"category":r["category"],"region":r["region"],"priority":r["priority"],"summary":r["summary"].strip() or x["raw_summary"][:420],"why_it_matters":r["why_it_matters"].strip()})
 return out

def validate_dataset(data):
 if data.get("schema_version")!=1 or not isinstance(data.get("articles"),list): raise PipelineError("Invalid dataset envelope")
 seen=set()
 for a in data["articles"]:
  required={"id","title","url","source","published_at","category","region","priority","summary","why_it_matters"}
  if not required<=a.keys(): raise PipelineError(f"Missing article fields: {a.get('id')}")
  if a["id"] in seen: raise PipelineError(f"Duplicate ID: {a['id']}")
  seen.add(a["id"]); p=urlparse(a["url"])
  if p.scheme not in {"http","https"} or not p.netloc: raise PipelineError("Invalid URL")
  if a["category"] not in TOPICS or a["region"] not in {"peru","world"} or not 0<=a["priority"]<=100: raise PipelineError("Invalid classification")
  dateparser.isoparse(a["published_at"])
def atomic_json(path,data):
 path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=".tmp-",dir=path.parent,text=True)
 try:
  with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2,sort_keys=True); f.write("\n"); f.flush(); os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp): os.unlink(tmp)
def main(argv=None):
 ap=argparse.ArgumentParser(); ap.add_argument("--config",default="config/news_feeds.json"); ap.add_argument("--output",default="data/news.json"); ap.add_argument("--offline-fixture"); ap.add_argument("--skip-ai",action="store_true"); args=ap.parse_args(argv)
 logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),format="%(levelname)s %(message)s")
 config=read_json(ROOT/args.config,None)
 if not config: raise PipelineError("Missing or invalid configuration")
 old=read_json(ROOT/args.output,{"schema_version":1,"articles":[]}); items,failures=(read_json(ROOT/args.offline_fixture,[]),[]) if args.offline_fixture else collect(config,requests.Session())
 old_map={a["id"]:a for a in old.get("articles",[])}; new=[x for x in items if x["id"] not in old_map]
 if not new: LOG.info("No new articles; existing dataset preserved"); return 0
 if args.skip_ai:
  if not args.offline_fixture: raise PipelineError("--skip-ai is restricted to fixture testing")
  reviews=[{"id":x["id"],"category":"other","region":x.get("region_hint","world"),"priority":50,"summary":x.get("raw_summary","")[:420],"why_it_matters":"","relevant":True} for x in new]
 else:
  reviews=[]; size=config["ai"].get("max_batch_size",5)
  for i in range(0,len(new),size): reviews.extend(ai_review(new[i:i+size],config))
 combined=list(old_map.values())+merge_review(new,reviews); cutoff=datetime.now(timezone.utc)-timedelta(days=config.get("retention_days",30)); combined=[a for a in combined if dateparser.isoparse(a["published_at"])>=cutoff]; combined.sort(key=lambda a:(a["priority"],a["published_at"]),reverse=True)
 data={"schema_version":1,"generated_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"source_failures":failures,"articles":combined}; validate_dataset(data); atomic_json(ROOT/args.output,data); LOG.info("Published %d articles (%d new)",len(combined),len(new)); return 0
if __name__=="__main__":
 try: raise SystemExit(main())
 except PipelineError as exc: LOG.error("%s",exc); raise SystemExit(2)
