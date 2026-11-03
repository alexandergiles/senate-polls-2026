#!/usr/bin/env python3
"""Commit data/results-live.json when the count changed (or every 15 minutes as a heartbeat); rebase and retry the push."""
import json, subprocess, datetime, sys
new = json.load(open('data/results-live.json'))
try:
    old = json.loads(subprocess.check_output(['git', 'show', 'HEAD:data/results-live.json']))
except Exception:
    old = {}
strip = lambda d: {k: {kk: vv for kk, vv in v.items() if kk not in ('fetched',)} for k, v in (d.get('races') or {}).items()}
changed = strip(new) != strip(old)
age_ok = True
try:
    t = datetime.datetime.fromisoformat(old.get('updated'))
    age_ok = (datetime.datetime.now(datetime.timezone.utc) - t.astimezone(datetime.timezone.utc)).total_seconds() > 15 * 60
except Exception:
    pass
if not changed and not age_ok:
    print('no change; skipping commit'); sys.exit(0)
subprocess.check_call(['git', 'config', 'user.name', 'github-actions[bot]'])
subprocess.check_call(['git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'])
subprocess.check_call(['git', 'add', 'data/results-live.json'])
msg = 'Live results ' + (new.get('updated') or '') + ('' if changed else ' (heartbeat)')
subprocess.check_call(['git', 'commit', '-q', '-m', msg])
for attempt in range(4):   # someone may have pushed (e.g. a hand edit of results-manual.json): rebase and retry
    if subprocess.call(['git', 'pull', '-q', '--rebase', '-X', 'theirs', 'origin', 'main']) == 0 and subprocess.call(['git', 'push', '-q']) == 0:
        break
    subprocess.call(['git', 'rebase', '--abort'])
else:
    sys.exit('push failed after retries')
print('committed:', msg)
