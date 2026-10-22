# -*- coding: utf-8 -*-
"""Services : serveur web local, publication MQTT (Home Assistant), synthèse vocale."""
import json, math, sys, socket, threading, subprocess, time
from collections import deque
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from PySide6 import QtCore

# ------------------------------------------------------------------ Web
PAGE = """<!doctype html><html lang=fr><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>XDM1041</title><style>
body{margin:0;background:#0d0e10;color:#ddd;font-family:Segoe UI,Roboto,sans-serif}
#v{font:700 15vw/1.1 Consolas,monospace;color:#4cff7a;text-align:right;padding:2vw 4vw 0;white-space:nowrap}
@media(min-width:900px){#v{font-size:120px}}
.r{display:flex;justify-content:space-between;align-items:center;padding:4px 4vw;color:#aaa;gap:10px;flex-wrap:wrap}
#pf{font-weight:700;padding:2px 12px;border-radius:6px;color:#000}#s{color:#7ad7ff;font:20px Consolas,monospace}
canvas{width:100%;height:45vh;display:block}</style>
<div class=r><span id=m>…</span><span id=pf></span></div><div id=v>-----</div>
<div class=r><span id=st></span><span id=s></span></div><canvas id=c></canvas>
<script>
let pts=[],since=0,gen=-1;const c=document.getElementById('c'),g=c.getContext('2d'),$=i=>document.getElementById(i);
async function tick(){try{const j=await(await fetch('/api/state?since='+since+'&gen='+gen)).json();
if(j.gen!==gen){pts=[];gen=j.gen}
$('v').textContent=j.display;$('m').textContent=j.mode+(j.online?'':'  — hors ligne');$('s').textContent=j.sec||'';$('st').textContent=j.stats||'';
const pf=$('pf');pf.textContent=j.pf||'';pf.style.background=j.pf=='PASS'?'#4cff7a':j.pf=='FAIL'?'#ff5050':'transparent';
for(const p of j.points){pts.push(p);since=p[0]}pts=pts.slice(-1500);draw(j.unit)}catch(e){}setTimeout(tick,500)}
function draw(u){const d=devicePixelRatio,W=c.width=c.clientWidth*d,H=c.height=c.clientHeight*d;g.clearRect(0,0,W,H);
const v=pts.filter(p=>p[1]!==null);if(v.length<2)return;let lo=Infinity,hi=-Infinity;for(const p of v){lo=Math.min(lo,p[1]);hi=Math.max(hi,p[1])}
if(hi==lo){hi+=Math.abs(hi)*1e-4+1e-12;lo-=Math.abs(lo)*1e-4+1e-12}const t0=pts[0][0],t1=pts[pts.length-1][0],pad=24*d;
g.strokeStyle='#2a2d33';g.fillStyle='#999';g.font=(11*d)+'px sans-serif';
for(let i=0;i<=4;i++){const y=pad+(H-2*pad)*i/4;g.beginPath();g.moveTo(0,y);g.lineTo(W,y);g.stroke();g.fillText((hi-(hi-lo)*i/4).toPrecision(6)+' '+u,4,y-3)}
g.strokeStyle='#4cff7a';g.lineWidth=1.5*d;g.beginPath();let f=true;
for(const p of pts){if(p[1]===null){f=true;continue}const x=(p[0]-t0)/((t1-t0)||1)*W,y=pad+(hi-p[1])/(hi-lo)*(H-2*pad);f?g.moveTo(x,y):g.lineTo(x,y);f=false}g.stroke()}
tick();</script>"""


class WebServer:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = dict(display="-----", unit="", mode="", sec="", pf="", stats="", online=False)
        self.hist = deque(maxlen=5000)
        self.gen = 0
        self.httpd = None

    def update(self, t, v, **kw):
        with self.lock:
            self.state.update(kw)
            self.hist.append((round(t, 3), v if v is not None and math.isfinite(v) else None))

    def set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def clear(self):
        with self.lock:
            self.hist.clear()
            self.gen += 1

    def snapshot(self, since, gen):
        with self.lock:
            pts = list(self.hist) if gen != self.gen else [p for p in self.hist if p[0] > since]
            return dict(self.state, gen=self.gen, points=pts[-1500:])

    def start(self, port):
        self.stop()
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/api/state":
                    q = parse_qs(u.query)
                    try:
                        since, gen = float(q.get("since", ["0"])[0]), int(q.get("gen", ["-1"])[0])
                    except ValueError:
                        since, gen = 0.0, -1
                    body, ctype = json.dumps(srv.snapshot(since, gen)).encode(), "application/json"
                elif u.path == "/":
                    body, ctype = PAGE.encode("utf-8"), "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    @staticmethod
    def local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.0.2.1", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"


# ------------------------------------------------------------------ MQTT
HA_CLASS = {"V": "voltage", "A": "current", "Hz": "frequency", "°C": "temperature", "°F": "temperature", "K": "temperature", "s": "duration"}


class MqttPublisher(QtCore.QObject):
    status = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.client = None
        self.cfg = {}
        self.last_pub = 0.0
        self.unit_sent = None
        self.sid = "dmm"

    def start(self, cfg, sid):
        self.stop()
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.status.emit("paho-mqtt absent")
            return False
        self.cfg, self.sid, self.unit_sent = dict(cfg), (sid or "dmm").replace(" ", ""), None
        p = self.cfg["prefix"].rstrip("/")
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"xdm1041-{self.sid}-{int(time.time())}")
        if self.cfg.get("user"):
            c.username_pw_set(self.cfg["user"], self.cfg.get("password") or None)
        c.will_set(f"{p}/status", "offline", retain=True)

        def on_connect(cl, ud, flags, rc, props=None):
            if rc == 0:
                cl.publish(f"{p}/status", "online", retain=True)
                self.unit_sent = None
                self.status.emit(f"MQTT connecté ({self.cfg['host']})")
            else:
                self.status.emit(f"MQTT refusé : {rc}")

        def on_disconnect(cl, ud, flags, rc, props=None):
            self.status.emit("MQTT déconnecté")

        c.on_connect, c.on_disconnect = on_connect, on_disconnect
        try:
            c.connect_async(self.cfg["host"], int(self.cfg.get("port", 1883)), 30)
            c.loop_start()
        except Exception as e:
            self.status.emit(f"MQTT : {e}")
            return False
        self.client = c
        return True

    def stop(self):
        if self.client:
            try:
                self.client.publish(f"{self.cfg['prefix'].rstrip('/')}/status", "offline", retain=True)
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
            self.client = None

    def _discovery(self, unit):
        p = self.cfg["prefix"].rstrip("/")
        dev = {"identifiers": [f"xdm1041_{self.sid}"], "name": "OWON XDM1041", "manufacturer": "OWON", "model": "XDM1041"}
        base = {"state_topic": f"{p}/state", "availability_topic": f"{p}/status", "device": dev}
        val = dict(base, name="Mesure", unique_id=f"xdm1041_{self.sid}_value", value_template="{{ value_json.value }}",
                   unit_of_measurement=unit, state_class="measurement", json_attributes_topic=f"{p}/state")
        if unit in HA_CLASS:
            val["device_class"] = HA_CLASS[unit]
        mode = dict(base, name="Fonction", unique_id=f"xdm1041_{self.sid}_mode", value_template="{{ value_json.mode }}", icon="mdi:multimeter")
        pf = dict(base, name="Limites", unique_id=f"xdm1041_{self.sid}_pf", value_template="{{ value_json.pf }}", icon="mdi:check-decagram")
        root = self.cfg.get("discovery_prefix", "homeassistant")
        for k, d in (("value", val), ("mode", mode), ("pf", pf)):
            self.client.publish(f"{root}/sensor/xdm1041_{self.sid}/{k}/config", json.dumps(d), retain=True)

    def publish(self, t, v, unit, mode, pf, sec, display):
        if not self.client or not self.client.is_connected():
            return
        if t - self.last_pub < float(self.cfg.get("interval", 1.0)):
            return
        self.last_pub = t
        if self.cfg.get("discovery", True) and unit != self.unit_sent:
            self._discovery(unit)
            self.unit_sent = unit
        p = self.cfg["prefix"].rstrip("/")
        payload = dict(value=v if v is not None and math.isfinite(v) else None, unit=unit, mode=mode, pf=pf or "",
                       secondary=sec if sec is not None and math.isfinite(sec) else None, display=display, ts=round(t, 3))
        self.client.publish(f"{p}/state", json.dumps(payload))


# ------------------------------------------------------------------ Voix
class Speaker:
    def __init__(self):
        self.engine = None
        self.proc = None
        try:
            from PySide6.QtTextToSpeech import QTextToSpeech
            from PySide6.QtCore import QLocale
            self.engine = QTextToSpeech()
            for loc in self.engine.availableLocales():
                if loc.language() == QLocale.Language.French:
                    self.engine.setLocale(loc)
                    break
        except Exception:
            self.engine = None

    def say(self, text):
        if self.engine is not None:
            try:
                self.engine.stop()
                self.engine.say(text)
                return
            except Exception:
                pass
        if sys.platform == "win32":
            if self.proc and self.proc.poll() is None:
                return
            safe = text.replace("'", " ")
            self.proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command",
                 f"Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                 f"try{{$s.SelectVoiceByHints('NotSet','NotSet',0,[Globalization.CultureInfo]'fr-FR')}}catch{{}};$s.Speak('{safe}')"],
                creationflags=0x08000000)
