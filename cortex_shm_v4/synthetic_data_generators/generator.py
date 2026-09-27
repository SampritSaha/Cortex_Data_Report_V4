import csv, gzip, json, math, zlib
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR=Path(__file__).resolve().parents[1]
CONFIG=json.loads((BASE_DIR/'config'/'projects.json').read_text(encoding='utf-8'))
DATA_DIR=BASE_DIR/'data'; DATA_DIR.mkdir(exist_ok=True)

def project(pid): return next(p for p in CONFIG['projects'] if p['id']==pid)
def path(pid): return DATA_DIR/f'{pid}.csv.gz'

def temp_at(ts, base):
    h=ts.hour+ts.minute/60+ts.second/3600
    return base + 4.0*math.sin((h-8)*2*math.pi/24) + 0.12*math.sin(ts.timetuple().tm_yday/15)

def traffic_at(ts):
    h=ts.hour+ts.minute/60
    morning=0.8*math.exp(-((h-8)/2.4)**2)
    evening=1.2*math.exp(-((h-20)/3.0)**2)
    night=0.45 if h<5 else 1.0
    return max(0.08,(0.25+morning+evening)*night)

def noise(seed, n, channel):
    # Deterministic pseudo-random noise: repeatable data without looking like a clean sine wave.
    x=(seed*1103515245 + n*12345 + channel*2654435761) & 0xffffffff
    x ^= (x >> 13)
    x = (x * 1274126177) & 0xffffffff
    return (x / 4294967295.0) * 2.0 - 1.0

def value(sensor, ts, n, seed):
    st=sensor['type']; loc=sensor['location']; tf=temp_at(ts,25+seed%5); tr=traffic_at(ts)
    ch=sum(ord(c) for c in sensor['id'])
    r1=noise(seed,n,ch); r2=noise(seed,n//3+17,ch+9); r3=noise(seed,n//11+41,ch+21)
    if st=='Temperature':
        # Slow thermal trend + small measurement noise.
        return tf + 0.10*r1 + 0.035*r2
    loc_factor=1.0 if any(x in loc.lower() for x in ['mid','centre','center','top']) else 0.35
    base=sensor.get('limit',1.0)
    if st in ('Deflection','LVDT'):
        load=base*(0.28+0.34*tr)*loc_factor
        drift=0.05*base*math.sin(n/2200 + ch/50)
        jitter=0.035*base*r1 + 0.018*base*r2
        event=0.18*base*max(0,tr-0.9)*abs(r3)
        return max(0, load + drift + jitter + event)
    if st in ('Vibration','Acceleration'):
        amp=(0.78 if st=='Vibration' else 0.17)*(0.42+tr)
        f1=0.55 + (ch%7)*0.035
        f2=0.13 + (ch%5)*0.018
        periodic=amp*math.sin(2*math.pi*f1*n) + 0.28*amp*math.sin(2*math.pi*f2*n + ch)
        noise_part=0.16*amp*r1 + 0.07*amp*r2
        impulse=amp*(0.8+0.6*abs(r3)) if n%137==0 else 0.0
        # Deliberate demonstration anomaly on Manglia A01 in the latest 2 hours.
        # This is synthetic test data so the alert workflow can be verified end-to-end.
        if sensor['id']=='A01' and seed==zlib.crc32('PRJ-001'.encode())%10000 and ts >= datetime.now().replace(microsecond=0)-timedelta(hours=2):
            periodic *= 5.0; noise_part *= 3.0
        return periodic + noise_part + impulse
    return 0.0

def generate(pid,start=None,end=None,overwrite=False):
    p=project(pid); out=path(pid)
    if out.exists() and not overwrite: return out
    end=end or datetime.now().replace(microsecond=0)
    start=start or (end-timedelta(days=p['history_days']))
    step=p['sampling_seconds']; seed=zlib.crc32(pid.encode())%10000
    # Example fault windows. These deliberately create offline sensors for testing.
    fault_sensor='V02' if pid=='PRJ-002' else ('D02' if pid=='PRJ-003' else None)
    fault_start=end-timedelta(hours=12) if fault_sensor else None
    fault_end=end-timedelta(hours=8) if fault_sensor else None
    with gzip.open(out,'wt',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['timestamp','project_id','sensor_id','sensor_type','location','unit','value','status'])
        ts=start; n=0
        while ts<=end:
            for s in p['sensors']:
                offline=(s['id']==fault_sensor and fault_start<=ts<=fault_end)
                status='OFFLINE' if offline else 'ONLINE'
                val='' if offline else round(value(s,ts,n,seed),6)
                w.writerow([ts.strftime('%Y-%m-%d %H:%M:%S'),pid,s['id'],s['type'],s['location'],s['unit'],val,status])
            ts+=timedelta(seconds=step); n+=1
    return out

if __name__=='__main__':
    import sys
    print(generate(sys.argv[1] if len(sys.argv)>1 else 'PRJ-001',overwrite=True))
