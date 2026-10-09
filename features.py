"""Local answer helpers. Sensor snapshots and model text are data, not commands."""
import datetime
import json
import math
import re
import time


def structured(result):
    try:
        raw = result.strip()
        if raw.startswith('```') and raw.endswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError, IndexError):
        return {}


def recipe_data(result):
    recipe = structured(result).get('recipe')
    if not isinstance(recipe, dict):
        return None
    steps, ingredients = recipe.get('steps'), recipe.get('ingredients')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 80:
        return None
    if not isinstance(ingredients, list) or not 1 <= len(ingredients) <= 100:
        return None
    if any(not isinstance(x, str) or not x.strip() or len(x) > 2000 for x in steps + ingredients):
        return None
    return {'title': str(recipe.get('title', 'Recipe'))[:120], 'servings': str(recipe.get('servings', 'not specified'))[:80],
            'ingredients': ingredients, 'steps': steps, 'source': str(recipe.get('source', 'Source not provided'))[:1000]}


def attention_report(snapshot, previous=None, known_offline=(), now=None):
    now = time.time() if now is None else now
    try:
        sampled = datetime.datetime.fromisoformat(snapshot['sampled_at'].replace('Z', '+00:00')).timestamp()
        if snapshot.get('error') or snapshot.get('alert_count', 0) < 0 or not -60 <= now - sampled <= 900:
            raise ValueError('Unavailable or stale')
        for key in ('maintenance', 'low_batteries', 'unavailable', 'network'):
            if not isinstance(snapshot.get(key), list):
                raise ValueError('Incomplete report')
    except (KeyError, ValueError, TypeError):
        return {'title': 'Attention unavailable', 'speech': 'Current monitoring is unavailable or stale. I cannot tell what changed.',
                'display': 'Current monitoring is unavailable or stale. No healthy status inferred and no baseline updated.'}, None
    alerts = {}
    def add(category, row, text):
        entity = row.get('entity_id')
        if isinstance(entity, str) and entity not in known_offline:
            alerts[category + ':' + entity] = {'text': text[:500], 'state': str(row.get('state', 'unknown')),
                                               'updated': str(row.get('updated', 'not supplied'))}
    for row in snapshot['maintenance']:
        if row.get('state') == 'on':
            add('maintenance', row, str(row.get('name', row.get('entity_id'))) + ': maintenance flag active')
    for row in snapshot['low_batteries']:
        try:
            value = float(row['state'])
            if math.isfinite(value) and 0 <= value <= 20:
                add('battery', row, str(row.get('name', row.get('entity_id'))) + ': ' + str(row['state']) + '% battery')
        except (KeyError, ValueError, TypeError):
            continue
    for row in snapshot['unavailable']:
        if row.get('state') in ('unknown', 'unavailable'):
            add('unavailable', row, str(row.get('name', row.get('entity_id'))) + ': reading ' + row['state'] + ', device failure unconfirmed')
    for row in snapshot['network']:
        if row.get('entity_id', '').startswith('binary_sensor.') and row.get('state') == 'off':
            add('network', row, str(row.get('name', row.get('entity_id'))) + ': connectivity reports off')
    # Optional current backup monitor; dated recovery inspections are not live alerts.
    for row in snapshot.get('backups', []):
        if row.get('state') in ('failed', 'error'):
            add('backup', row, str(row.get('name', row.get('entity_id'))) + ': backup monitor reports failure')
    old = previous.get('alerts', {}) if isinstance(previous, dict) else None
    changed = [v for k, v in alerts.items() if old is None or k not in old or old[k].get('state') != v['state']]
    cleared = [v['text'] for k, v in (old or {}).items() if k not in alerts]
    label = 'Initial baseline: current readings' if old is None else 'New or changed since your last attention check'
    lines = [label, 'Sampled: ' + snapshot['sampled_at']]
    lines += [v['text'] + ' (source updated ' + v['updated'] + ')' for v in changed] or ['No new attention items.']
    if cleared:
        lines += ['Previously flagged items no longer flagged (not independently verified repaired):', *cleared]
    lines += [f'{len(alerts)} current monitored flags; {len(known_offline)} configured known-offline entities excluded.',
              'Unknown readings do not prove a device failed. Historical recovery notes are excluded from live alerts.']
    speech = (f'Initial check: {len(changed)} monitored items need attention.' if old is None else
              f'{len(changed)} new or changed attention items since your last check.')
    if changed:
        speech += ' ' + '; '.join(v['text'] for v in changed[:3]) + '.'
    return {'title': 'What needs attention', 'speech': speech, 'display': '\n'.join(lines)}, {'alerts': alerts, 'sampled_at': snapshot['sampled_at']}


def project_report(projects, read_turns):
    lines, active = [], 0
    for project in projects[:12]:
        label = str(project.get('name', 'Project'))[:100]
        lines += [label]
        try:
            turns = read_turns(project['thread_id'])
            current = turns[0] if turns else {}
            state = current.get('status', 'no recorded turns')
            active += state in ('inProgress', 'in_progress', 'started')
            stamp = current.get('updatedAt') or current.get('completedAt') or current.get('startedAt') or 'not supplied'
            if isinstance(stamp, (int, float)):
                if stamp > 1e12: stamp /= 1000
                stamp = datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).isoformat()
            lines += ['Current turn: ' + state, 'Last turn time: ' + str(stamp)]
            answers = []
            for turn in turns:
                if turn.get('status') != 'completed':
                    continue
                finals = [i.get('text', '') for i in turn.get('items', []) if i.get('type') == 'agentMessage' and i.get('phase') in ('final_answer', 'final')]
                if finals:
                    report = '\n'.join(finals)
                    report = re.sub(r'\[(?:COMPLETE|STATUS|COMMENTARY)\]\s*', '', report)
                    # Exclude advice/authentication exchanges from work progress.
                    is_advice = bool(re.search(r"^I[’']d (?:start|recommend)|^You don[’']t need to log in|verification code|^Close or minimize", report, re.I))
                    if not is_advice and re.search(r'\b(published|installed|implemented|fixed|removed|updated|created|deployed|finished|passed|verified|completed)\b', report, re.I):
                        answers.append(report)
                if len(answers) >= 3: break
            lines += ['Recent completed-work reports (historical):', *(a[:1800] for a in answers)] if answers else ['No completed-work report in the inspected history.']
            reports = '\n'.join(answers)
            tests = [l for l in reports.splitlines() if re.search(r'\b(test|tests|tested|verification|checks)\b.*\b(pass(?:ed)?|fail(?:ed|ure)?|run|ran|OK|[0-9]+)\b', l, re.I)]
            blockers = [l for l in reports.splitlines() if re.search(r'^\s*(?:[-*]\s*)?(?:blocked|blocker|pending|awaiting|decision|approval|remaining)(?:\s|:)|\b(?:is|remains|still) blocked\b', l, re.I)]
            lines += ['Reported tests: ' + ('; '.join(tests)[:1800] or 'Not recorded in recent reports.'),
                      'Reported blockers/decisions: ' + ('; '.join(blockers)[:1800] or 'Not recorded in recent reports.')]
        except Exception:
            lines += ['Project history unavailable; no completion or healthy status inferred.']
        lines.append('')
    lines.append('Read-only history snapshot. No task queued, tests rerun, or deployment performed. Reported results may be superseded.')
    return {'title': 'Codex project board', 'speech': f'Project board for {len(projects[:12])} configured projects. {active} have an active recorded turn. Recent work and reported tests are on your screen and in the app.',
            'display': '\n'.join(lines)}


def redact_result(result, config):
    """Suppress configured secrets and recognizable credential lines at output."""
    secrets = [v for k, v in config.items() if isinstance(v, str) and len(v) >= 8 and any(word in k.upper() for word in ('PASSWORD', 'SECRET', 'API_KEY', 'TOKEN'))]
    def clean(text):
        for secret in secrets:
            text = text.replace(secret, '[redacted]')
        text = re.sub(r'\bsk-[A-Za-z0-9_-]{12,}\b', '[redacted]', text)
        text = re.sub(r'(?im)^.*\b(?:password|api[ _-]?key|access[ _-]?token|bearer)\s*(?:is\s+|[:=]\s*).+$', '[credential details redacted]', text)
        return text
    def visit(value):
        if isinstance(value, str): return clean(value)
        if isinstance(value, list): return [visit(x) for x in value]
        if isinstance(value, dict): return {k:visit(v) for k,v in value.items()}
        return value
    data = structured(result)
    return json.dumps(visit(data)) if data else clean(result)
