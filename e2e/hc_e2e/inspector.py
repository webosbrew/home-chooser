# SPDX-License-Identifier: Apache-2.0
"""The web inspector (CDP over a websocket forwarded with ares-novacom) and the page probes."""
import base64
import json
import os
import signal
import socket
import struct
import urllib.request

from . import timing
from .constants import APP_ID

# ---------------------------------------------------------------------------------------------
# Inspector (CDP over a websocket, forwarded with ares-novacom)
# ---------------------------------------------------------------------------------------------
class Inspector:
    def __init__(self, dev):
        # A free port is picked and released before ares-novacom binds it, so a parallel run can take
        # it in between; then our forward exits at once and another port is tried.
        for _ in range(3):
            s = socket.socket()
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]
            s.close()
            self.proc = dev.forward(9998, self.port)
            timing.sleep(2)
            if self.proc.poll() is None:
                break

    def close(self):
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except OSError:
            pass

    def _page(self, app_id=APP_ID):
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/json' % self.port, timeout=5) as r:
                pages = json.loads(r.read().decode())
        except Exception:
            return None
        for p in pages:
            if app_id in p.get('url', ''):
                return p
        return None

    def log_pages(self, why):
        """Say what /json lists, so a page-lookup failure is told apart from empty DOM content."""
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/json' % self.port, timeout=5) as r:
                pages = [(p.get('id'), p.get('title'), p.get('url')) for p in json.loads(r.read().decode())]
        except Exception as e:
            pages = 'unreadable: %r' % (e,)
        print('  inspector evaluate -> None (%s); /json pages: %s' % (why, pages), flush=True)

    def evaluate(self, expr, app_id=APP_ID):
        """Runtime.evaluate in the page of `app_id` (default: Home Chooser's); None if it is not there.  Why a call gave None is
        kept in self.last_error (no page, the forward gone, a socket error, an error reply, a JS exception)."""
        self.last_error = None
        page = self._page(app_id)
        if not page:
            self.last_error = 'no page for %s in /json (forward %s)' % (
                app_id, 'alive' if self.proc.poll() is None else 'exited rc=%s' % self.proc.returncode)
            self.log_pages('no page for %s' % app_id)
            return None
        s = None
        try:
            s = socket.create_connection(('127.0.0.1', self.port), timeout=8)
            key = base64.b64encode(os.urandom(16)).decode()
            s.sendall(('GET /devtools/page/%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\n'
                       'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n'
                       % (page['id'], self.port, key)).encode())
            buf = b''
            while b'\r\n\r\n' not in buf:
                buf += s.recv(4096)
            buf = buf.split(b'\r\n\r\n', 1)[1]
            msg = json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                              'params': {'expression': expr, 'returnByValue': True}}).encode()
            mask = os.urandom(4)
            hdr = bytes([0x81])
            if len(msg) < 126:
                hdr += bytes([0x80 | len(msg)])
            elif len(msg) < 65536:
                hdr += bytes([0x80 | 126]) + struct.pack('>H', len(msg))
            else:
                hdr += bytes([0x80 | 127]) + struct.pack('>Q', len(msg))
            s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(msg)))
            while True:
                frame, buf = self._frame(s, buf)
                if frame is None:
                    self.last_error = 'websocket gave no frame'
                    return None
                try:
                    d = json.loads(frame.decode('utf-8', 'replace'))
                except ValueError:
                    continue
                if d.get('id') == 1:
                    res = d.get('result') or {}
                    v = (res.get('result') or {}).get('value')
                    if v is None:
                        self.last_error = 'reply without a value: %s' % json.dumps(d)[:400]
                    return v
        except (OSError, ValueError) as e:
            self.last_error = 'websocket %s: %r (page %s, forward %s)' % (
                'handshake/read', e, page.get('id'), 'alive' if self.proc.poll() is None else 'exited')
            return None
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass

    @staticmethod
    def _frame(s, buf):
        def need(n):
            nonlocal buf
            while len(buf) < n:
                chunk = s.recv(65536)
                if not chunk:
                    raise OSError('closed')
                buf += chunk
        need(2)
        ln = buf[1] & 0x7f
        off = 2
        if ln == 126:
            need(4)
            ln, off = struct.unpack('>H', buf[2:4])[0], 4
        elif ln == 127:
            need(10)
            ln, off = struct.unpack('>Q', buf[2:10])[0], 10
        need(off + ln)
        data, buf = buf[off:off + ln], buf[off + ln:]
        return data, buf


FOCUS_JS = ("(function(){var e=document.activeElement;var r=e&&e.closest&&e.closest('[data-app-id]');"
            "return JSON.stringify({row:r?r.getAttribute('data-app-id'):null,"
            "text:e?(e.textContent||'').trim().slice(0,80):null,"
            "dialog:(function(){var d=document.querySelector('[role=dialog]');return d?d.getAttribute('aria-label'):null})(),"
            "dialogText:(function(){var d=document.querySelector('[role=dialog]');return d?(d.textContent||'').trim().slice(0,300):null})(),"
            "toast:(function(){var d=document.querySelector('[role=status]');return d?d.textContent:null})(),"
            "restart:!!document.querySelector('[data-spotlight-id=restart-now]'),"
            "ticked:(function(){var c=document.querySelector('[data-app-id] [aria-label=Current]');"
            "var r=c&&c.closest('[data-app-id]');return r?r.getAttribute('data-app-id'):''})(),"
            "rows:Array.prototype.map.call(document.querySelectorAll('[data-app-id]'),function(x){return x.getAttribute('data-app-id')})})})()")
# Key focus of the Home Chooser page: on 9.x its window is an overlay (appinfo defaultWindowType), so sam's
# foreground app stays the card beneath (com.webos.app.home) while our dialog has the keys; the page's own
# document.hasFocus() is what says the keys reach it.
PAGE_JS = ("JSON.stringify({focus:document.hasFocus(),visibility:document.visibilityState,href:location.href})")
PLANT_JS = ("(function(){localStorage.setItem('homechooser.pending',JSON.stringify("
            "{id:'%s',title:'stale',at:Date.now()-86400000}));return localStorage.getItem('homechooser.pending')})()")
MARKER_JS = "String(localStorage.getItem('homechooser.pending'))"

# The row carrying the check mark (aria-label "Current"), by data-app-id; '' when none.
TICK_JS = ("(function(){var c=document.querySelector('[data-app-id] [aria-label=Current]');"
           "var r=c&&c.closest('[data-app-id]');return r?r.getAttribute('data-app-id'):''})()")
