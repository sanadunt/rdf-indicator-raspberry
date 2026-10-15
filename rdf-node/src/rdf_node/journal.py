from __future__ import annotations
import json
import sqlite3
import threading
from pathlib import Path
from .util import compact, digest, now_ms

TERMINAL={'APPLIED','FAILED','REJECTED','EXPIRED','CONFLICT','PERSISTED_UNVERIFIED','OUTCOME_UNKNOWN','CANCELLED'}
class Journal:
    def __init__(self, path:Path):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(path,check_same_thread=False,timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, hash TEXT NOT NULL, request TEXT NOT NULL, actor TEXT NOT NULL, stage TEXT NOT NULL, result TEXT NOT NULL, updated INTEGER NOT NULL)')
        # Additive columns for existing Pi databases: where a command came from and what it changed.
        columns={row[1] for row in self.db.execute('PRAGMA table_info(operations)')}
        if 'link' not in columns: self.db.execute('ALTER TABLE operations ADD COLUMN link TEXT')
        if 'meta' not in columns: self.db.execute("ALTER TABLE operations ADD COLUMN meta TEXT NOT NULL DEFAULT '{}'")
        self.db.commit()
    def close(self):
        with self.lock: self.db.close()
    def get(self,key,default=None):
        with self.lock:
            r=self.db.execute('SELECT v FROM kv WHERE k=?',(key,)).fetchone()
            return json.loads(r[0]) if r else default
    def set(self,key,value):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',(key,compact(value).decode()))
    def config_revision(self,h):
        with self.lock,self.db:
            old=self.get('config',{})
            if old.get('digest')==h: return old['rev']
            rev=old.get('rev',0)+1
            self.set('config',{'rev':rev,'digest':h})
            return rev
    def lookup(self,id):
        with self.lock:
            r=self.db.execute('SELECT hash, request, actor, stage, result, updated, link, meta FROM operations WHERE id=?',(id,)).fetchone()
            if not r: return None
            return dict(id=id,hash=r[0],request=json.loads(r[1]),actor=r[2],stage=r[3],result=json.loads(r[4]),updated_ms=r[5],
                        link=r[6],meta=json.loads(r[7] or '{}'))
    def accept(self,request,actor,link=None,meta=None):
        with self.lock,self.db:
            old=self.lookup(request['id'])
            h=digest(request)
            if old:
                if old['hash']!=h: raise ValueError('COMMAND_ID_CONFLICT')
                return False,old
            self.db.execute('INSERT INTO operations (id,hash,request,actor,stage,result,updated,link,meta) VALUES (?,?,?,?,?,?,?,?,?)',
                (request['id'],h,compact(request).decode(),actor,'ACCEPTED','{}',now_ms(),link,compact(meta or {}).decode()))
            return True,self.lookup(request['id'])
    def update(self,id,stage,result=None):
        with self.lock,self.db:
            self.db.execute('UPDATE operations SET stage=?,result=?,updated=? WHERE id=?',
                (stage,compact(result or {}).decode(),now_ms(),id))
        return self.lookup(id)
    def latest(self,n=20,skip_ops=(),links=None):
        """Newest operations first; skip_ops drops ops by name, links keeps only those origins."""
        columns='SELECT id, hash, request, actor, stage, result, updated, link, meta FROM operations'
        where=[];params=[]
        if links:
            where.append(f"link IN ({','.join('?'*len(links))})");params.extend(links)
        with self.lock:
            if skip_ops:
                try:
                    clause=f"coalesce(json_extract(request,'$.op'),'') NOT IN ({','.join('?'*len(skip_ops))})"
                    sql=columns+' WHERE '+' AND '.join(where+[clause])+' ORDER BY updated DESC LIMIT ?'
                    rows=self.db.execute(sql,(*params,*skip_ops,n)).fetchall()
                except sqlite3.OperationalError:
                    # SQLite built without JSON1: filter in Python.
                    sql=columns+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY updated DESC'
                    rows=[]
                    for r in self.db.execute(sql,params):
                        if json.loads(r[2]).get('op') not in skip_ops: rows.append(r)
                        if len(rows)>=n: break
            else:
                sql=columns+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY updated DESC LIMIT ?'
                rows=self.db.execute(sql,(*params,n)).fetchall()
            return [dict(id=r[0],hash=r[1],request=json.loads(r[2]),actor=r[3],stage=r[4],result=json.loads(r[5]),
                         updated_ms=r[6],link=r[7],meta=json.loads(r[8] or '{}')) for r in rows]
    def pending_shutdowns(self):
        failures={'FAILED','REJECTED','EXPIRED','CONFLICT','CANCELLED'}
        with self.lock:
            rows=self.db.execute('SELECT id,request,stage FROM operations').fetchall()
        return [{'id':id,'stage':stage} for id,request,stage in rows
                if stage not in failures and json.loads(request).get('op')=='system.shutdown.execute']
    def pending_ppp_restarts(self):
        terminal={'APPLIED','FAILED','REJECTED','EXPIRED','CONFLICT','CANCELLED','PERSISTED_UNVERIFIED'}
        with self.lock:
            rows=self.db.execute('SELECT id,request,stage,result FROM operations').fetchall()
        pending=[]
        for id,raw_request,stage,raw_result in rows:
            request=json.loads(raw_request); result=json.loads(raw_result)
            if request.get('op')!='ppp.restart' or stage in terminal: continue
            if stage=='OUTCOME_UNKNOWN' and result.get('confirmed_by'): continue
            pending.append(dict(id=id,request=request,stage=stage,result=result))
        return pending
    def recover(self,boot):
        recovered=[]
        with self.lock:
            rows=self.db.execute('SELECT id,stage,request,result FROM operations').fetchall()
            for id,stage,r,rs in rows:
                if stage in TERMINAL: continue
                req=json.loads(r); result=json.loads(rs)
                if stage=='REBOOT_SCHEDULED' and req.get('boot')!=boot:
                    recovered.append(self.update(id,'APPLIED',{'proof':'OS_BOOT_ID_CHANGED','previous_boot':req.get('boot'),'boot':boot}))
                else:
                    recovered.append(self.update(id,'OUTCOME_UNKNOWN',{'reason':'AGENT_RESTARTED_RECONCILE_NO_REPLAY'}))
        return recovered
    def prune(self,max_rows=2000,days=30):
        cutoff=now_ms()-days*86400000
        with self.lock,self.db:
            terminal=tuple(TERMINAL-{'OUTCOME_UNKNOWN'})
            marks=','.join('?' for _ in terminal)
            self.db.execute(f'DELETE FROM operations WHERE stage IN ({marks}) AND updated<?',(*terminal,cutoff))
            self.db.execute(f'DELETE FROM operations WHERE id IN (SELECT id FROM operations WHERE stage IN ({marks}) ORDER BY updated DESC LIMIT -1 OFFSET ?)',(*terminal,max_rows))
