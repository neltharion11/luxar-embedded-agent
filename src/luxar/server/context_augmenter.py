# Context augmenter
from __future__ import annotations
import os
from pathlib import Path

def _parse_yaml_frontmatter(text):
    lines = text.split(chr(10))
    if not lines or lines[0].strip() != chr(45)*3:
        return {}
    end = 0
    for i in range(1, len(lines)):
        if lines[i].strip() == chr(45)*3:
            end = i
            break
    if end == 0:
        return {}
    result = {}
    current_key = None
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(chr(45) + chr(32)):
            item = stripped[2:].strip().strip(chr(34) + chr(39))
            if current_key and item:
                if current_key not in result or not isinstance(result[current_key], list):
                    result[current_key] = []
                result[current_key].append(item)
            continue
        if chr(58) in stripped:
            key, _, val = stripped.partition(chr(58))
            key = key.strip().lower()
            val = val.strip().strip(chr(34) + chr(39))
            if val:
                if val.startswith(chr(91)) and val.endswith(chr(93)):
                    val = [v.strip().strip(chr(34) + chr(39)) for v in val[1:-1].split(chr(44))]
                result[key] = val
            else:
                current_key = key
                result[key] = []
            continue
        if current_key:
            item = stripped.strip().strip(chr(34) + chr(39))
            if item:
                result[current_key].append(item)
    return result

def match_skills(user_input, skill_root):
    skill_root = Path(skill_root)
    if not skill_root.exists():
        return []
    lowered = user_input.lower()
    scored = []
    for root, dirs, files in os.walk(skill_root):
        for f in files:
            if f != 'SKILL.md':
                continue
            path = Path(root) / f
            try:
                content = path.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            fm = _parse_yaml_frontmatter(content)
            if not fm:
                continue
            name = fm.get('name', '')
            category = fm.get('category', '')
            desc = fm.get('description', '')
            triggers = fm.get('triggers', [])
            tags = fm.get('tags', [])
            if isinstance(triggers, str):
                triggers = [triggers]
            if isinstance(tags, str):
                tags = [tags]
            score = 0
            for t in triggers:
                t_lower = str(t).lower()
                if len(t_lower) >= 3 and t_lower in lowered:
                    score += 3
                elif any(w in lowered for w in t_lower.split() if len(w) >= 3):
                    score += 1
            for t in tags:
                if str(t).lower() in lowered:
                    score += 2
            if name and str(name).lower() in lowered:
                score += 2
            if score > 0:
                brief = ''
                for line in content.split(chr(10)):
                    line = line.strip()
                    if line and not line.startswith('#') and not line.startswith('---'):
                        brief = line[:120]
                        break
                scored.append((score, {
                    'name': name or Path(root).name,
                    'category': category,
                    'description': brief or desc or '',
                    'type': 'skill',
                    'path': str(Path(root).relative_to(skill_root)),
                }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s[1] for s in scored[:5]]


def match_drivers(user_input, library):
    try:
        results = library.search_drivers(keyword=user_input, limit=3)
        return [{'name': r.name, 'chip': r.chip, 'protocol': r.protocol, 'device': r.device, 'vendor': r.vendor, 'type': 'driver'} for r in results]
    except Exception:
        return []


def build_resource_hint(skills, drivers):
    if not skills and not drivers:
        return ''
    lines = ['', 'Available resources matching your request:']
    for s in skills:
        name = s.get('name', '?')
        cat = s.get('category', '')
        desc = s.get('description', '')
        parts = ['[skill/', cat, '] ', name]
        if desc:
            parts.append(' - ' + desc[:80])
        parts.append(' (call: skill_execute(name="' + name + '"))')
        lines.append(''.join(parts))
    for d in drivers:
        name = d.get('name', '?')
        chip = d.get('chip', '')
        proto = d.get('protocol', '')
        parts = ['[driver] ', name]
        if chip:
            parts.append(' (' + chip)
            if proto:
                parts.append(' ' + proto)
            parts.append(')')
        parts.append(' (call: search_driver(keyword="' + name + '"))')
        lines.append(''.join(parts))
    return chr(10).join(lines)
