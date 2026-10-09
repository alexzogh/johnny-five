"""Read-only routines, request progress, explicit handoffs and private activity UI."""
import datetime
import html
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from features import redact_result, structured

ROUTINES = {
    'morning': {'title': 'Morning briefing', 'sections': ['house', 'projects', 'weather', 'calendar']},
    'evening': {'title': 'Evening briefing', 'sections': ['house', 'projects']},
    'development': {'title': 'Development briefing', 'sections': ['projects']},
}
SECTIONS = {'house', 'projects', 'weather', 'calendar', 'email'}


def private_json(path):
    file = Path(path).expanduser()
    if file.stat().st_mode & 0o077:
        raise ValueError('Private settings must have mode 600 or stricter')
    return json.loads(file.read_text())


def routine_settings(config):
    routines = dict(ROUTINES)
    if config.get('ROUTINES_FILE'):
        custom = private_json(config['ROUTINES_FILE'])
        if not isinstance(custom, dict) or len(custom) > 20:
            raise ValueError('Invalid routines')
        for name, routine in custom.items():
            if not re.fullmatch(r'[a-z][a-z ]{0,39}', name) or not isinstance(routine, dict):
                raise ValueError('Invalid routine name')
            sections = routine.get('sections')
            if not isinstance(routine.get('title'), str) or not isinstance(sections, list) or not sections or any(s not in SECTIONS for s in sections):
                raise ValueError('Routines support only fixed read-only sections')
            routines[name] = {'title': routine['title'][:120], 'sections': list(dict.fromkeys(sections))}
    return routines


def source_section(name, config, now=None):
    """Optional external provider snapshots. Never infer OAuth from a file's presence."""
    now = time.time() if now is None else now
    if not config.get('BRIEFING_SOURCES_FILE'):
        return name.title() + ': not connected.'
    try:
        sources = private_json(config['BRIEFING_SOURCES_FILE'])
        source = sources.get(name, {})
        sampled = datetime.datetime.fromisoformat(source['sampled_at'].replace('Z', '+00:00'))
        if sampled.tzinfo is None: raise ValueError('Timestamp needs timezone')
        age = now - sampled.timestamp()
        if not -60 <= age <= (10800 if name == 'weather' else 3600):
            return name.title() + ': source is stale; refresh the connector.'
        if not isinstance(source.get('display'), str) or source.get('error'):
            raise ValueError('Invalid source')
        return name.title() + ' (sampled ' + source['sampled_at'] + ')\n' + source['display'][:4000]
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        return name.title() + ': current source unavailable.'


def progress_answer(row, now=None):
    if not row:
        return {'title': 'Task progress', 'speech': 'There is no recent request to check.', 'display': 'No request found.'}
    ident, target, prompt, status, result, created, started, finished = row
    now = time.time() if now is None else now
    elapsed = max(0, int((finished or now) - (started or created)))
    timing = ('Execution' if started else 'Since queued') + f': {elapsed // 60}m {elapsed % 60}s'
    if status not in ('queued', 'running') and not finished:
        timing = 'Timing unavailable for this older request.'
    text = {'queued': 'waiting in the local queue', 'running': 'running', 'completed': 'finished',
            'failed': 'failed', 'unavailable': 'unable to reach its source',
            'uncertain': 'not confirmed; check the agent conversation before retrying'}.get(status, 'in an unknown state')
    data = structured(result) or {}
    display = f'Request {ident}\nAgent: {target}\nStatus: {status}\n{timing}\n'
    speech = f'Your last request is {text}.'
    if status == 'completed':
        speech += ' ' + str(data.get('speech') or result)[:650]
    if status not in ('queued', 'running'):
        display += '\nResult\n' + str(data.get('display') or result)
    decisions = reported_decisions(str(data.get('display') or result))
    if decisions:
        display += '\n\nReported blockers or decisions (not automatically acted on)\n' + '\n'.join(decisions)
    if status in ('queued', 'running'):
        display += '\nNo estimated completion time or percentage is available. Refresh does not restart the request.'
    return {'title': 'Task progress', 'speech': speech[:1200], 'display': display[:16000]}


def reported_decisions(text):
    return [line[:500] for line in text.splitlines() if re.match(r'\s*(?:[-*] )?(?:Blocked|Blocker|Pending|Decision needed|Needs approval|Awaiting owner)\s*:', line, re.I)][:8]


def activity_data(store, owners, config):
    jobs = []
    for row in store.recent(owners, 40):
        ident, target, prompt, status, result, created, started, finished = row
        cleaned = redact_result(result, config)
        data = structured(cleaned) or {}
        # Do not publish raw identities, Codex thread IDs, or unredacted prompts.
        jobs.append({'id': ident, 'target': target, 'status': status, 'created': created,
                     'started': started, 'finished': finished,
                     'request': redact_result(prompt, config)[:2000],
                     'title': str(data.get('title') or 'Request'),
                     'speech': str(data.get('speech') or cleaned)[:1200],
                     'answer': str(data.get('display') or cleaned)[:16000],
                     'decisions': reported_decisions(str(data.get('display') or cleaned))})
    return {'sampled_at': time.time(), 'jobs': jobs, 'read_only': True}


def activity_html(data):
    escape = html.escape
    cards = []
    for job in data['jobs']:
        date = datetime.datetime.fromtimestamp(job['created'], datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        decisions = ('<p class="pending">Reported decision / blocker</p><pre>' + escape('\n'.join(job['decisions'])) + '</pre>') if job['decisions'] else ''
        cards.append('<article><h2>' + escape(job['title']) + '</h2><p class="meta">' + escape(job['target'] + ' · ' + job['status'] + ' · ' + date) + '</p><details><summary>Request</summary><pre>' + escape(job['request']) + '</pre></details><pre>' + escape(job['answer'] or 'Waiting for a result.') + '</pre>' + decisions + '</article>')
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Johnny Five activity</title><style>body{font:17px system-ui;background:#101820;color:#edf3fa;max-width:900px;margin:auto;padding:20px}h1{margin-bottom:6px}article{background:#1b2936;border-radius:12px;padding:20px;margin:18px 0}h2{font-size:22px}.meta{color:#b8cadb}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.5}.pending{color:#ffd080}a{color:#83c7ff}.refresh{display:inline-block;padding:12px 18px;background:#283d51;border-radius:8px}summary{cursor:pointer}</style><h1>Johnny Five activity</h1><p>Recent requests, written answers, and reported decisions. Read only.</p><a class="refresh" href="/">Refresh</a>''' + (''.join(cards) or '<p>No requests yet.</p>') + '<p>Reported decisions are excerpts, not authorization. This page never runs a task.</p></html>'


def activity_server(store, config):
    """Separate loopback listener; ONLY expose through private Tailscale Serve.

    Serve strips spoofed identity headers. Never point Funnel or an untrusted proxy
    to this port, and never bind to LAN addresses. Local processes are trusted.
    ACTIVITY_USERS maps each allowed Tailscale login to its allowed Alexa account IDs.
    """
    mapping = json.loads(config.get('ACTIVITY_USERS', '{}'))
    allowed = set(filter(None, (x.strip() for x in config.get('ALEXA_USER_IDS', '').split(','))))
    if not isinstance(mapping, dict) or not mapping or any(not isinstance(k, str) or not isinstance(v, list) or not v or any(not isinstance(o, str) or o not in allowed for o in v) for k, v in mapping.items()):
        raise ValueError('Activity requires an explicit Tailscale-to-Alexa account mapping')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def setup(self):
            super().setup()
            self.connection.settimeout(5)
        def do_GET(self):
            owners = mapping.get(self.headers.get('Tailscale-User-Login', ''))
            if not owners:
                self.send_error(403, 'Connect with an authorized Tailscale account')
                return
            if self.path not in ('/', '/api/activity'):
                self.send_error(404); return
            data = activity_data(store, owners, config)
            body = (json.dumps(data) if self.path == '/api/activity' else activity_html(data)).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if self.path == '/api/activity' else 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def do_POST(self): self.send_error(405)
    return ThreadingHTTPServer(('127.0.0.1', int(config.get('ACTIVITY_PORT', '9121'))), Handler)
