import {useEffect,useCallback,useState} from 'react';
import {Heart,LayoutDashboard,MessageCircle,Settings,ChevronRight,ShieldCheck,Menu,Database,RefreshCw} from 'lucide-react';
import {Toaster} from 'sonner';
import {api} from './api';
import {Link} from './shared';
import type {State} from './types';
import Dashboard from './Dashboard';
import Twin from './Twin';
import SettingsView from './Settings';
import {MealDialog,ReportDialog} from './dialogs';
export default function App(){
 const [s,setS]=useState<State|null>(null),[error,setError]=useState(''),[path,setPath]=useState(location.pathname+location.search),[menu,setMenu]=useState(false),[modal,setModal]=useState('');
 const view=path.startsWith('/twin')?'twin':path.startsWith('/settings')?'settings':'dashboard';
 const refresh=useCallback(async()=>{try{setS(await api('state'));setError('')}catch(e:any){setError(e.message);throw e}},[]);
 useEffect(()=>{refresh().catch(()=>{});const listener=()=>{setPath(location.pathname+location.search);setMenu(false)};window.addEventListener('popstate',listener);const poll=setInterval(()=>{if(document.visibilityState==='visible')refresh().catch(()=>{})},20000);return()=>{window.removeEventListener('popstate',listener);clearInterval(poll)}},[refresh]);
 useEffect(()=>{document.title=`${view==='dashboard'?'My dashboard':view==='twin'?'My twin':'Settings'} · Twin`},[view]);
 const name=s?.profile.name??'Your journal';
 return <><a className="skip-link" href="#main-content">Skip to content</a>{menu&&<button className="nav-scrim shown" aria-label="Close navigation" onClick={()=>setMenu(false)}/>}
 <aside className={`journal-sidebar ${menu?'shown':''}`} aria-label="Main navigation"><div data-slot="sidebar-inner"><div data-slot="sidebar-header"><Link href="/" className="brand"><span className="brand-mark"><Heart size={23}/></span><span>twin<span className="brand-period">.</span></span></Link><p className="brand-note">Your health, together.</p></div><div data-slot="sidebar-content"><div className="nav-label">YOUR LITTLE CORNER</div><nav className="main-nav">{[['dashboard','/','My dashboard',LayoutDashboard],['twin','/twin','My twin',MessageCircle],['settings','/settings','Connections & settings',Settings]].map(([id,url,label,Icon]:any)=><Link href={url} key={id} className={id===view?'active':''} aria-current={id===view?'page':undefined}><Icon size={19}/><span>{label}</span>{id==='twin'&&!!s?.nudges.length&&<b className="nav-count">{s.nudges.length}</b>}</Link>)}</nav><div className="sidebar-note stamp"><img src="/art/journal.png" alt="An avocado enjoying a quiet moment with a book and a cat"/><p>A little care.<br/>A little every day.</p><Heart size={17}/></div></div><div data-slot="sidebar-footer"><div className="demo-label"><Database size={15}/> Your connected journal</div><p className="sidebar-fine">Saved by your Python backend.</p><Link href="/settings?tab=profile" className="profile-link"><span className="avatar">{name.charAt(0)}</span><span><b>{name}</b><small>My health journal</small></span><ChevronRight size={17}/></Link></div></div></aside>
 <main className="workspace" id="main-content"><header className="topbar"><div><button className="mobile-menu" onClick={()=>setMenu(!menu)} aria-label="Open navigation" aria-expanded={menu}><Menu size={19}/></button><span className="breadcrumb">My journal <span>/</span> {view==='dashboard'?'Dashboard':view==='twin'?'My twin':'Settings'}</span></div><div className="topbar-right"><span className="private-note"><ShieldCheck size={15}/> Just for you</span><span className="date">{new Date().toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric',year:'numeric'}).toUpperCase()}</span></div></header><div className="page-content">
 {error&&<div className="error-banner" role="alert">{error} <button onClick={()=>refresh().catch(()=>{})}>Try again <RefreshCw size={14}/></button></div>}
 {!s?<div className="loading-panel"><Heart size={30}/><h1>{error?'Your journal is waiting.':'Opening your little journal…'}</h1><p>{error?'Start the Python backend to load your saved data.':'Bringing your health data together.'}</p></div>:view==='dashboard'?<Dashboard s={s} refresh={refresh} openMeal={()=>setModal('meal')}/>:view==='twin'?<Twin s={s} refresh={refresh} key={path}/>:<SettingsView s={s} refresh={refresh} openReport={()=>setModal('report')} key={path}/>}
 <footer className="page-footer"><span>Small steps. More you. <Heart size={13}/></span><span>Source modes are labeled · Wellness information, not medical advice.</span></footer></div></main>
 {s&&<><MealDialog open={modal==='meal'} onClose={()=>setModal('')} s={s} refresh={refresh}/><ReportDialog open={modal==='report'} onClose={()=>setModal('')} s={s} refresh={refresh}/></>}<Toaster position="bottom-right" richColors/></>
}
