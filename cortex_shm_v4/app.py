from flask import Flask, render_template, request, send_file, redirect, url_for, jsonify
from pathlib import Path
from datetime import datetime
import json, pandas as pd, numpy as np

BASE=Path(__file__).resolve().parent
app=Flask(__name__)
CONFIG=json.loads((BASE/'config'/'projects.json').read_text(encoding='utf-8'))

CODE_SETS={
    'IS Code': {
        'description':'Configurable IS-code monitoring profile for this prototype.',
        'multiplier':1.00,
    },
    'Dutch Norm': {
        'description':'Configurable Dutch-norm monitoring profile for this prototype.',
        'multiplier':0.90,
    },
    'Project Baseline': {
        'description':'Project sensor baseline configured in the project file.',
        'multiplier':1.00,
    },
}

def projects(): return CONFIG['projects']
def project(pid): return next(p for p in projects() if p['id']==pid)
def data_path(pid): return BASE/'data'/f'{pid}.csv.gz'

def ensure_data(pid):
    if not data_path(pid).exists():
        import sys; sys.path.insert(0,str(BASE/'synthetic_data_generators'))
        from generator import generate; generate(pid)

def load_df(pid,start=None,end=None,sensors=None):
    ensure_data(pid)
    df=pd.read_csv(data_path(pid),compression='gzip',parse_dates=['timestamp'])
    if start: df=df[df.timestamp>=pd.to_datetime(start)]
    if end: df=df[df.timestamp<=pd.to_datetime(end)]
    if sensors: df=df[df.sensor_id.isin(sensors)]
    return df


def display_noise(sensor_type, seed_text):
    # Small measurement noise for the live simulator. Historical files remain unchanged.
    seed=abs(hash(seed_text)) % (2**32)
    rng=np.random.default_rng(seed)
    if sensor_type=='Temperature': return float(rng.normal(0,0.06))
    if sensor_type in ('Deflection','LVDT'): return float(rng.normal(0,0.025))
    return float(rng.normal(0,0.06))

def live_df(pid,sensors=None):
    df=load_df(pid)
    end=df.timestamp.max(); start=end-pd.Timedelta(hours=24)
    df=df[(df.timestamp>=start)&(df.timestamp<=end)]
    return df[df.sensor_id.isin(sensors)] if sensors else df

def analysis_window(pid,sid):
    df=live_df(pid,[sid])
    if df.empty: return df
    # Use the latest 10 minutes for live signal processing. This keeps VRMS/FFT responsive.
    end=df.timestamp.max(); start=end-pd.Timedelta(minutes=10)
    g=df[(df.timestamp>=start)&(df.timestamp<=end)].copy()
    if not g.empty:
        g['value']=g.apply(lambda r: r['value'] if pd.isna(r['value']) else float(r['value'])+display_noise(r['sensor_type'], f"{sid}-{r['timestamp']}"), axis=1)
    return g[g.value.notna()]

def threshold_for(pid, sid, code):
    p=project(pid); meta=next(s for s in p['sensors'] if s['id']==sid)
    base=float(meta.get('limit', 1.0))
    # These are configurable prototype profiles, not declarations of code compliance.
    if code=='IS Code':
        return base
    if code=='Dutch Norm':
        return base*0.90
    return base

def evaluate_sensor(pid,sid,code='IS Code'):
    p=project(pid); meta=next(s for s in p['sensors'] if s['id']==sid)
    df=analysis_window(pid,sid)
    if df.empty:
        return {'sensor_id':sid,'type':meta['type'],'unit':meta['unit'],'status':'OFFLINE','value':None,'threshold':threshold_for(pid,sid,code),'message':'Sensor offline or no valid readings in the analysis window.','code':code}
    vals=df.value.to_numpy(float)
    st=meta['type']
    if st in ('Vibration','Acceleration'):
        vrms=float(np.sqrt(np.mean(vals**2)))
        value=vrms
        threshold=threshold_for(pid,sid,code)
        status='ALERT' if vrms>threshold else 'NORMAL'
        message=f'VRMS {vrms:.4f} {meta["unit"]} vs threshold {threshold:.4f} {meta["unit"]}.'
    elif st in ('Deflection','LVDT'):
        value=float(np.max(np.abs(vals)))
        threshold=threshold_for(pid,sid,code)
        status='ALERT' if value>threshold else 'NORMAL'
        message=f'Peak displacement {value:.4f} {meta["unit"]} vs threshold {threshold:.4f} {meta["unit"]}.'
    else:
        value=float(vals[-1])
        threshold=threshold_for(pid,sid,code)
        status='NORMAL'
        message=f'Latest temperature {value:.3f} {meta["unit"]}. Temperature is shown for trend monitoring.'
    return {'sensor_id':sid,'type':st,'unit':meta['unit'],'status':status,'value':value,'threshold':threshold,'message':message,'code':code,'timestamp':df.timestamp.max().isoformat()}

@app.route('/')
def home(): return redirect('/dashboard')

@app.route('/dashboard')
def dashboard():
    ps=projects(); pid=request.args.get('project_id',ps[0]['id']); p=project(pid)
    return render_template('dashboard.html',title='Dashboard',projects=ps,project=p,sensors=p['sensors'])

@app.route('/<section>')
def scope(section):
    if section.lower() in {'devices','sensors','mqtt','projects','structures','users','audit','settings'}:
        return render_template('not_scope.html',title=section.title(),active=section.title())
    return redirect('/dashboard')

@app.route('/data-download',methods=['GET','POST'])
def data_download():
    ps=projects(); pid=request.values.get('project_id',ps[0]['id']); p=project(pid)
    if request.method=='POST':
        start=request.form.get('start') or None; end=request.form.get('end') or None; ss=request.form.getlist('sensors')
        df=load_df(pid,start,end,ss)
        name=f'{pid}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'; out=BASE/'outputs'/'excel'/name
        with pd.ExcelWriter(out,engine='openpyxl') as w:
            pd.DataFrame([['Project',p['name']],['Site',p['site']],['Structure',p['structure']],['Records',len(df)],['Start',df.timestamp.min() if len(df) else ''],['End',df.timestamp.max() if len(df) else '']],columns=['Parameter','Value']).to_excel(w,index=False,sheet_name='Project Information')
            pd.DataFrame(p['sensors']).to_excel(w,index=False,sheet_name='Sensor Mapping')
            df.to_excel(w,index=False,sheet_name='All Sensor Data')
            for sid,sdf in df.groupby('sensor_id'): sdf.to_excel(w,index=False,sheet_name=str(sid)[:31])
        return render_template('excel_preview.html',title='Data Download',filename=name,rows=len(df),start=df.timestamp.min() if len(df) else '',end=df.timestamp.max() if len(df) else '')
    return render_template('data_download.html',title='Data Download',projects=ps,project=p)

@app.route('/download-excel/<name>')
def download_excel(name): return send_file(BASE/'outputs'/'excel'/name,as_attachment=True)

@app.route('/reports')
def reports():
    ps=projects(); pid=request.args.get('project_id',ps[0]['id']); p=project(pid)
    return render_template('reports.html',title='Reports',projects=ps,project=p)

@app.route('/report-preview',methods=['POST'])
def report_preview():
    pid=request.form['project_id']; p=project(pid); start=request.form.get('start') or None; end=request.form.get('end') or None
    df=load_df(pid,start,end); s=df[df.value.notna()].groupby(['sensor_id','sensor_type','unit']).value.agg(['count','min','max','mean']).reset_index()
    return render_template('report_preview.html',title='Report Preview',project=p,summary=s.to_dict('records'),start=start or str(df.timestamp.min()),end=end or str(df.timestamp.max()))

@app.route('/report-docx',methods=['POST'])
def report_docx():
    from docx import Document
    pid=request.form['project_id']; p=project(pid); df=load_df(pid,request.form.get('start') or None,request.form.get('end') or None)
    s=df[df.value.notna()].groupby(['sensor_id','sensor_type','unit']).value.agg(['count','min','max','mean']).reset_index()
    doc=Document(); doc.add_heading('Structural Health Monitoring Report',0); doc.add_paragraph(p['name']); doc.add_paragraph(f"{p['site']} | {p['structure']}")
    doc.add_heading('Sensor Summary',1); t=doc.add_table(rows=1,cols=6); t.style='Table Grid'
    for i,h in enumerate(['Sensor','Type','Unit','Samples','Min','Max']): t.rows[0].cells[i].text=h
    for _,r in s.iterrows():
        c=t.add_row().cells
        for i,v in enumerate([r.sensor_id,r.sensor_type,r.unit,int(r['count']),f"{r['min']:.3f}",f"{r['max']:.3f}"]): c[i].text=str(v)
    out=BASE/'outputs'/'reports'/f'{pid}_report.docx'; doc.save(out)
    return send_file(out,as_attachment=True)

@app.route('/analyse')
def analyse():
    ps=projects(); pid=request.args.get('project_id',ps[0]['id']); p=project(pid); ss=request.args.getlist('sensors')
    if not ss: ss=[s['id'] for s in p['sensors']]
    rule=request.args.get('rule','IS Code')
    return render_template('analyse.html',title='Analysis',projects=ps,project=p,sensors=p['sensors'],selected=ss,code_sets=list(CODE_SETS),selected_rule=rule)

@app.route('/alerts')
def alerts():
    ps=projects(); pid=request.args.get('project_id',ps[0]['id']); p=project(pid); code=request.args.get('rule','IS Code')
    results=[evaluate_sensor(pid,s['id'],code) for s in p['sensors']]
    active=[r for r in results if r['status']=='ALERT']
    return render_template('alerts.html',title='Alerts',projects=ps,project=p,code_sets=list(CODE_SETS),selected_rule=code,alerts=active)

@app.route('/api/live')
def api_live():
    pid=request.args.get('project_id',projects()[0]['id']); ss=request.args.getlist('sensor'); df=live_df(pid,ss or None)
    result=[]
    for sid,g in df.groupby('sensor_id'):
        tail=g.tail(3600).copy()
        vals=[]
        for _,row in tail.iterrows():
            if pd.isna(row['value']): vals.append(None)
            else: vals.append(float(row['value']) + display_noise(row['sensor_type'], f"{sid}-{row['timestamp']}"))
        tail['value']=vals
        result.append({'sensor_id':sid,'type':g.sensor_type.iloc[0],'unit':g.unit.iloc[0],'location':g.location.iloc[0],'data':tail[['timestamp','value','status']].where(pd.notnull(tail[['timestamp','value','status']]),None).to_dict('records')})
    return jsonify(result)

@app.route('/api/analysis')
def api_analysis():
    pid=request.args.get('project_id'); sid=request.args.get('sensor'); g=analysis_window(pid,sid)
    if g.empty: return jsonify({'error':'No data'})
    x=g.value.to_numpy(float); vrms=float(np.sqrt(np.mean(x*x))); x=x-x.mean(); step=project(pid)['sampling_seconds']
    # Remove DC and apply Hann window for a cleaner engineering spectrum.
    if len(x)>4: x=x*np.hanning(len(x))
    spec=np.abs(np.fft.rfft(x)); freq=np.fft.rfftfreq(len(x),d=step)
    if len(spec)>1:
        inds=np.argsort(spec[1:])[-20:]+1
        fft=[{'frequency':round(float(freq[i]),5),'amplitude':round(float(spec[i]),5)} for i in inds[::-1]]
        peak_i=int(inds[np.argmax(spec[inds])])
        peak_freq=float(freq[peak_i]); peak_amp=float(spec[peak_i])
    else: fft=[]; peak_freq=0.0; peak_amp=0.0
    return jsonify({'vrms':vrms,'fft':fft,'fft_peak_frequency':peak_freq,'fft_peak_amplitude':peak_amp,'timestamp':g.timestamp.max().isoformat(),'samples':len(g)})

@app.route('/api/code-check')
def code_check():
    pid=request.args.get('project_id'); sid=request.args.get('sensor'); rule=request.args.get('rule','IS Code')
    return jsonify(evaluate_sensor(pid,sid,rule))

@app.route('/api/alerts')
def api_alerts():
    pid=request.args.get('project_id',projects()[0]['id']); code=request.args.get('rule','IS Code'); p=project(pid)
    results=[evaluate_sensor(pid,s['id'],code) for s in p['sensors']]
    active=[r for r in results if r['status']=='ALERT']
    return jsonify({'count':len(active),'project_id':pid,'rule':code,'alerts':active})

if __name__=='__main__': app.run(debug=True)
