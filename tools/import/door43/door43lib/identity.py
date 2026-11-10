"""Module identity and module_info content from a Door43 manifest (design section 6)."""
import importlib.util
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
_OVERRIDES_PY = os.path.join(HERE, '..', '..', 'sword', 'bible', 'apply-module-overrides.py')

_mod = None


def _overrides_module():
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location('apply_module_overrides', os.path.abspath(_OVERRIDES_PY))
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
    return _mod


def deterministic_uuid(identity_key):
    """The rule of apply-module-overrides.py / sword_common.cpp (imported, not copied)."""
    return _overrides_module().deterministic_uuid(identity_key)


def module_uuid(module_type, abbreviation, language):
    return deterministic_uuid(f'{module_type}:{abbreviation.lower()}:{language}')


KIND_INFO = {
    'tw': dict(module_type='dictionary', prefix='TW', english='translationWords'),
    'tn': dict(module_type='commentary', prefix='TN', english='translationNotes'),
    'tq': dict(module_type='quiz', prefix='TQ', english='translationQuestions'),
    'obs': dict(module_type='book', prefix='OBS', english='Open Bible Stories'),
}
LICENSE_SPDX = 'CC-BY-SA-4.0'
LICENSE_URL = 'https://creativecommons.org/licenses/by-sa/4.0/'


def iso639_1(door43_lang):
    """'es-419' -> 'es', 'pt-br' -> 'pt'."""
    return door43_lang.split('-')[0].lower()


def abbreviation(kind, door43_lang):
    return f"{KIND_INFO[kind]['prefix']}-{iso639_1(door43_lang).upper()}"


def file_name(kind, door43_lang):
    return f"{KIND_INFO[kind]['module_type']}_{abbreviation(kind, door43_lang)}.db"


def copyright_line(license_text):
    """The first ``Copyright ©`` line of LICENSE.md (markdown emphasis stripped), else None."""
    for line in (license_text or '').splitlines():
        t = re.sub(r'[*_`]', '', line).strip()
        if re.match(r'(?i)copyright\s*(©|\(c\))', t):
            return t
    return None


def year_from_issued(issued):
    m = re.match(r'(\d{4})', str(issued or ''))
    return int(m.group(1)) if m else 0


def build_module_info(manifest, kind, source, license_text=None, zip_sha256=None):
    """module_info column dict (plus ``metadata`` dict) for a tW/tN/tQ/OBS module.

    ``source`` is a sources.json row. ``manifest`` is the parsed manifest.yaml.
    """
    info = KIND_INFO[kind]
    dc = (manifest or {}).get('dublin_core', {})
    lang = dc.get('language', {}) or {}
    d43 = source.get('door43_lang') or lang.get('identifier') or source['lang']
    code = iso639_1(source.get('lang') or d43)
    abbr = abbreviation(kind, code)
    title = dc.get('title') or info['english']
    cr = copyright_line(license_text)
    owner, repo, tag = source['owner'], source['repo'], source['tag']
    mods = ('Converted from Door43 %s %s to the Bible app module format: Markdown to HTML, links resolved, '
            'verse numbering mapped to KJV, images and translationAcademy links removed' % (repo, tag))
    metadata = {
        'derivative': True,
        'door43_language': d43,
        'content_format': 'html',
        'attribution': cr or '',
        'modifications': mods,
        'notices': 'The licensor does not endorse this module or its use.',
        'door43': {'owner': owner, 'repo': repo, 'tag': tag, 'zip_sha256': zip_sha256,
                   'manifest_version': dc.get('version'), 'sources': dc.get('source', [])},
    }
    cols = {
        'module_uuid': module_uuid(info['module_type'], abbr, code),
        'module_type': info['module_type'],
        'abbreviation': abbr,
        'full_name': f"{title} ({info['english']})",
        'author': dc.get('creator'),
        'publisher': dc.get('publisher'),
        'year_published': year_from_issued(dc.get('issued')),
        'description': dc.get('description'),
        'language_code': code,
        'right_to_left': 1 if lang.get('direction') == 'rtl' else 0,
        'content_version': tag,
        'copyright': cr,
        'license_spdx': LICENSE_SPDX,
        'license_url': LICENSE_URL,
        'source_url': f'https://git.door43.org/{owner}/{repo}/releases/tag/{tag}',
        'versification': 'kjv-english',
        'metadata': metadata,
    }
    return cols
