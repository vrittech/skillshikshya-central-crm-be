import logging
import re

import requests

from django.conf import settings

from apps.course.models import Course, CourseCategory

logger = logging.getLogger(__name__)

# Public Skill Shikshya course catalog API
PUBLIC_COURSE_API_URL = getattr(
    settings, 'PUBLIC_COURSE_API_URL', 'https://base.skillshikshya.com/api/course/'
)

STOPWORDS = {
    'course', 'courses', 'diploma', 'training', 'program', 'programme',
    'masterclass', 'master', 'class', 'job', 'ready', 'in', 'nepal',
    'with', 'and', 'the', 'for', 'of', 'to', 'on', 'online', 'professional',
    'complete', 'basic', 'advanced', 'foundation', 'bootcamp',
    'skill', 'shikshya', 'trial', 'free', 'ne', 'an', 'ai',
    'integrated', 'day', 'days', 'full', 'stack', 'web', 'development',
    'designing', 'design', 'graphics', 'graphic',
}

MATCH_THRESHOLD = 0.6

# In-process cache for the public catalog
_CACHE = {}


def _normalize(name):
    text = (name or '').lower()
    text = re.sub(r'[^a-z0-9 ]+', ' ', text)
    tokens = {t for t in text.split() if t and t not in STOPWORDS}
    return tokens


def _match_score(query_tokens, candidate_tokens):
    if not query_tokens or not candidate_tokens:
        return 0.0
    intersection = query_tokens & candidate_tokens
    if not intersection:
        return 0.0
    completeness = len(intersection) / len(query_tokens)
    union = query_tokens | candidate_tokens
    coverage = len(intersection) / len(union)
    return 0.7 * completeness + 0.3 * coverage


def _fetch_public_courses():
    cached = _CACHE.get('public')
    if cached is not None:
        return cached or None

    results = []
    url = PUBLIC_COURSE_API_URL
    try:
        while url:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get('results', []) or [])
            url = data.get('next')
    except Exception as exc:
        logger.warning('Public course API unavailable (%s); falling back to local table', exc)
        _CACHE['public'] = None
        return None

    _CACHE['public'] = results
    return results


def _upsert_from_public(item, requested_name, full_amount):
    title = (item.get('title') or requested_name).strip()
    if not title:
        return None, False

    public_amount = item.get('amount')
    if public_amount is None or public_amount == '':
        amount = full_amount if full_amount is not None else '0.00'
    else:
        amount = public_amount

    course, created = Course.objects.get_or_create(
        name=title,
        defaults={'full_amount': amount},
    )
    if not created and course.full_amount is None:
        course.full_amount = amount
        course.save(update_fields=['full_amount'])

    for cat in item.get('categories') or []:
        cat_name = cat.get('name') if isinstance(cat, dict) else cat
        if not cat_name:
            continue
        category, _ = CourseCategory.objects.get_or_create(name=str(cat_name).strip())
        course.categories.add(category)

    return course, created


def match_or_create_course(raw_name, full_amount=None, create_unknown=True):
    """
    Resolve a plain-text inbound course name to a Course without duplicating.

    Strategy:
      1. Always reuse an existing Course with the exact same name.
      2. Match against the public SkillShikshya catalog by normalized tokens.
         On a hit, get-or-create the Course using the public amount (or 0.00)
         and attach its categories.
      3. Otherwise get-or-create a new Course with amount 0.00, unless
         create_unknown is False: then (None, False).
    """
    name = (raw_name or '').strip()
    if not name:
        return None, False

    # 1. Exact-name dedup: reuse an existing course if present.
    existing = Course.objects.filter(name__iexact=name).first()
    if existing:
        return existing, False

    query_tokens = _normalize(name)

    # 2. Public catalog match (preferred)
    public = _fetch_public_courses()
    if public is not None:
        best = None
        best_score = 0.0
        for item in public:
            title = (item.get('title') or '').strip()
            if not title:
                continue
            if title.lower() == name.lower():
                best = item
                best_score = 1.0
                break
            candidate_tokens = _normalize(title)
            score = _match_score(query_tokens, candidate_tokens)
            if score > best_score:
                best_score = score
                best = item
        if best and best_score >= MATCH_THRESHOLD:
            return _upsert_from_public(best, name, full_amount)

    # 3. Not in API: get-or-create with amount 0.00 (no duplicates).
    if not create_unknown:
        return None, False
    course, created = Course.objects.get_or_create(
        name=name,
        defaults={'full_amount': '0.00'},
    )
    return course, created