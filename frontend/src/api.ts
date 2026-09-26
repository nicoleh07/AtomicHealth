export async function api(path:string,body?:unknown,method?:string){
 const response=await fetch('/api/'+path,{method:method??(body!==undefined?'POST':'GET'),headers:body!==undefined?{'Content-Type':'application/json'}:undefined,body:body!==undefined?JSON.stringify(body):undefined});
 let data:any;try{data=await response.json()}catch{throw Error('The Python backend is unavailable. Start the backend and try again.')}
 if(!response.ok){const detail=data.detail??data.error;throw Error(typeof detail==='string'?detail:Array.isArray(detail)?detail.map(x=>`${x.loc?.slice(1).join(' ')}: ${x.msg}`).join('; '):'Your change could not be saved. Please try again.');}
 return data;
}
export async function uploadReport(file:File){const form=new FormData();form.append('file',file);const r=await fetch('/api/reports/renpho/extract',{method:'POST',body:form});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'The report could not be uploaded.');return d;}
export async function streamChat(message:string,onEvent:(event:any)=>void,signal?:AbortSignal){
 const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message}),signal});
 if(!response.ok){const d=await response.json();throw Error(typeof d.detail==='string'?d.detail:'The message could not be sent.')}
 const reader=response.body!.getReader(),decoder=new TextDecoder();let buffer='';
 while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const lines=buffer.split('\n');buffer=lines.pop()||'';for(const line of lines){if(line.startsWith('data: ')){const event=JSON.parse(line.slice(6));if(event.error)throw Error(event.error);onEvent(event);}}}
}
