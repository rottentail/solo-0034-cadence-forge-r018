#!/usr/bin/env python3
"""Time-responsibility compatibility harness (not a test suite; testing stays deferred).

Drives the real HTTP and media pipeline through a scenario built to stress
editorial time boundaries: spans whose millisecond edges do not align with
the 24fps output raster, one source repeated across spans, and cues that
straddle span gaps. Emits a canonical JSON record so records from two
binaries can be diffed to prove the plan/caption/encoding time
relationships did not drift.

Phases:
  seed   - fresh data root; ingest, revise, preview, render MP4+WAV, then
           SIGKILL while a long render runs, leaving recovery evidence.
  resume - start on an existing root; recovery, re-preview, retry the
           interrupted render, re-verify a committed artifact.

Typical proof:
  python3 scripts/timegrid_check.py --bin forged-A --root r1 --phase seed  --out A-seed.json
  python3 scripts/timegrid_check.py --bin forged-B --root r2 --phase seed  --out B-seed.json
  diff A-seed.json B-seed.json            # fresh-path semantics identical
  cp -a r1 r1b && python3 scripts/timegrid_check.py --bin forged-B --root r1b --phase resume --out B-resume.json
"""
import argparse
import copy
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEX_ID = re.compile(r'^[a-f0-9]{32}$')
TIMESTAMP = re.compile(r'^\d{4}-\d{2}-\d{2}T')


def need(value, message):
    if not value:
        raise RuntimeError(message)


def canonical(node, ids):
    """Replace random identities and wall-clock readings with stable placeholders.

    Content digests (source and artifact SHA-256) stay literal: they are the
    determinism assertion this harness exists to check.
    """
    if isinstance(node, dict):
        out = {}
        for key in sorted(node):
            value = node[key]
            if HEX_ID.match(key):
                key = canonical(key, ids)
            if key == 'request_hash':
                out[key] = 'hash'
            else:
                out[key] = canonical(value, ids)
        return out
    if isinstance(node, list):
        return [canonical(v, ids) for v in node]
    if isinstance(node, str):
        if HEX_ID.match(node):
            if node not in ids:
                ids[node] = f'id#{len(ids) + 1}'
            return ids[node]
        if TIMESTAMP.match(node):
            return 'TS'
    return node


def scenario_shapes(sid):
    """Sheet/recipe shapes with non-integer-frame edges and a repeated source."""
    spans = [
        dict(source_id=sid, in_ms=333, out_ms=1783, gain_db=-3),    # 1450ms = 34.8 frames @24fps
        dict(source_id=sid, in_ms=2711, out_ms=3999, gain_db=0),    # 1288ms = 30.912 frames @24fps
        dict(source_id=sid, in_ms=333, out_ms=1783, gain_db=-1),    # repeated source range
    ]
    cues = [
        dict(source_id=sid, start_ms=1700, end_ms=2900, text='跨界 <&>'),   # clips into all three spans
        dict(source_id=sid, start_ms=400, end_ms=1000, text='完整'),        # inside the repeated range
        dict(source_id=sid, start_ms=1800, end_ms=2700, text='空隙'),       # falls between spans: dropped
        dict(source_id=sid, start_ms=300, end_ms=500, text='片头'),         # clips at span head
        dict(source_id=sid, start_ms=333, end_ms=600, text='同起点'),       # shares output start with 片头
    ]
    mp4_recipe = dict(name='review-grid', format='mp4', width=160, height=90,
                      fps=24, sample_rate=48000, normalize=False, target_lufs=-16)
    wav_recipe = dict(name='reference-wave', format='wav', width=0, height=0,
                      fps=0, sample_rate=48000, normalize=True, target_lufs=-16)
    return spans, cues, mp4_recipe, wav_recipe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin', required=True, help='forged server binary under test')
    ap.add_argument('--root', required=True, help='data root (fresh for seed, existing for resume)')
    ap.add_argument('--phase', choices=['seed', 'resume'], required=True)
    ap.add_argument('--media', default='', help='calibration MP4 to reuse across runs')
    ap.add_argument('--out', default='', help='write canonical record here (default stdout)')
    args = ap.parse_args()
    root = Path(args.root)
    ffmpeg = os.environ.get('FFMPEG_BIN', 'ffmpeg')
    if args.phase == 'seed':
        need(not root.exists(), f'seed root {root} must not exist yet')
        media_path = Path(args.media) if args.media else Path(tempfile.mkdtemp(prefix='cadence-media-')) / 'calibration.mp4'
        if not media_path.exists():
            subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
                            '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=24:duration=4',
                            '-f', 'lavfi', '-i', 'sine=frequency=660:sample_rate=48000:duration=4',
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1',
                            '-c:a', 'aac', '-shortest', str(media_path)], check=True, timeout=30)
    else:
        need((root / 'state.json').exists(), f'resume root {root} has no state.json')
        media_path = None
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    address = 'http://127.0.0.1:' + str(port) + '/forge/v1'
    env = dict(os.environ, FORGE_ROOT=str(root), FORGE_ADDR='127.0.0.1:' + str(port), FORGE_WORKERS='1')
    log = open(root.parent / (root.name + '.server.log'), 'ab')
    process = None

    def request(method, route, body=None, headers=None, status=200, raw=False):
        hs = dict(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            hs['Content-Type'] = 'application/json'
        req = urllib.request.Request(address + route, data=body, headers=hs, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = response.read()
            need(response.status == status, f'{method} {route}: {response.status}, wanted {status}: {data[:600]!r}')
            return data if raw or not data else json.loads(data)

    def start():
        nonlocal process
        process = subprocess.Popen([str(Path(args.bin).resolve())], cwd=ROOT, env=env,
                                   stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                request('GET', '/health')
                return
            except (OSError, RuntimeError):
                if process.poll() is not None:
                    raise RuntimeError('server exited; see ' + log.name)
                time.sleep(.05)
        raise RuntimeError('server readiness timed out')

    def stop(force=False):
        nonlocal process
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            process.wait(timeout=15)
        process = None

    def wait_render(rid, desired=None, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = request('GET', '/renders/' + rid)
            if desired and r['state'] == desired:
                return r
            if not desired and r['state'] in ('succeeded', 'failed', 'cancelled'):
                return r
            time.sleep(.02)
        raise RuntimeError(f'render {rid} state timeout: {r["state"]}')

    record = {'phase': args.phase}
    ids = {}
    try:
        start()
        if args.phase == 'seed':
            media = media_path.read_bytes()
            source = request('POST', '/sources?name=timegrid-calibration', media, status=201)
            sid = source['id']
            need(source['digest'] == hashlib.sha256(media).hexdigest(), 'source digest mismatch')
            spans, cues, mp4_recipe, wav_recipe = scenario_shapes(sid)
            mp4r = request('POST', '/recipes', mp4_recipe, status=201)
            wavr = request('POST', '/recipes', wav_recipe, status=201)
            sheet = request('POST', '/sheets', dict(name='timegrid', spans=spans, cues=cues), status=201)
            sh = sheet['id']
            plan = request('GET', '/sheets/' + sh + '/preview?recipe_id=' + mp4r['id'])
            need(plan['duration_ms'] == 4188, f"plan duration drifted: {plan['duration_ms']}")
            record['preview'] = plan
            req = dict(sheet_id=sh, sheet_revision=1, recipe_id=mp4r['id'], recipe_revision=1)
            r1 = wait_render(request('POST', '/renders', req, {'Idempotency-Key': 'grid-mp4'}, status=202)['id'])
            need(r1['state'] == 'succeeded', str(r1['attempts']))
            record['mp4_render'] = r1
            record['mp4_artifact_sha256'] = hashlib.sha256(
                request('GET', '/renders/' + r1['id'] + '/artifact', raw=True)).hexdigest()
            captions = request('GET', '/renders/' + r1['id'] + '/artifact?captions=1', raw=True)
            need(captions.decode() == r1['plan']['vtt'], 'served captions drifted from frozen plan')
            record['mp4_captions'] = captions.decode()
            req_wav = dict(req, recipe_id=wavr['id'])
            r2 = wait_render(request('POST', '/renders', req_wav, {'Idempotency-Key': 'grid-wav'}, status=202)['id'])
            need(r2['state'] == 'succeeded', str(r2['attempts']))
            record['wav_render'] = r2
            record['wav_artifact_sha256'] = hashlib.sha256(
                request('GET', '/renders/' + r2['id'] + '/artifact', raw=True)).hexdigest()
            long_sheet = request('POST', '/sheets', dict(name='timegrid-extended', spans=spans * 10, cues=cues), status=201)
            r3 = request('POST', '/renders', dict(req, sheet_id=long_sheet['id']),
                         {'Idempotency-Key': 'grid-long'}, status=202)
            wait_render(r3['id'], 'running')
            record['interrupted_render_id'] = r3['id']
            record['state_at_kill'] = request('GET', '/state')
            record['events_at_kill'] = request('GET', '/events?after=0')
            stop(force=True)
        else:
            state = request('GET', '/state')
            record['state_after_recovery'] = state
            sheets = state['sheets']
            sh = next(s for s, versions in sheets.items() if len(versions[-1]['spans']) == 3)
            recipes = state['recipes']
            mp4_recipe_id = next(r for r, versions in recipes.items() if versions[-1]['format'] == 'mp4')
            plan = request('GET', '/sheets/' + sh + '/preview?recipe_id=' + mp4_recipe_id)
            record['preview_after_recovery'] = plan
            interrupted = next(r for r in state['renders'].values()
                               if r['state'] == 'failed' and r['attempts'][-1]['outcome'] == 'interrupted')
            retried = request('POST', '/renders/' + interrupted['id'] + '/retry', status=202)
            done = wait_render(retried['id'])
            need(done['state'] == 'succeeded' and len(done['attempts']) == 2, str(done['attempts']))
            record['retried_render'] = done
            committed = next(r for r in state['renders'].values()
                             if r['state'] == 'succeeded' and r['recipe']['format'] == 'mp4')
            verification = request('POST', '/renders/' + committed['id'] + '/verify')
            need(verification['digest'] == committed['artifact']['inspection']['digest'],
                 'committed artifact digest drifted across binaries')
            record['reverification'] = verification
            captions = request('GET', '/renders/' + committed['id'] + '/artifact?captions=1', raw=True)
            need(captions.decode() == committed['plan']['vtt'], 'committed captions drifted')
            record['committed_captions'] = captions.decode()
            record['events_after_recovery'] = request('GET', '/events?after=0')
            stop()
        out = json.dumps(canonical(record, ids), ensure_ascii=False, indent=2, sort_keys=True) + '\n'
        if args.out:
            Path(args.out).write_text(out)
        else:
            print(out, end='')
    except Exception:
        log.flush()
        raise
    finally:
        if process and process.poll() is None:
            stop(force=True)
        log.close()


if __name__ == '__main__':
    main()
