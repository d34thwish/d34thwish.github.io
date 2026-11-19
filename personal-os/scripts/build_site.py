#!/usr/bin/env python3
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'_site'
if OUT.exists(): shutil.rmtree(OUT)
OUT.mkdir()
# Preserve existing public pages/assets when present, without publishing automation internals.
for p in ROOT.iterdir():
    if p.name in {'.git','.github','scripts','config','tests','data','_site','var'} or p.name.startswith('.') or p.name in {'requirements.txt','README.md'}: continue
    if p.is_file() and p.suffix.lower() in {'.html','.css','.js','.png','.jpg','.jpeg','.webp','.svg','.ico','.txt'}: shutil.copy2(p,OUT/p.name)
    elif p.is_dir() and p.name in {'assets','images','css','js'}: shutil.copytree(p,OUT/p.name)
(OUT/'data').mkdir(); shutil.copy2(ROOT/'data/news.json',OUT/'data/news.json')
if not (OUT/'news.html').exists(): raise SystemExit('news.html missing from deployment')
(OUT/'.nojekyll').write_text('',encoding='utf-8')
