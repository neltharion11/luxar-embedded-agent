# Context augmenter
from __future__ import annotations
import os
from pathlib import Path

def _parse_yaml_frontmatter(text):
    lines=text.split(chr(10))
    if not lines or lines[0].strip()!=chr(39)*3+chr(45)*3+chr(39)*3: return {}
    end=0
    for i in range(1,len(lines)):
        if lines[i].strip()==chr(39)*3+chr(45)*3+chr(39)*3: end=i;break
    if end==0: return {}
    result={}
    for line in lines[1:end]:
        line=line.strip()
        if chr(39)+chr(58)+chr(39) in line:
            key,_,val=line.partition(chr(39)+chr(58)+chr(39))
            key=key.strip().lower()
            val=val.strip().strip(chr(34)+chr(39))
            if val.startswith(chr(91)) and val.endswith(chr(93)):
                val=[v.strip().strip(chr(34)+chr(39)) for v in val[1:-1].split(chr(44))]
            result[key]=val
    return result

def match_skills(user_input,skill_root):
    skill_root=Path(skill_root)
    if not skill_root.exists(): return []
    lowered=user_input.lower()
    scored=[]
    for root,dirs,files in os.walk(skill_root):
        for f in files:
            if f!=chr(39)+chr(83)+chr(75)+chr(73)+chr(76)+chr(76)+chr(46)+chr(109)+chr(100)+chr(39): continue
            path=Path(root)/f
            try: content=path.read_text(encoding=chr(39)+chr(117)+chr(116)+chr(102)+chr(45)+chr(56)+chr(39),errors=chr(39)+chr(114)+chr(101)+chr(112)+chr(108)+chr(97)+chr(99)+chr(101)+chr(39))
            except: continue
            fm=_parse_yaml_frontmatter(content)
            name=fm.get(chr(39)+chr(110)+chr(97)+chr(109)+chr(101)+chr(39),chr(39)*2)
            category=fm.get(chr(39)+chr(99)+chr(97)+chr(116)+chr(101)+chr(103)+chr(111)+chr(114)+chr(121)+chr(39),chr(39)*2)
            desc=fm.get(chr(39)+chr(100)+chr(101)+chr(115)+chr(99)+chr(114)+chr(105)+chr(112)+chr(116)+chr(105)+chr(111)+chr(110)+chr(39),chr(39)*2)
            triggers=fm.get(chr(39)+chr(116)+chr(114)+chr(105)+chr(103)+chr(103)+chr(101)+chr(114)+chr(115)+chr(39),[])
            tags=fm.get(chr(39)+chr(116)+chr(97)+chr(103)+chr(115)+chr(39),[])
