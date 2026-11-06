const fs=require("fs"),path=require("path"),vm=require("vm"),assert=require("assert/strict");
const source=fs.readFileSync(path.join(__dirname,"..","app.js"),"utf8");
new vm.Script(source);
const block=source.slice(source.indexOf("let reportHistoryLoadId ="),source.indexOf("// 渲染历史会话的对话记录"));
const notice={hidden:true,textContent:"",setAttribute(){}};
const list={setAttribute(key,value){this[key]=value;}};
const nodes={reportHistoryLoading:notice,reportHistoryList:list};
const requests=[],timers=new Map();let timerId=0;
const rendered=[];
const context=vm.createContext({
 document:{getElementById:id=>nodes[id]||null,querySelectorAll:()=>[]},
 AbortController,encodeURIComponent,
 reportConversationId:"previous",currentReportSession:{report:{}},
 fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject})),
 setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),
 updateReportDisplay:session=>rendered.push(session.id),renderQuickTags(){},updateReportStatus(){},
 setReportElapsed(){},renderHistoryMessages(){},console:{error(){}},
});
vm.runInContext(block,context);
const response=id=>({ok:true,json:async()=>({session:{id,report:{},route_name:id}})});
(async()=>{
 const a=context.openHistoryReport("a");
 assert.equal(notice.hidden,false);assert.equal(list["aria-busy"],"true");
 const b=context.openHistoryReport("b");
 assert.equal(requests[0].options.signal.aborted,true);
 requests[1].resolve(response("b"));await b;
 requests[0].resolve(response("a"));await a;
 assert.equal(context.reportConversationId,"b");assert.deepEqual(rendered,["b"]);
 assert.equal(notice.hidden,true);assert.equal(timers.size,0);
 const failure=context.openHistoryReport("missing");requests[2].resolve({ok:false,status:404});await failure;
 assert.equal(context.reportConversationId,"b");assert.match(notice.textContent,/加载失败/);
 const timeout=context.openHistoryReport("slow");
 [...timers.values()][0]();
 requests[3].reject(Object.assign(new Error("aborted"),{name:"AbortError"}));await timeout;
 assert.match(notice.textContent,/超时/);assert.equal(timers.size,0);
 const late=context.openHistoryReport("late");context.cancelHistoryReportLoad();context.reportConversationId="new";
 requests[4].resolve(response("late"));await late;
 assert.equal(context.reportConversationId,"new");assert.deepEqual(rendered,["b"]);
 assert.equal(requests.length,5);assert.equal(timers.size,0);
 console.log("History progress, latest-selection, timeout, error and new-report cancellation passed.");
})().catch(error=>{console.error(error);process.exitCode=1;});
