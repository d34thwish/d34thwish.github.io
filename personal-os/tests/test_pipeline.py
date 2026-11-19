import json, tempfile, unittest
from pathlib import Path
from scripts.hermes_news import canonical_url, article_id, validate_dataset, PipelineError
class Tests(unittest.TestCase):
 def test_canonical(self): self.assertEqual(canonical_url('HTTPS://Example.com/a/?utm_source=x&b=2'),'https://example.com/a?b=2')
 def test_id_stable(self): self.assertEqual(article_id('https://x.test/a?utm_source=z'),article_id('https://x.test/a'))
 def test_valid(self): validate_dataset({'schema_version':1,'articles':[{'id':'x','title':'t','url':'https://x.test/a','source':'s','published_at':'2026-11-19T12:00:00Z','category':'other','region':'world','priority':1,'summary':'s','why_it_matters':''}]})
 def test_duplicate_rejected(self):
  a={'id':'x','title':'t','url':'https://x.test/a','source':'s','published_at':'2026-11-19T12:00:00Z','category':'other','region':'world','priority':1,'summary':'s','why_it_matters':''}
  with self.assertRaises(PipelineError): validate_dataset({'schema_version':1,'articles':[a,a]})
if __name__=='__main__': unittest.main()
