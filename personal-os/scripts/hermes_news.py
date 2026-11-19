#!/usr/bin/env python3
"""Hermes News: deterministic collection -> AI review -> validated publication."""
from __future__ import annotations
import argparse, hashlib, html, json, logging, os, random, re, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import feedparser, requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from jsonschema import Draft202012Validator

ROOT=Path(__file__).resolve().parents[1]
LOG=logging.getLogger('hermes')
TOPICS={'alert','security','politics','economy','services','environment','other'}
AI_ITEM={"type":"object","additionalProperties":False,"required":["id","category","region","priority","summary","why_it_matters","relevant"],"properties":{"id":{"type":"string"},"category":{"enum":sorted(TOPICS)},"region":{"enum":["peru","world"]},"priority":{"type":"integer","minimum":0,"maximum":100},"summary":{"type":"string","maxLength":420},"why_it_matters":{"type":"string","maxLength":420},"relevant":{"type":"boolean"}}}
AI_SCHEMA={"type":"object","additionalProperties":False,"required":["articles"],"properties":{"articles":{"type":"array","items":AI_ITEM}}}

class PipelineError(RuntimeError): pass

def clean_text(v): return re.sub(r'\s+',' ',BeautifulSoup(v or '', 'html.parser').get_text(' ',strip=True)).strip()
def canonical_url(raw):
    p=urlparse(raw.strip()); q=[(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in {'fbclid','gclid'}]
    return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/') or '/', '', urlencode(q), ''))
def article_id(url): return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:20]
def iso_date(entry):
    raw=entry.get('published') or entry.get('updated') or ''
    try:
        d=dateparser.parse(raw); d=d if d.tzinfo else d.replace(tzinfo=timezone.utc); return d.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    except Exception: return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def read_json(path, default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError: return default

def collect(config, session):
    out=[]; failures=[]
    headers={'User-Agent':'HermesNews/2.0 (+https://d34thwish.github.io/personal-os/news.html)'}
    for feed in config['feeds']:
        try:
            r=session.get(feed['url'],headers=headers,timeout=(10,25)); r.raise_for_status(); parsed=feedparser.parse(r.content)
            if parsed.bozo and not parsed.entries: raise PipelineError(str(parsed.bozo_exception))
            for e in parsed.entries[:config.get('max_items_per_feed',40)]:
                url=canonical_url(e.get('link',''))
                if not url.startswith(('http://','https://')): continue
                title=clean_text(e.get('title','')); desc=clean_text(e.get('summary','') or e.get('description',''))
                if not title: continue
                out.append({'id':article_id(url),'title':title,'url':url,'source':feed['name'],'published_at':iso_date(e),'raw_summary':desc[:1200],'region_hint':feed.get('region','world')})
        except Exception as exc:
            failures.append({'source':feed['name'],'error':str(exc)[:240]}); LOG.error('Feed failed: %s: %s',feed['name'],exc)
    if not out: raise PipelineError('All feeds failed or returned no usable articles')
    by_url={x['url']:x for x in out}; by_title={}
    for x in by_url.values():
        key=re.sub(r'\W+','',x['title'].lower())[:140]; by_title.setdefault(key,x)
    return list(by_title.values()), failures

def ai_review(items, config):
    key=os.getenv('GROQ_API_KEY')
    if not key: raise PipelineError('GROQ_API_KEY is not configured')
    from groq import Groq
    client=Groq(api_key=key,timeout=45,max_retries=0)
    payload=[{k:x[k] for k in ('id','title','source','published_at','raw_summary','region_hint')} for x in items]
    prompt="Review this news batch for a personal briefing in Spanish for a reader in Peru. Preserve IDs. Classify only; do not invent facts. summary and why_it_matters must be concise Spanish. Region peru only for primarily Peruvian/local events. Priority 0-100. Return JSON only with top-level key articles and exactly one result per input ID."
    last=None
    for attempt in range(4):
        try:
            res=client.chat.completions.create(model=config['ai']['model'],temperature=0,response_format={'type':'json_object'},messages=[{'role':'system','content':prompt},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}])
            text=res.choices[0].message.content
            if not text: raise PipelineError('AI returned empty content')
            data=json.loads(text); errors=list(Draft202012Validator(AI_SCHEMA).iter_errors(data))
            if errors: raise PipelineError('Invalid AI schema: '+errors[0].message)
            ids=[x['id'] for x in data['articles']]
            if len(ids)!=len(payload) or set(ids)!={x['id'] for x in payload}: raise PipelineError('AI response IDs do not exactly match request')
            return data['articles']
        except Exception as exc:
            last=exc
            if attempt==3: break
            delay=min(30,2**attempt+random.random()); LOG.warning('AI attempt %d failed; retrying in %.1fs: %s',attempt+1,delay,exc); time.sleep(delay)
    raise PipelineError(f'AI review failed after retries: {last}')

def merge_review(items, reviews):
    rev={x['id']:x for x in reviews}; out=[]
    for x in items:
        r=rev[x['id']]
        if not r['relevant']: continue
        out.append({'id':x['id'],'title':x['title'],'url':x['url'],'source':x['source'],'published_at':x['published_at'],'category':r['category'],'region':r['region'],'priority':r['priority'],'summary':r['summary'].strip() or x['raw_summary'][:420],'why_it_matters':r['why_it_matters'].strip()})
    return out

def validate_dataset(data):
    if data.get('schema_version')!=1 or not isinstance(data.get('articles'),list): raise PipelineError('Invalid dataset envelope')
    seen=set()
    for a in data['articles']:
        required={'id','title','url','source','published_at','category','region','priority','summary','why_it_matters'}
        if not required<=a.keys(): raise PipelineError(f'Missing article fields: {a.get("id")}')
        if a['id'] in seen: raise PipelineError(f'Duplicate ID: {a["id"]}')
        seen.add(a['id'])
        if urlparse(a['url']).scheme not in {'http','https'} or not urlparse(a['url']).netloc: raise PipelineError('Invalid URL')
        if a['category'] not in TOPICS or a['region'] not in {'peru','world'} or not 0<=a['priority']<=100: raise PipelineError('Invalid classification')
        dateparser.isoparse(a['published_at'])

def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.tmp-',dir=path.parent,text=True)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2,sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--config',default='config/news_feeds.json'); ap.add_argument('--output',default='data/news.json'); ap.add_argument('--offline-fixture'); ap.add_argument('--skip-ai',action='store_true'); args=ap.parse_args(argv)
    logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(levelname)s %(message)s')
    config=read_json(ROOT/args.config,None)
    if not config: raise PipelineError('Missing or invalid configuration')
    old=read_json(ROOT/args.output,{'schema_version':1,'articles':[]})
    if args.offline_fixture:
        items=read_json(ROOT/args.offline_fixture,[]); failures=[]
    else: items,failures=collect(config,requests.Session())
    old_map={a['id']:a for a in old.get('articles',[])}
    new=[x for x in items if x['id'] not in old_map]
    if not new:
        LOG.info('No new articles; existing dataset preserved'); return 0
    if args.skip_ai:
        if not args.offline_fixture: raise PipelineError('--skip-ai is restricted to fixture testing')
        reviews=[{'id':x['id'],'category':'other','region':x.get('region_hint','world'),'priority':50,'summary':x.get('raw_summary','')[:420],'why_it_matters':'','relevant':True} for x in new]
    else:
        reviews=[]; size=config['ai'].get('max_batch_size',20)
        for i in range(0,len(new),size): reviews.extend(ai_review(new[i:i+size],config))
    combined=list(old_map.values())+merge_review(new,reviews)
    cutoff=datetime.now(timezone.utc)-timedelta(days=config.get('retention_days',30))
    combined=[a for a in combined if dateparser.isoparse(a['published_at'])>=cutoff]
    combined.sort(key=lambda a:(a['priority'],a['published_at']),reverse=True)
    data={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'source_failures':failures,'articles':combined}
    validate_dataset(data)
    candidate=json.dumps(data,ensure_ascii=False,sort_keys=True); previous=json.dumps(old,ensure_ascii=False,sort_keys=True)
    if candidate==previous: LOG.info('No meaningful change'); return 0
    atomic_json(ROOT/args.output,data); LOG.info('Published %d articles (%d new)',len(combined),len(new)); return 0
if __name__=='__main__':
    try: raise SystemExit(main())
    except PipelineError as exc: LOG.error('%s',exc); raise SystemExit(2)
