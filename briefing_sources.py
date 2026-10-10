"""Run inside Hermes: read Gmail headers, Calendar events and HA weather states.

No send, mark-read, event creation, service calls, or arbitrary command surface.
Credentials and OAuth refresh remain in Hermes' private persistent directory.
"""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request


def google_read(home, args):
    script = home / 'skills/productivity/google-workspace/scripts/google_api.py'
    result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, timeout=35)
    if result.returncode: raise RuntimeError('Google source not authenticated or unavailable')
    data = json.loads(result.stdout)
    if not isinstance(data, list): raise ValueError('Invalid Google source response')
    return data


def snapshots(home=None, run_google=None, get_states=None, now=None):
    home = Path(home or os.environ.get('HERMES_HOME', '~/.hermes')).expanduser()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    sampled = now.isoformat()
    output = {}
    run_google = run_google or (lambda args: google_read(home, args))
    def record(name, read):
        try:
            speech, display = read()
            output[name] = {'sampled_at': sampled, 'display': display, 'speech': speech}
        except Exception: output[name] = {'sampled_at': sampled, 'error': 'source_unavailable', 'display': 'Not authenticated or source unavailable.'}
    def email():
        items = run_google(['gmail', 'search', 'is:unread newer_than:7d', '--max', '8'])
        lines = ['Up to 8 recent unread messages (last 7 days). Headers only; nothing marked read.']
        for item in items[:8]:
            lines.append(str(item.get('date', '')) + ' — ' + str(item.get('from', ''))[:200] + '\n' + str(item.get('subject', '(no subject)'))[:300])
        if not items: lines.append('No matching unread messages returned.')
        return f'I found {len(items[:8])} recent unread email headers; this is a limited sample.', '\n'.join(lines)
    def calendar():
        end = now + datetime.timedelta(days=1)
        items = run_google(['calendar', 'list', '--start', now.isoformat(), '--end', end.isoformat()])
        lines = ['Primary calendar: next 24 hours. Times include their source timezone.']
        for item in items[:20]:
            start = item.get('start', '')
            if isinstance(start, dict): start = start.get('dateTime') or start.get('date') or ''
            lines.append(str(start) + ' — ' + str(item.get('summary', '(untitled event)'))[:300])
        if not items: lines.append('No events returned in this window.')
        if len(items) > 20: lines.append('Additional events omitted; ask Hermes for the full calendar.')
        summary = f'{len(items)} events returned for the next twenty-four hours.'
        if items: summary += ' Next: ' + str(items[0].get('summary', '(untitled event)'))[:120] + '.'
        return summary, '\n'.join(lines)
    def weather():
        if get_states:
            states = get_states()
        else:
            from dotenv import load_dotenv
            load_dotenv(home / '.env', override=False)
            url, token = os.environ.get('HASS_URL'), os.environ.get('HASS_TOKEN')
            if not url or not token: raise RuntimeError('HA weather not connected')
            req = urllib.request.Request(url.rstrip('/') + '/api/states', headers={'Authorization': 'Bearer ' + token})
            with urllib.request.urlopen(req, timeout=12) as response: states = json.load(response)
        rows = []
        for row in states:
            if not row.get('entity_id', '').startswith('weather.') or row.get('state') in ('unknown', 'unavailable'): continue
            try:
                updated = datetime.datetime.fromisoformat((row.get('last_reported') or row['last_updated']).replace('Z', '+00:00'))
                if updated.tzinfo is None or not -60 <= (now-updated).total_seconds() <= 10800: continue
            except (ValueError, KeyError, TypeError): continue
            rows.append(row)
        if not rows: raise RuntimeError('No current HA weather entity')
        lines = ['Current Home Assistant weather readings. This is not a fresh forecast request.']
        for row in rows[:3]:
            attrs = row.get('attributes', {})
            lines.append(str(attrs.get('friendly_name') or row['entity_id']) + ': ' + row['state'] + '\nTemperature: ' + str(attrs.get('temperature', 'unavailable')) + ' ' + str(attrs.get('temperature_unit', '')) + '\nReading updated: ' + str(row.get('last_updated', 'unknown')))
        attrs = rows[0].get('attributes', {})
        summary = 'Current home weather: ' + rows[0]['state'].replace('-', ' ').replace('_', ' ') + ', ' + str(attrs.get('temperature', 'temperature unavailable')) + ' ' + str(attrs.get('temperature_unit', '')) + '.'
        return summary, '\n'.join(lines)
    record('email', email); record('calendar', calendar); record('weather', weather)
    return output


if __name__ == '__main__':
    os.umask(0o077)
    print(json.dumps(snapshots(), ensure_ascii=False))
