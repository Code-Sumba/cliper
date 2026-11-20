"""Build the dashboard website into _site/ (served by GitHub Pages).

    python -m cliper.site            # writes _site/index.html

One static file with the data embedded - no server, nothing to keep running.
Buttons open pre-filled GitHub forms / workflow pages, so every change still
goes through the repo (and only the owner can make it).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from .config import ROOT, load_creators
from .discover import leaderboard, load_db
from .queue import list_items

OUT = ROOT / "_site"


def repo_slug() -> str:
    slug = os.environ.get("GITHUB_REPOSITORY", "")
    if slug:
        return slug
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
        m = re.search(r"github\.com[:/](.+?)(\.git)?$", url)
        return m.group(1) if m else ""
    except OSError:
        return ""


def creator_form(repo: str, action: str, platform: str = "", channel: str = "") -> str:
    q = {"template": "creator.yml", "title": f"creator: {action.lower()} {channel}".strip(),
         "action": action, "channel": channel, "permission": "none"}
    if platform:
        q["platform"] = platform
    return f"https://github.com/{repo}/issues/new?{urlencode(q)}"


def build() -> Path:
    repo = repo_slug()
    creators = load_creators()
    watching = {f"twitch:{c.twitch.lower()}": c for c in creators if c.twitch}
    watching |= {f"youtube:{c.youtube_channel_id}": c for c in creators if c.youtube_channel_id}

    board = []
    for r in leaderboard(limit=60):
        c = watching.get(r["key"])
        r["status"] = ("auto" if c.source == "auto" else "manual") if c else ("brand" if r["brand"] else "")
        r["add_url"] = creator_form(repo, "Add", r["platform"], r["id"])
        r["block_url"] = creator_form(repo, "Block from auto-discovery", r["platform"], r["id"])
        board.append(r)

    week_ago = time.time() - 7 * 86400
    items = list_items()
    queue = [{
        "id": i["id"], "creator": i.get("creator", ""), "status": i.get("status", ""),
        "score": i.get("score", 0), "chat": i.get("chat_sample", "")[:140],
        "title": (i.get("meta") or {}).get("title", ""), "hook": (i.get("meta") or {}).get("hook", ""),
        "meta_source": (i.get("meta") or {}).get("source", ""),
        "run_url": f"https://github.com/{repo}/actions/runs/{i['run_id']}" if i.get("run_id") else "",
        "created": i.get("created_at", 0),
    } for i in items if i.get("status") in {"pending", "approved"}
        or (i.get("status") == "published" and i.get("published_at", 0) >= week_ago)]
    queue.sort(key=lambda q: (q["status"] != "pending", -q["created"]))

    data = {
        "repo": repo,
        "built": int(time.time()),
        "discovery_updated": load_db().get("updated", 0),
        "links": {
            "add": creator_form(repo, "Add"),
            "review": f"https://github.com/{repo}/actions/workflows/review.yml",
            "watch": f"https://github.com/{repo}/actions/workflows/watch.yml",
            "publish": f"https://github.com/{repo}/actions/workflows/publish.yml",
            "actions": f"https://github.com/{repo}/actions",
        },
        "creators": [{
            "handle": c.handle, "name": c.display_name, "source": c.source,
            "permission": c.permission, "per_week": c.post_per_week,
            "platforms": c.platforms or ["all"],
            "where": "twitch" if c.twitch else "youtube" if c.youtube_channel_id else "-",
            "url": f"https://www.twitch.tv/{c.twitch}" if c.twitch else
                   f"https://www.youtube.com/channel/{c.youtube_channel_id}" if c.youtube_channel_id else "",
            "remove_url": creator_form(repo, "Remove", "", c.handle),
            "note": c.permission_note[:200],
        } for c in creators],
        "board": board,
        "queue": queue,
        "counts": {
            "pending": sum(1 for i in items if i.get("status") == "pending"),
            "approved": sum(1 for i in items if i.get("status") == "approved"),
            "published_7d": sum(1 for i in items if i.get("status") == "published"
                                and i.get("published_at", 0) >= week_ago),
        },
    }
    OUT.mkdir(exist_ok=True)
    # "</" must not appear raw inside <script>, or a stream title could end it.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (OUT / "index.html").write_text(PAGE.replace("__DATA__", blob), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    return OUT / "index.html"


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Cliper</title>
<style>
:root{--bg:#0d0f14;--panel:#151922;--line:#232a36;--text:#e7ebf2;--mute:#8b94a5;--acc:#7c5cff;
--tw:#9146ff;--yt:#ff3d3d;--ok:#2fbf71;--warn:#f5a524}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:inherit}header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;
justify-content:space-between;padding:18px 22px;border-bottom:1px solid var(--line);position:sticky;top:0;
background:rgba(13,15,20,.92);backdrop-filter:blur(8px);z-index:2}
h1{margin:0;font-size:20px;letter-spacing:.3px}h1 span{color:var(--acc)}
.sub{color:var(--mute);font-size:12px}main{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
.btn{display:inline-block;padding:8px 14px;border-radius:9px;border:1px solid var(--line);
background:var(--panel);text-decoration:none;font-weight:600;font-size:13px;white-space:nowrap}
.btn.primary{background:var(--acc);border-color:var(--acc);color:#fff}.btn.sm{padding:4px 9px;font-size:12px}
.btn:hover{filter:brightness(1.15)}.row{display:flex;gap:8px;flex-wrap:wrap}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:4px 0 22px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.tile b{display:block;font-size:26px}.tile span{color:var(--mute);font-size:12px}
section{background:var(--panel);border:1px solid var(--line);border-radius:14px;margin-bottom:22px;overflow:hidden}
section>h2{margin:0;padding:14px 16px;font-size:15px;border-bottom:1px solid var(--line);
display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.tabs button{background:none;border:1px solid var(--line);color:var(--mute);border-radius:7px;
padding:4px 10px;cursor:pointer;font-size:12px}.tabs button.on{color:var(--text);border-color:var(--acc)}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}
th{color:var(--mute);font-weight:600;font-size:12px;white-space:nowrap}tr:last-child td{border-bottom:0}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pf{font-size:11px;font-weight:700;padding:2px 7px;border-radius:5px;color:#fff}
.pf.twitch{background:var(--tw)}.pf.youtube{background:var(--yt)}
.live{color:#ff5a5a;font-weight:700;font-size:12px;white-space:nowrap}
.chip{font-size:11px;padding:2px 8px;border-radius:20px;border:1px solid var(--line);color:var(--mute);white-space:nowrap}
.chip.ok{color:var(--ok);border-color:var(--ok)}.chip.warn{color:var(--warn);border-color:var(--warn)}
.chip.acc{color:#b9a8ff;border-color:var(--acc)}
.name a{font-weight:600;text-decoration:none}.small{color:var(--mute);font-size:12px;max-width:340px;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.up{color:var(--ok)}.down{color:#ff6b6b}
.empty{padding:22px 16px;color:var(--mute)}.help{color:var(--mute);font-size:12px;padding:10px 16px 14px}
</style>
</head>
<body>
<header>
  <div><h1>Cliper <span>●</span></h1><div class="sub" id="upd"></div></div>
  <div class="row" id="topbtns"></div>
</header>
<main>
  <div class="tiles" id="tiles"></div>

  <section>
    <h2>Clip queue <span class="row" id="qbtns"></span></h2>
    <div class="scroll"><table id="queue"></table></div>
    <div class="help">Watch a clip: open its run and download the <b>clips</b> artifact. Approve: <b>Review</b> → Run workflow → paste the clip id.</div>
  </section>

  <section>
    <h2>Top streams right now &amp; last 24h
      <span class="tabs" id="tabs"></span></h2>
    <div class="scroll"><table id="board"></table></div>
    <div class="help">Hours watched = viewers × hours live over the last 24h (sampled every 2h). The top creators are added automatically each day as <b>review-only</b>. Brand / esports channels are skipped.</div>
  </section>

  <section>
    <h2>Creators being watched</h2>
    <div class="scroll"><table id="creators"></table></div>
  </section>
</main>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
function el(tag, attrs, ...kids){const e=document.createElement(tag);
  for(const [k,v] of Object.entries(attrs||{})){ if(k==='class') e.className=v; else if(k==='text') e.textContent=v;
    else e.setAttribute(k,v);} for(const k of kids){ if(k!=null) e.append(k);} return e;}
const fmt = n => (n||0).toLocaleString('en-US');
const ago = t => { if(!t) return 'never'; const m=Math.round((Date.now()/1000-t)/60);
  return m<60? m+' min ago' : m<1440? Math.round(m/60)+' h ago' : Math.round(m/1440)+' d ago'; };
const link = (text, href, cls) => el('a', {href, target:'_blank', rel:'noopener', class:cls||'', text});
const pf = p => el('span', {class:'pf '+p, text: p==='twitch'?'Twitch':p==='youtube'?'YouTube':p});

$('#upd').textContent = `Updated ${ago(D.built)} · discovery ${ago(D.discovery_updated)}`;
$('#topbtns').append(link('+ Add creator', D.links.add, 'btn primary'), link('Review clips', D.links.review, 'btn'),
  link('Check streams now', D.links.watch, 'btn'), link('All runs', D.links.actions, 'btn'));
$('#qbtns').append(link('Review', D.links.review, 'btn sm'), link('Publish now', D.links.publish, 'btn sm'));

const tiles = [['Creators watched', D.creators.length, `${D.creators.filter(c=>c.source==='auto').length} auto-discovered`],
  ['Clips waiting', D.counts.pending, 'need your approval'], ['Approved', D.counts.approved, 'post on next publish'],
  ['Posted (7 days)', D.counts.published_7d, 'across all platforms'],
  ['Live now (top list)', D.board.filter(r=>r.live_now).length, 'streams being sampled']];
for(const [t,n,s] of tiles) $('#tiles').append(el('div',{class:'tile'}, el('span',{text:t}), el('b',{text:fmt(n)}), el('span',{text:s})));

// queue
(function(){ const t=$('#queue');
  if(!D.queue.length){ t.replaceWith(el('div',{class:'empty',text:'No clips yet - they appear after the next harvest.'})); return; }
  t.append(el('tr',{}, ...['Status','Clip','Creator','Score','Chat at that moment',''].map(h=>el('th',{text:h}))));
  for(const q of D.queue){
    const st = q.status==='pending'?'warn':q.status==='approved'?'acc':'ok';
    t.append(el('tr',{}, el('td',{}, el('span',{class:'chip '+st,text:q.status})),
      el('td',{}, el('div',{class:'name',text:q.title||q.hook}), el('div',{class:'small',text:q.id+(q.meta_source==='llm'?' · AI text':'')})),
      el('td',{text:q.creator}), el('td',{class:'n',text:fmt(Math.round(q.score))}),
      el('td',{}, el('div',{class:'small',text:q.chat})),
      el('td',{}, q.run_url? link('Watch', q.run_url, 'btn sm') : '')));
  }})();

// leaderboard
let filter='all';
function drawBoard(){ const t=$('#board'); t.textContent='';
  const rows = D.board.filter(r=>filter==='all'||r.platform===filter);
  if(!rows.length){ t.append(el('tr',{}, el('td',{class:'empty',text:'No data yet - filled in by the next watch run.'}))); return; }
  t.append(el('tr',{}, ...['#','Creator','','Live now','Hours watched 24h','vs prev 24h','Avg','Peak','Days live (3)',''].map(h=>el('th',{text:h}))));
  rows.forEach((r,i)=>{
    const prev=r.hours_watched_prev_24h, delta = prev? Math.round((r.hours_watched_24h-prev)/prev*100) : null;
    let action;
    if(r.status==='manual') action=el('span',{class:'chip ok',text:'watching'});
    else if(r.status==='auto') action=el('span',{class:'row'}, el('span',{class:'chip acc',text:'auto'}), link('Block', r.block_url,'btn sm'));
    else if(r.status==='brand') action=el('span',{class:'chip',text:'brand / esports'});
    else action=el('span',{class:'row'}, link('Add', r.add_url,'btn sm primary'), link('Block', r.block_url,'btn sm'));
    t.append(el('tr',{}, el('td',{class:'n',text:i+1}),
      el('td',{class:'name'}, link(r.name, r.url), el('div',{class:'small',text:[r.game,r.title].filter(Boolean).join(' · ')})),
      el('td',{}, pf(r.platform)),
      el('td',{class:'n'}, r.live_now? el('span',{class:'live',text:'● '+fmt(r.live_now)}) : '–'),
      el('td',{class:'n',text:fmt(r.hours_watched_24h)}),
      el('td',{class:'n'}, delta==null? '–' : el('span',{class:delta>=0?'up':'down',text:(delta>=0?'+':'')+delta+'%'})),
      el('td',{class:'n',text:fmt(r.avg_viewers_24h)}), el('td',{class:'n',text:fmt(r.peak_24h)}),
      el('td',{class:'n',text:r.days_live_3d}), el('td',{},action)));
  });}
for(const [k,l] of [['all','All'],['twitch','Twitch'],['youtube','YouTube']]){
  const b=el('button',{class:k===filter?'on':'',text:l}); b.onclick=()=>{filter=k;
    document.querySelectorAll('#tabs button').forEach(x=>x.classList.remove('on')); b.classList.add('on'); drawBoard();};
  $('#tabs').append(b);}
drawBoard();

// creators
(function(){ const t=$('#creators');
  t.append(el('tr',{}, ...['Creator','Source','Permission','Per week','Posts to',''].map(h=>el('th',{text:h}))));
  for(const c of D.creators){
    const perm = c.permission==='none'?'chip warn':'chip ok';
    t.append(el('tr',{}, el('td',{class:'name'}, c.url? link(c.name,c.url):c.name, ' ', c.where!=='-'?pf(c.where):''),
      el('td',{}, el('span',{class:c.source==='auto'?'chip acc':'chip',text:c.source})),
      el('td',{}, el('span',{class:perm,text:c.permission==='none'?'review only':c.permission})),
      el('td',{class:'n',text:c.per_week}), el('td',{class:'small',text:c.platforms.join(', ')}),
      el('td',{}, link('Remove', c.remove_url, 'btn sm'))));
  }})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print(build())
