import json, os, unittest
from unittest.mock import patch
from scripts.hermes_news import (PipelineError, ProviderError, ai_review, article_id,
 canonical_url, retry_after, validate_ai, validate_dataset)

ARTICLE={"id":"abc","title":"Title","source":"Source","published_at":"2026-11-19T12:00:00Z","raw_summary":"Summary","region_hint":"world"}
REVIEW={"articles":[{"id":"abc","category":"other","region":"world","priority":50,"summary":"Resumen","why_it_matters":"Importa","relevant":True}]}
CFG={"ai":{"providers":[{"name":"gemini","model":"x"},{"name":"groq","model":"y"}]}}
class Response:
 def __init__(self,status,text="",headers=None): self.status_code=status; self.text=text; self.headers=headers or {}; self.ok=200<=status<300
class Tests(unittest.TestCase):
 def test_canonical(self): self.assertEqual(canonical_url('HTTPS://Example.com/a/?utm_source=x&b=2'),'https://example.com/a?b=2')
 def test_id_stable(self): self.assertEqual(article_id('https://x.test/a?utm_source=z'),article_id('https://x.test/a'))
 def test_valid(self): validate_dataset({'schema_version':1,'articles':[{'id':'x','title':'t','url':'https://x.test/a','source':'s','published_at':'2026-11-19T12:00:00Z','category':'other','region':'world','priority':1,'summary':'s','why_it_matters':''}]})
 def test_duplicate_rejected(self):
  a={'id':'x','title':'t','url':'https://x.test/a','source':'s','published_at':'2026-11-19T12:00:00Z','category':'other','region':'world','priority':1,'summary':'s','why_it_matters':''}
  with self.assertRaises(PipelineError): validate_dataset({'schema_version':1,'articles':[a,a]})
 def test_accepts_decoded_dict(self):
  got=validate_ai(REVIEW,[ARTICLE])
  self.assertEqual(got[0]["id"],"abc")
 def test_rejects_unsupported_response_type(self):
  with self.assertRaises(PipelineError): validate_ai([REVIEW],[ARTICLE])
 def test_schema_rejects_missing_category(self):
  bad={"articles":[{"id":"abc","region":"world","priority":1,"summary":"s","why_it_matters":"w","relevant":True}]}
  with self.assertRaises(PipelineError): validate_ai(json.dumps(bad),[ARTICLE])
 def test_retry_after_header(self): self.assertEqual(retry_after(Response(429,headers={'retry-after':'41'})),41)
 def test_retry_after_message(self): self.assertAlmostEqual(retry_after(Response(429,'Please try again in 15.5s')),15.5)
 @patch('scripts.hermes_news.call_provider')
 def test_missing_secret_provider_falls_back(self,call):
  call.side_effect=[ProviderError('secret not configured',permanent=True),json.dumps(REVIEW)]
  got=ai_review([ARTICLE],CFG,sleep=lambda _:None)
  self.assertEqual(got[0]['category'],'other'); self.assertEqual(call.call_count,2)
 @patch('scripts.hermes_news.call_provider')
 def test_one_schema_repair(self,call):
  bad={"articles":[{"id":"abc","region":"world","priority":1,"summary":"s","why_it_matters":"w","relevant":True}]}
  call.side_effect=[json.dumps(bad),json.dumps(REVIEW)]
  got=ai_review([ARTICLE],CFG,sleep=lambda _:None)
  self.assertEqual(got[0]['id'],'abc'); self.assertEqual(call.call_count,2)
if __name__=='__main__': unittest.main()
